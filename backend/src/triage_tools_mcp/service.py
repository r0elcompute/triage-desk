import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, TypeVar
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Connection, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from triage_sentinel.data.enums import (
    AuditEntityType,
    QueueEventType,
    ToolExecutionStatus,
)
from triage_sentinel.data.models import (
    AssessmentVersion,
    ClinicianApproval,
    ClinicianApprovalRevocation,
    ClinicianReviewRequest,
    QueueCommit,
    ToolExecution,
    TriageEncounter,
    VitalObservation,
)
from triage_sentinel.data.repositories import EventRepository
from triage_sentinel.data.schemas import (
    AuditLogCreate,
    QueueEventCreate,
    VitalObservationCreate,
)
from triage_sentinel.evaluation.iitt import IittDeterministicEvaluator
from triage_tools_mcp.schemas import (
    AuthenticatedClinician,
    CommitApprovedQueueUpdateInput,
    CommitApprovedQueueUpdateOutput,
    EvaluateTriageInput,
    EvaluateTriageOutput,
    RequestClinicianReviewInput,
    RequestClinicianReviewOutput,
    ToolError,
    ValidateVitalsInput,
    ValidateVitalsOutput,
    ValidationIssue,
)

LOGGER = logging.getLogger(__name__)
SERVICE_ACTOR = "SYN-MCP-SERVICE"
APPROVE_PERMISSION = "triage:assessment:approve"
REVOKE_PERMISSION = "triage:approval:revoke"
COMMIT_PERMISSION = "triage:queue:commit"
ResultT = TypeVar("ResultT")


class ClinicalEvaluator(Protocol):
    def evaluate(self, data: EvaluateTriageInput) -> EvaluateTriageOutput: ...


class PrincipalProvider(Protocol):
    def __call__(self, context: object) -> AuthenticatedClinician: ...


class ClinicalEvaluatorUnavailable(RuntimeError):
    pass


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def __str__(self) -> str:
        return f"{self.code}: {super().__str__()}"


@dataclass(slots=True)
class ReassessmentReviewResult:
    status: Literal["pending", "not_triggered"]
    request_id: UUID | None
    encounter_id: UUID
    assessment_version_id: UUID
    rule_reference: str | None
    reason: str | None = None
    created_at: datetime | None = None


class TriageToolsService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        evaluator: ClinicalEvaluator | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._evaluator = evaluator or IittDeterministicEvaluator()

    def request_reassessment_review(
        self,
        encounter_id: UUID,
        observation: VitalObservationCreate,
        *,
        rule_reference: str | None = None,
    ) -> ReassessmentReviewResult:
        try:
            if not isinstance(observation, VitalObservationCreate):
                observation = VitalObservationCreate.model_validate(observation)
        except ValidationError as error:
            raise ToolFailure(
                "INVALID_OBSERVATION",
                "Observation payload is invalid and cannot be evaluated",
            ) from error

        if observation.source_reference is None or not observation.source_reference.startswith(
            "SYN-"
        ):
            raise ToolFailure(
                "INVALID_OBSERVATION",
                "Observation source_reference must use a synthetic identity",
            )

        if rule_reference is None or not rule_reference.strip():
            raise ToolFailure(
                "INVALID_RULE_REFERENCE",
                "A protocol rule_reference is required to assess reassessment triggers",
            )

        sanitized_rule = rule_reference.strip()

        if isinstance(self._session_factory, Connection):
            session = Session(bind=self._session_factory, expire_on_commit=False)
            with session.begin_nested():
                return self._request_reassessment_review_session(
                    session, encounter_id, observation, sanitized_rule
                )

        with self._session_factory.begin() as session:
            return self._request_reassessment_review_session(
                session, encounter_id, observation, sanitized_rule
            )

    def _request_reassessment_review_session(
        self,
        session: Session,
        encounter_id: UUID,
        observation: VitalObservationCreate,
        rule_reference: str,
    ) -> ReassessmentReviewResult:
        if observation.source_reference is None or not observation.source_reference.startswith(
            "SYN-"
        ):
            raise ToolFailure(
                "INVALID_OBSERVATION",
                "Observation source_reference must use a synthetic identity",
            )

        encounter = session.get(TriageEncounter, encounter_id)
        if encounter is None:
            raise ToolFailure("ENCOUNTER_NOT_FOUND", "Encounter was not found")

        assessment = session.scalar(
            select(AssessmentVersion)
            .where(AssessmentVersion.encounter_id == encounter_id)
            .order_by(AssessmentVersion.version.desc())
            .limit(1)
        )
        if assessment is None:
            raise ToolFailure(
                "ASSESSMENT_NOT_FOUND",
                "Encounter does not have an assessment version to reassess",
            )

        history = session.scalars(
            select(VitalObservation)
            .where(VitalObservation.encounter_id == encounter_id)
            .order_by(
                VitalObservation.measured_at.desc(),
                VitalObservation.created_at.desc(),
            )
        ).all()

        for existing in history:
            if (
                existing.measurement == observation.measurement.value
                and existing.value == observation.value
                and existing.unit == observation.unit.value
                and existing.measured_at == observation.measured_at
            ):
                reason = (
                    f"Protocol trigger {rule_reference}: duplicate observation "
                    f"for {observation.measurement.value} at "
                    f"{observation.measured_at.isoformat()}"
                )
                existing_request = session.scalar(
                    select(ClinicianReviewRequest)
                    .where(
                        ClinicianReviewRequest.encounter_id == encounter_id,
                        ClinicianReviewRequest.assessment_version_id == assessment.id,
                        ClinicianReviewRequest.reason.like(f"%{rule_reference}%"),
                        ClinicianReviewRequest.created_at
                        >= datetime.now(UTC) - timedelta(days=7),
                    )
                    .order_by(ClinicianReviewRequest.created_at.desc())
                )
                if existing_request is not None:
                    return ReassessmentReviewResult(
                        status="pending",
                        request_id=existing_request.id,
                        encounter_id=encounter_id,
                        assessment_version_id=assessment.id,
                        rule_reference=rule_reference,
                        reason=existing_request.reason,
                        created_at=existing_request.created_at,
                    )

                request = ClinicianReviewRequest(
                    assessment_version_id=assessment.id,
                    encounter_id=encounter_id,
                    requested_by_reference=SERVICE_ACTOR,
                    reason=reason,
                    created_at=datetime.now(UTC),
                )
                session.add(request)
                session.flush()
                return ReassessmentReviewResult(
                    status="pending",
                    request_id=request.id,
                    encounter_id=encounter_id,
                    assessment_version_id=assessment.id,
                    rule_reference=rule_reference,
                    reason=request.reason,
                    created_at=request.created_at,
                )

        for existing in history:
            if (
                existing.measurement == observation.measurement.value
                and observation.measured_at < existing.measured_at
            ):
                reason = (
                    f"Protocol trigger {rule_reference}: stale observation "
                    f"for {observation.measurement.value} at "
                    f"{observation.measured_at.isoformat()}"
                )
                existing_request = session.scalar(
                    select(ClinicianReviewRequest)
                    .where(
                        ClinicianReviewRequest.encounter_id == encounter_id,
                        ClinicianReviewRequest.assessment_version_id == assessment.id,
                        ClinicianReviewRequest.reason.like(f"%{rule_reference}%"),
                        ClinicianReviewRequest.created_at
                        >= datetime.now(UTC) - timedelta(days=7),
                    )
                    .order_by(ClinicianReviewRequest.created_at.desc())
                )
                if existing_request is not None:
                    return ReassessmentReviewResult(
                        status="pending",
                        request_id=existing_request.id,
                        encounter_id=encounter_id,
                        assessment_version_id=assessment.id,
                        rule_reference=rule_reference,
                        reason=existing_request.reason,
                        created_at=existing_request.created_at,
                    )

                request = ClinicianReviewRequest(
                    assessment_version_id=assessment.id,
                    encounter_id=encounter_id,
                    requested_by_reference=SERVICE_ACTOR,
                    reason=reason,
                    created_at=datetime.now(UTC),
                )
                session.add(request)
                session.flush()
                return ReassessmentReviewResult(
                    status="pending",
                    request_id=request.id,
                    encounter_id=encounter_id,
                    assessment_version_id=assessment.id,
                    rule_reference=rule_reference,
                    reason=request.reason,
                    created_at=request.created_at,
                )

        return ReassessmentReviewResult(
            status="not_triggered",
            request_id=None,
            encounter_id=encounter_id,
            assessment_version_id=assessment.id,
            rule_reference=None,
        )

    def validate_vitals(
        self, data: ValidateVitalsInput
    ) -> ValidateVitalsOutput | ToolError:
        action_id = uuid4()

        def operation(session: Session) -> ValidateVitalsOutput:
            encounter = session.get(TriageEncounter, data.encounter_id)
            if encounter is None:
                raise ToolFailure("ENCOUNTER_NOT_FOUND", "Encounter was not found")

            observations = []
            issues: list[ValidationIssue] = []
            for index, observation in enumerate(data.observations):
                try:
                    observations.append(
                        self._validate_observation(observation.model_dump())
                    )
                except ValidationError as error:
                    issues.extend(
                        ValidationIssue(
                            code=item["type"],
                            path=f"observations.{index}."
                            + ".".join(map(str, item["loc"])),
                            message=item["msg"],
                        )
                        for item in error.errors()
                    )

            actual_measurements = {item.measurement for item in observations}
            missing = sorted(
                set(data.required_measurements) - actual_measurements,
                key=lambda measurement: measurement.value,
            )
            return ValidateVitalsOutput(
                valid=not issues,
                complete=not missing,
                missing_measurements=missing,
                issues=issues,
                validated_at=data.validated_at,
                action_id=action_id,
            )

        return self._execute("validate_vitals", data, action_id, operation)

    def evaluate_triage_rules(
        self, data: EvaluateTriageInput
    ) -> EvaluateTriageOutput | ToolError:
        action_id = uuid4()

        def operation(session: Session) -> EvaluateTriageOutput:
            assessment = session.get(AssessmentVersion, data.assessment_version_id)
            if assessment is None or assessment.encounter_id != data.encounter_id:
                raise ToolFailure(
                    "ASSESSMENT_NOT_FOUND",
                    "Assessment version does not belong to the encounter",
                )
            try:
                result = self._evaluator.evaluate(data)
            except ClinicalEvaluatorUnavailable as error:
                raise ToolFailure("EVALUATOR_UNAVAILABLE", str(error)) from error
            return result.model_copy(
                update={
                    "assessment_version_id": data.assessment_version_id,
                    "action_id": action_id,
                }
            )

        return self._execute(
            "evaluate_triage_rules",
            data,
            action_id,
            operation,
        )

    def request_clinician_review(
        self, data: RequestClinicianReviewInput
    ) -> RequestClinicianReviewOutput | ToolError:
        action_id = uuid4()

        def operation(session: Session) -> RequestClinicianReviewOutput:
            assessment = session.get(AssessmentVersion, data.assessment_version_id)
            if assessment is None:
                raise ToolFailure(
                    "ASSESSMENT_NOT_FOUND", "Assessment version not found"
                )
            request = ClinicianReviewRequest(
                assessment_version_id=assessment.id,
                encounter_id=assessment.encounter_id,
                requested_by_reference=SERVICE_ACTOR,
                reason=data.reason,
                created_at=datetime.now(UTC),
            )
            session.add(request)
            session.flush()
            return RequestClinicianReviewOutput(
                status="pending",
                request_id=request.id,
                assessment_version_id=assessment.id,
                created_at=request.created_at or datetime.now(UTC),
                action_id=action_id,
            )

        return self._execute(
            "request_clinician_review",
            data,
            action_id,
            operation,
        )

    def commit_approved_queue_update(
        self,
        data: CommitApprovedQueueUpdateInput,
        clinician: AuthenticatedClinician | None,
    ) -> CommitApprovedQueueUpdateOutput | ToolError:
        action_id = uuid4()

        def operation(session: Session) -> CommitApprovedQueueUpdateOutput:
            authenticated_clinician = self._require_permission(
                clinician, COMMIT_PERMISSION
            )
            previous = session.scalar(
                select(QueueCommit).where(
                    QueueCommit.idempotency_key == data.idempotency_key
                )
            )
            if previous is not None:
                if (
                    previous.assessment_version_id != data.assessment_version_id
                    or previous.approval_id != data.approval_id
                    or previous.assessment_number != data.assessment_number
                    or previous.queue_name != data.queue_name
                    or previous.committed_by_subject
                    != authenticated_clinician.subject
                    or previous.committed_by_reference
                    != authenticated_clinician.reference
                ):
                    raise ToolFailure(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Idempotency key was already used for a different commit",
                    )
                return CommitApprovedQueueUpdateOutput(
                    status="already_committed",
                    queue_event_id=previous.queue_event_id,
                    commit_id=previous.id,
                    assessment_version_id=previous.assessment_version_id,
                    approval_id=previous.approval_id,
                    action_id=action_id,
                )

            assessment = session.scalar(
                select(AssessmentVersion)
                .where(AssessmentVersion.id == data.assessment_version_id)
                .with_for_update()
            )
            if assessment is None:
                raise ToolFailure(
                    "ASSESSMENT_NOT_FOUND", "Assessment version not found"
                )
            session.scalar(
                select(TriageEncounter)
                .where(TriageEncounter.id == assessment.encounter_id)
                .with_for_update()
            )
            if assessment.version != data.assessment_number:
                raise ToolFailure(
                    "STALE_ASSESSMENT",
                    "Assessment version number does not match the committed record",
                )
            latest_number = session.scalar(
                select(func.max(AssessmentVersion.version)).where(
                    AssessmentVersion.encounter_id == assessment.encounter_id
                )
            )
            if latest_number != assessment.version:
                raise ToolFailure(
                    "STALE_ASSESSMENT",
                    "A newer assessment exists; obtain approval for the latest version",
                )

            approval = session.scalar(
                select(ClinicianApproval)
                .where(ClinicianApproval.id == data.approval_id)
                .with_for_update()
            )
            if approval is None:
                raise ToolFailure("APPROVAL_NOT_FOUND", "Approval was not found")
            if (
                approval.assessment_version_id != assessment.id
                or approval.assessment_number != assessment.version
            ):
                raise ToolFailure(
                    "APPROVAL_MISMATCH",
                    "Approval does not apply to this exact assessment version",
                )
            if approval.clinician_reference != authenticated_clinician.reference:
                raise ToolFailure(
                    "APPROVAL_CLINICIAN_MISMATCH",
                    "Authenticated clinician does not match the approver",
                )
            if approval.clinician_subject != authenticated_clinician.subject:
                raise ToolFailure(
                    "APPROVAL_CLINICIAN_MISMATCH",
                    "Approval belongs to a different authenticated clinician",
                )
            if session.scalar(
                select(ClinicianApprovalRevocation.id).where(
                    ClinicianApprovalRevocation.approval_id == approval.id
                )
            ):
                raise ToolFailure("APPROVAL_REVOKED", "Approval has been revoked")

            committed_at = datetime.now(UTC)
            queue_event = EventRepository(session).add_queue_event(
                assessment.encounter_id,
                QueueEventCreate(
                    queue_name=data.queue_name,
                    event_type=QueueEventType.ENTERED,
                    occurred_at=committed_at,
                    actor_reference=authenticated_clinician.reference,
                ),
            )
            commit = QueueCommit(
                idempotency_key=data.idempotency_key,
                assessment_version_id=assessment.id,
                assessment_number=assessment.version,
                approval_id=approval.id,
                queue_event_id=queue_event.id,
                queue_name=data.queue_name,
                committed_by_subject=authenticated_clinician.subject,
                committed_by_reference=authenticated_clinician.reference,
            )
            session.add(commit)
            session.flush()
            self._add_audit(
                session,
                AuditEntityType.QUEUE_EVENT,
                queue_event.id,
                "queue.update.committed",
                authenticated_clinician.reference,
                committed_at,
            )
            return CommitApprovedQueueUpdateOutput(
                status="committed",
                queue_event_id=queue_event.id,
                commit_id=commit.id,
                assessment_version_id=assessment.id,
                approval_id=approval.id,
                action_id=action_id,
            )

        return self._execute(
            "commit_approved_queue_update",
            data,
            action_id,
            operation,
            actor_reference=(clinician.reference if clinician else SERVICE_ACTOR),
        )

    def approve_review_request(
        self,
        request_id: UUID,
        clinician: AuthenticatedClinician,
        *,
        now: datetime | None = None,
    ) -> UUID:
        self._require_permission(clinician, APPROVE_PERMISSION)
        with self._session_factory.begin() as session:
            request = session.scalar(
                select(ClinicianReviewRequest)
                .where(ClinicianReviewRequest.id == request_id)
                .with_for_update()
            )
            if request is None:
                raise ToolFailure(
                    "REVIEW_REQUEST_NOT_FOUND", "Review request not found"
                )
            session.scalar(
                select(TriageEncounter)
                .where(TriageEncounter.id == request.encounter_id)
                .with_for_update()
            )
            assessment = session.scalar(
                select(AssessmentVersion)
                .where(AssessmentVersion.id == request.assessment_version_id)
                .with_for_update()
            )
            if assessment is None or assessment.encounter_id != request.encounter_id:
                raise ToolFailure(
                    "ASSESSMENT_NOT_FOUND", "Requested assessment not found"
                )
            latest = session.scalar(
                select(func.max(AssessmentVersion.version)).where(
                    AssessmentVersion.encounter_id == assessment.encounter_id
                )
            )
            if latest != assessment.version:
                raise ToolFailure(
                    "STALE_ASSESSMENT",
                    "Cannot approve a superseded assessment version",
                )
            existing_approval = session.scalar(
                select(ClinicianApproval)
                .where(ClinicianApproval.request_id == request.id)
                .with_for_update()
            )
            if existing_approval is not None:
                if (
                    existing_approval.clinician_subject != clinician.subject
                    or existing_approval.clinician_reference != clinician.reference
                ):
                    raise ToolFailure(
                        "ALREADY_APPROVED",
                        "Review request was approved by a different clinician",
                    )
                if session.scalar(
                    select(ClinicianApprovalRevocation.id).where(
                        ClinicianApprovalRevocation.approval_id
                        == existing_approval.id
                    )
                ):
                    raise ToolFailure(
                        "APPROVAL_REVOKED", "Approval has been revoked"
                    )
                return existing_approval.id
            approval = ClinicianApproval(
                request_id=request.id,
                assessment_version_id=assessment.id,
                assessment_number=assessment.version,
                clinician_subject=clinician.subject,
                clinician_reference=clinician.reference,
                created_at=now or datetime.now(UTC),
            )
            session.add(approval)
            session.flush()
            self._add_audit(
                session,
                AuditEntityType.ASSESSMENT_VERSION,
                approval.assessment_version_id,
                "clinician.approval.created",
                clinician.reference,
                now or datetime.now(UTC),
            )
            return approval.id

    def revoke_approval(
        self,
        approval_id: UUID,
        clinician: AuthenticatedClinician,
        *,
        now: datetime | None = None,
    ) -> UUID:
        self._require_permission(clinician, REVOKE_PERMISSION)
        revoked_at = now or datetime.now(UTC)
        with self._session_factory.begin() as session:
            approval = session.scalar(
                select(ClinicianApproval)
                .where(ClinicianApproval.id == approval_id)
                .with_for_update()
            )
            if approval is None:
                raise ToolFailure("APPROVAL_NOT_FOUND", "Approval was not found")
            if session.scalar(
                select(ClinicianApprovalRevocation.id).where(
                    ClinicianApprovalRevocation.approval_id == approval.id
                )
            ):
                raise ToolFailure(
                    "APPROVAL_REVOKED", "Approval has already been revoked"
                )
            revocation = ClinicianApprovalRevocation(
                approval_id=approval.id,
                revoked_by_reference=clinician.reference,
                revoked_at=revoked_at,
            )
            session.add(revocation)
            session.flush()
            self._add_audit(
                session,
                AuditEntityType.ASSESSMENT_VERSION,
                approval.assessment_version_id,
                "clinician.approval.revoked",
                clinician.reference,
                revoked_at,
            )
            return revocation.id

    def _execute(
        self,
        tool_name: str,
        data: object,
        action_id: UUID,
        operation: Callable[[Session], ResultT],
        *,
        actor_reference: str = SERVICE_ACTOR,
    ) -> ResultT | ToolError:
        started_at = datetime.now(UTC)
        input_digest = self._digest(data)
        try:
            with self._session_factory.begin() as session:
                result = operation(session)
                completed_at = datetime.now(UTC)
                self._write_execution(
                    session,
                    tool_name,
                    ToolExecutionStatus.SUCCEEDED,
                    started_at,
                    completed_at,
                    input_digest,
                    self._digest(result),
                    None,
                    action_id,
                    actor_reference,
                )
            return result
        except ToolFailure as error:
            return self._fail(
                tool_name,
                data,
                action_id,
                started_at,
                error.code,
                str(error),
                error.retryable,
                actor_reference,
            )
        except (SQLAlchemyError, ValidationError):
            LOGGER.exception("Triage MCP tool failed: %s", tool_name)
            return self._fail(
                tool_name,
                data,
                action_id,
                started_at,
                "TOOL_EXECUTION_FAILED",
                "Tool execution failed",
                True,
                actor_reference,
            )
        except Exception:
            LOGGER.exception("Unexpected Triage MCP tool failure: %s", tool_name)
            return self._fail(
                tool_name,
                data,
                action_id,
                started_at,
                "INTERNAL_ERROR",
                "Tool execution failed unexpectedly",
                False,
                actor_reference,
            )

    def _fail(
        self,
        tool_name: str,
        data: object,
        action_id: UUID,
        started_at: datetime,
        code: str,
        message: str,
        retryable: bool,
        actor_reference: str,
    ) -> ToolError:
        completed_at = datetime.now(UTC)
        with self._session_factory.begin() as session:
            self._write_execution(
                session,
                tool_name,
                ToolExecutionStatus.FAILED,
                started_at,
                completed_at,
                self._digest(data),
                self._digest({"code": code, "message": message}),
                code,
                action_id,
                actor_reference,
            )
        return ToolError(
            code=code,
            message=message,
            action_id=action_id,
            retryable=retryable,
        )

    @staticmethod
    def _write_execution(
        session: Session,
        tool_name: str,
        status: ToolExecutionStatus,
        started_at: datetime,
        completed_at: datetime,
        input_digest: str,
        output_digest: str,
        error_code: str | None,
        action_id: UUID,
        actor_reference: str,
    ) -> None:
        session.add(
            ToolExecution(
                id=action_id,
                tool_name=tool_name,
                status=status.value,
                started_at=started_at,
                completed_at=completed_at,
                input_digest=input_digest,
                output_digest=output_digest,
                error_code=error_code,
            )
        )
        session.flush()
        TriageToolsService._add_audit(
            session,
            AuditEntityType.TOOL_EXECUTION,
            action_id,
            f"tool.{tool_name}.{status.value}",
            actor_reference,
            completed_at,
        )

    @staticmethod
    def _add_audit(
        session: Session,
        entity_type: AuditEntityType,
        entity_id: UUID,
        action: str,
        actor_reference: str,
        occurred_at: datetime,
    ) -> None:
        EventRepository(session).add_audit_log(
            AuditLogCreate(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                actor_reference=actor_reference,
                occurred_at=occurred_at,
            )
        )

    @staticmethod
    def _validate_observation(
        payload: Mapping[str, object],
    ) -> VitalObservationCreate:
        observation_fields = VitalObservationCreate.model_fields
        normalized_payload = {
            field: payload[field] for field in observation_fields if field in payload
        }
        return VitalObservationCreate.model_validate(normalized_payload)

    @staticmethod
    def _require_permission(
        clinician: AuthenticatedClinician | None, permission: str
    ) -> AuthenticatedClinician:
        if clinician is None or not clinician.subject.strip():
            raise ToolFailure("UNAUTHENTICATED", "Authenticated clinician is required")
        if permission not in clinician.permissions:
            raise ToolFailure(
                "FORBIDDEN", "Clinician is not authorized for this operation"
            )
        return clinician

    @staticmethod
    def _digest(data: object) -> str:
        if hasattr(data, "model_dump"):
            payload = data.model_dump(mode="json")
        else:
            payload = data
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from triage_sentinel.data.enums import AuditEntityType
from triage_sentinel.data.models import (
    AssessmentVersion,
    ClinicianApproval,
    WorkflowExecution,
    WorkflowExecutionEvent,
)
from triage_sentinel.data.repositories import EventRepository
from triage_sentinel.data.schemas import AuditLogCreate
from triage_sentinel.evaluation.iitt import PROTOCOL_ID, PROTOCOL_VERSION
from triage_sentinel.workflow.schemas import WorkflowStartInput

WORKFLOW_ACTOR = "SYN-WORKFLOW-SERVICE"

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "received": {"validating", "failed"},
    "validating": {"evaluating", "failed"},
    "evaluating": {"generating_recommendation", "failed"},
    "generating_recommendation": {"requesting_review", "failed"},
    "requesting_review": {"awaiting_clinician_approval", "failed"},
    "awaiting_clinician_approval": {"committing"},
    "committing": {"completed", "failed"},
    "failed": set(),
    "completed": set(),
}


class WorkflowRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        data: WorkflowStartInput,
        *,
        model_id: str,
        input_digest: str,
    ) -> WorkflowExecution:
        with self._session_factory.begin() as session:
            assessment = session.scalar(
                select(AssessmentVersion).where(
                    AssessmentVersion.id == data.assessment_version_id,
                    AssessmentVersion.encounter_id == data.encounter_id,
                )
            )
            if assessment is None:
                raise WorkflowRepositoryError("ASSESSMENT_NOT_FOUND")
            now = datetime.now(UTC)
            execution = WorkflowExecution(
                id=uuid4(),
                encounter_id=data.encounter_id,
                assessment_version_id=assessment.id,
                assessment_number=assessment.version,
                model_id=model_id,
                protocol_id=PROTOCOL_ID,
                protocol_version=PROTOCOL_VERSION,
                status="received",
                input_digest=input_digest,
                queue_name=data.queue_name,
                created_at=now,
                updated_at=now,
            )
            session.add(execution)
            session.flush()
            self._append_event(
                session,
                execution,
                state="received",
                event_type="workflow.started",
                details={"model_id": model_id, "input_digest": input_digest},
                occurred_at=now,
            )
            return execution

    def transition(
        self,
        execution_id: UUID,
        *,
        expected_status: str,
        status: str,
        event_type: str,
        details: dict[str, Any] | None = None,
        action_id: UUID | None = None,
        fields: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        if status not in ALLOWED_TRANSITIONS.get(expected_status, set()):
            raise WorkflowRepositoryError("INVALID_WORKFLOW_TRANSITION")
        with self._session_factory.begin() as session:
            execution = session.scalar(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id)
                .with_for_update()
            )
            if execution is None:
                raise WorkflowRepositoryError("WORKFLOW_NOT_FOUND")
            if execution.status != expected_status:
                raise WorkflowRepositoryError("WORKFLOW_STATE_CONFLICT")
            execution.status = status
            execution.updated_at = datetime.now(UTC)
            if fields:
                for field, value in fields.items():
                    if field not in {
                        "validation_result",
                        "evaluation_result",
                        "recommendation",
                        "review_request_id",
                        "approval_id",
                        "commit_result",
                        "error_code",
                        "protocol_id",
                        "protocol_version",
                    }:
                        raise WorkflowRepositoryError("INVALID_WORKFLOW_FIELD")
                    setattr(execution, field, value)
            self._append_event(
                session,
                execution,
                state=status,
                event_type=event_type,
                action_id=action_id,
                details=details or {},
                occurred_at=execution.updated_at,
            )
            return execution

    def fail(
        self, execution_id: UUID, code: str, *, details: dict[str, Any] | None = None
    ) -> WorkflowExecution:
        with self._session_factory.begin() as session:
            execution = session.scalar(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id)
                .with_for_update()
            )
            if execution is None:
                raise WorkflowRepositoryError("WORKFLOW_NOT_FOUND")
            if execution.status in {"failed", "completed"}:
                return execution
            if "failed" not in ALLOWED_TRANSITIONS.get(execution.status, set()):
                raise WorkflowRepositoryError("INVALID_WORKFLOW_TRANSITION")
            execution.status = "failed"
            execution.error_code = code
            execution.updated_at = datetime.now(UTC)
            self._append_event(
                session,
                execution,
                state="failed",
                event_type="workflow.failed",
                details={"error_code": code, **(details or {})},
                occurred_at=execution.updated_at,
            )
            return execution

    def get(self, execution_id: UUID) -> WorkflowExecution | None:
        with self._session_factory() as session:
            return session.get(WorkflowExecution, execution_id)

    def events(self, execution_id: UUID) -> list[WorkflowExecutionEvent]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(WorkflowExecutionEvent)
                    .where(WorkflowExecutionEvent.workflow_execution_id == execution_id)
                    .order_by(
                        WorkflowExecutionEvent.occurred_at,
                        WorkflowExecutionEvent.id,
                    )
                ).all()
            )

    def approval_for_request(self, request_id: UUID) -> ClinicianApproval | None:
        with self._session_factory() as session:
            approval = session.scalar(
                select(ClinicianApproval).where(
                    ClinicianApproval.request_id == request_id
                )
            )
            if approval is not None:
                session.expunge(approval)
            return approval

    def record_approval(
        self, execution_id: UUID, approval_id: UUID, clinician_reference: str
    ) -> None:
        with self._session_factory.begin() as session:
            execution = session.scalar(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id)
                .with_for_update()
            )
            if execution is None:
                raise WorkflowRepositoryError("WORKFLOW_NOT_FOUND")
            if execution.status != "committing":
                raise WorkflowRepositoryError("WORKFLOW_STATE_CONFLICT")
            if execution.approval_id not in {None, approval_id}:
                raise WorkflowRepositoryError("APPROVAL_ID_CONFLICT")
            execution.approval_id = approval_id
            execution.updated_at = datetime.now(UTC)
            self._append_event(
                session,
                execution,
                state="committing",
                event_type="workflow.approval.verified",
                details={"clinician_reference": clinician_reference},
                occurred_at=execution.updated_at,
            )

    def record_event(
        self,
        execution_id: UUID,
        *,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        with self._session_factory.begin() as session:
            execution = session.scalar(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id)
                .with_for_update()
            )
            if execution is None:
                raise WorkflowRepositoryError("WORKFLOW_NOT_FOUND")
            now = datetime.now(UTC)
            execution.updated_at = now
            self._append_event(
                session,
                execution,
                state=execution.status,
                event_type=event_type,
                details=details,
                occurred_at=now,
            )

    @classmethod
    def _append_event(
        cls,
        session: Session,
        execution: WorkflowExecution,
        *,
        state: str,
        event_type: str,
        details: dict[str, Any],
        occurred_at: datetime,
        action_id: UUID | None = None,
    ) -> None:
        event = WorkflowExecutionEvent(
            id=uuid4(),
            workflow_execution_id=execution.id,
            state=state,
            event_type=event_type,
            action_id=action_id,
            details=details,
            occurred_at=occurred_at,
        )
        session.add(event)
        session.flush()
        EventRepository(session).add_audit_log(
            AuditLogCreate(
                entity_type=AuditEntityType.WORKFLOW_EXECUTION,
                entity_id=execution.id,
                action=event_type,
                actor_reference=WORKFLOW_ACTOR,
                occurred_at=occurred_at,
            )
        )


class WorkflowRepositoryError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

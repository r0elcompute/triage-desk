import asyncio
import hashlib
import importlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select

from triage_sentinel.data.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from triage_sentinel.data.enums import AuditEntityType
from triage_sentinel.data.models import (
    AssessmentVersion,
    AuditLog,
    ClinicianApproval,
    ClinicianReview,
    ClinicianReviewRequest,
    Patient,
    PresentingComplaint,
    TriageEncounter,
    VitalObservation,
    WorkflowExecution,
)
from triage_sentinel.data.repositories import (
    AssessmentRepository,
    EncounterRepository,
    EventRepository,
    PatientRepository,
    WorkflowExecutionRepository,
)
from triage_sentinel.data.schemas import (
    AssessmentVersionCreate,
    AuditLogCreate,
    ClinicianReviewCreate,
    EncounterCreate,
    PatientCreate,
    PresentingComplaintCreate,
    VitalObservationCreate,
)
from triage_sentinel.reference_mcp import FilesystemMCPReferenceClient
from triage_sentinel.workflow import (
    OllamaStructuredModel,
    StdioMCPToolCaller,
    TriageWorkflow,
    WorkflowRequest,
    WorkflowState,
    persist_workflow_execution,
)
from triage_tools_mcp.schemas import AuthenticatedClinician
from triage_tools_mcp.service import (
    APPROVE_PERMISSION,
    COMMIT_PERMISSION,
    ToolFailure,
    TriageToolsService,
)

WORKFLOW_RUN_PERMISSION = "triage:workflow:run"


class WorkflowAPISettings(BaseSettings):
    triage_workflow_principal_provider: str | None = None
    triage_ollama_model: str = "qwen3:1.7b"
    triage_ollama_url: str = "http://127.0.0.1:11434"
    triage_mcp_command: str = "triage-tools-mcp"
    triage_protocol_reference_directory: str | None = None
    triage_filesystem_mcp_image: str = "triage-filesystem-mcp:1.0.0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class ApprovalResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_number: int = Field(gt=0)
    queue_name: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            strip_whitespace=True,
        ),
    ]
    idempotency_key: Annotated[
        str,
        StringConstraints(
            min_length=16,
            max_length=128,
            pattern=r"^[A-Za-z0-9._:-]+$",
            strip_whitespace=True,
        ),
    ]


router = APIRouter(prefix="/workflows", tags=["workflow"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_reference: Annotated[
        str | None,
        StringConstraints(
            min_length=5,
            max_length=68,
            pattern=r"^SYN-[A-Z0-9][A-Z0-9-]{1,63}$",
        ),
    ] = None
    complaint_code: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ]
    complaint_summary: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    observations: list[VitalObservationCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def synthetic_observations_only(self) -> "DashboardIntakeRequest":
        if not self.complaint_summary.strip():
            raise ValueError("complaint_summary must not be blank")
        for observation in self.observations:
            if (
                observation.source_reference is None
                or not observation.source_reference.startswith("SYN-")
            ):
                raise ValueError(
                    "intake observations require synthetic source references"
                )
        return self


class DashboardReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "rejected", "overridden"]
    override_summary: (
        Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None
    ) = None
    override_reason_code: (
        Annotated[
            str,
            StringConstraints(
                min_length=1,
                max_length=64,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            ),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_override(self) -> "DashboardReviewRequest":
        if self.decision == "overridden":
            if not self.override_summary or not self.override_summary.strip():
                raise ValueError("An override reason is required")
            if self.override_reason_code is None:
                raise ValueError("An override reason code is required")
        elif self.override_summary is not None or self.override_reason_code is not None:
            raise ValueError("Override fields are only valid for overridden decisions")
        return self


@dashboard_router.get("/session")
async def get_dashboard_session(request: Request) -> dict[str, Any]:
    clinician = await _authenticated_principal(request)
    permissions = clinician.permissions
    return {
        "authenticated": True,
        "clinician_reference": clinician.reference,
        "can_run_workflow": WORKFLOW_RUN_PERMISSION in permissions,
        "can_review": APPROVE_PERMISSION in permissions,
        "can_commit_queue": COMMIT_PERMISSION in permissions,
    }


@lru_cache(maxsize=1)
def _session_factory():
    settings = DatabaseSettings()
    try:
        engine = create_database_engine(settings.database_url)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow persistence is not configured",
        ) from error
    return create_session_factory(engine)


def _load_principal_provider(reference: str) -> Any:
    module_name, separator, object_name = reference.partition(":")
    if not separator or not module_name or not object_name:
        raise ValueError("Principal provider must use module.path:object_name")
    configured = getattr(importlib.import_module(module_name), object_name)
    return configured() if isinstance(configured, type) else configured


async def _authenticated_principal(request: Request) -> AuthenticatedClinician:
    reference = WorkflowAPISettings().triage_workflow_principal_provider
    if not reference:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trusted workflow identity provider is not configured",
        )
    try:
        provider = _load_principal_provider(reference)
        result = provider(request)
        if asyncio.iscoroutine(result):
            result = await result
        return AuthenticatedClinician.model_validate(result)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not authenticate workflow request",
        ) from error


def _require_permission(clinician: AuthenticatedClinician, permission: str) -> None:
    if permission not in clinician.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clinician is not authorized for this workflow operation",
        )


@dashboard_router.post("/intakes", status_code=status.HTTP_201_CREATED)
async def create_synthetic_intake(
    body: DashboardIntakeRequest, request: Request
) -> dict[str, str]:
    clinician = await _authenticated_principal(request)
    _require_permission(clinician, WORKFLOW_RUN_PERMISSION)
    factory = _session_factory()
    now = datetime.now(UTC)
    patient_reference = (
        body.patient_reference or f"SYN-PATIENT-{uuid4().hex[:12].upper()}"
    )
    try:
        with factory.begin() as session:
            patient = PatientRepository(session).create(
                PatientCreate(synthetic_reference=patient_reference)
            )
            encounter = EncounterRepository(session).create(
                patient.id, EncounterCreate(started_at=now)
            )
            encounter_repository = EncounterRepository(session)
            complaint = encounter_repository.add_complaint(
                encounter.id,
                PresentingComplaintCreate(
                    concept_code=body.complaint_code,
                    summary=body.complaint_summary.strip(),
                ),
            )
            for observation in body.observations:
                encounter_repository.add_vital_observation(encounter.id, observation)
            assessment = AssessmentRepository(session).create_version(
                encounter.id,
                AssessmentVersionCreate(
                    recommendation="Awaiting configured protocol evaluation.",
                    created_by_reference="SYN-DASHBOARD-SERVICE",
                ),
            )
            event_repository = EventRepository(session)
            for entity_type, entity_id, action in (
                (
                    AuditEntityType.PATIENT,
                    patient.id,
                    "dashboard.intake.patient_created",
                ),
                (
                    AuditEntityType.ENCOUNTER,
                    encounter.id,
                    "dashboard.intake.encounter_created",
                ),
                (
                    AuditEntityType.COMPLAINT,
                    complaint.id,
                    "dashboard.intake.complaint_recorded",
                ),
                (
                    AuditEntityType.ASSESSMENT_VERSION,
                    assessment.id,
                    "dashboard.intake.assessment_created",
                ),
            ):
                event_repository.add_audit_log(
                    AuditLogCreate(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        action=action,
                        actor_reference=clinician.reference,
                        occurred_at=now,
                    )
                )
        return {
            "patient_reference": patient_reference,
            "encounter_id": str(encounter.id),
            "assessment_version_id": str(assessment.id),
        }
    except Exception as error:
        if "unique" in str(error).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Synthetic patient reference already exists",
            ) from error
        raise


@dashboard_router.get("/queue")
async def get_dashboard_queue() -> list[dict[str, Any]]:
    factory = _session_factory()
    with factory() as session:
        executions = session.scalars(
            select(WorkflowExecution)
            .order_by(WorkflowExecution.started_at.desc())
            .limit(100)
        ).all()
        rows: list[dict[str, Any]] = []
        for execution in executions:
            encounter = session.get(TriageEncounter, execution.encounter_id)
            assessment = session.get(AssessmentVersion, execution.assessment_version_id)
            if encounter is None or assessment is None:
                continue
            patient = session.get(Patient, encounter.patient_id)
            complaint = session.scalar(
                select(PresentingComplaint)
                .where(PresentingComplaint.encounter_id == encounter.id)
                .order_by(PresentingComplaint.created_at)
                .limit(1)
            )
            latest_review = session.scalar(
                select(ClinicianReview)
                .where(ClinicianReview.assessment_version_id == assessment.id)
                .order_by(ClinicianReview.reviewed_at.desc())
                .limit(1)
            )
            pending_request = session.scalar(
                select(ClinicianReviewRequest)
                .where(ClinicianReviewRequest.assessment_version_id == assessment.id)
                .order_by(ClinicianReviewRequest.created_at.desc())
                .limit(1)
            )
            findings = (execution.evaluation_result or {}).get("findings", [])
            rows.append(
                {
                    "workflow_id": str(execution.id),
                    "encounter_id": str(encounter.id),
                    "assessment_version_id": str(assessment.id),
                    "assessment_number": assessment.version,
                    "patient_reference": patient.synthetic_reference
                    if patient
                    else "SYN-UNKNOWN",
                    "complaint": complaint.summary
                    if complaint
                    else "No presenting complaint",
                    "started_at": execution.started_at.isoformat(),
                    "workflow_status": execution.status,
                    "review_status": (
                        latest_review.decision
                        if latest_review
                        else "pending"
                        if execution.status == "pending_review"
                        else execution.status
                    ),
                    "proposed_findings": findings,
                    "protocol_reference": execution.protocol_reference,
                    "error_code": execution.error_code,
                    "review_request_id": str(pending_request.id)
                    if pending_request
                    else None,
                }
            )
        return rows


@dashboard_router.get("/encounters/{encounter_id}")
async def get_dashboard_encounter(encounter_id: UUID) -> dict[str, Any]:
    factory = _session_factory()
    with factory() as session:
        encounter = session.get(TriageEncounter, encounter_id)
        if encounter is None:
            raise HTTPException(status_code=404, detail="Encounter was not found")
        patient = session.get(Patient, encounter.patient_id)
        complaints = session.scalars(
            select(PresentingComplaint)
            .where(PresentingComplaint.encounter_id == encounter_id)
            .order_by(PresentingComplaint.created_at)
        ).all()
        observations = session.scalars(
            select(VitalObservation)
            .where(VitalObservation.encounter_id == encounter_id)
            .order_by(VitalObservation.measured_at.desc())
        ).all()
        assessments = session.scalars(
            select(AssessmentVersion)
            .where(AssessmentVersion.encounter_id == encounter_id)
            .order_by(AssessmentVersion.version.desc())
        ).all()
        executions = session.scalars(
            select(WorkflowExecution)
            .where(WorkflowExecution.encounter_id == encounter_id)
            .order_by(WorkflowExecution.started_at.desc())
        ).all()
        requests = session.scalars(
            select(ClinicianReviewRequest)
            .where(ClinicianReviewRequest.encounter_id == encounter_id)
            .order_by(ClinicianReviewRequest.created_at.desc())
        ).all()
        assessment_ids = [item.id for item in assessments]
        reviews = (
            session.scalars(
                select(ClinicianReview)
                .where(ClinicianReview.assessment_version_id.in_(assessment_ids))
                .order_by(ClinicianReview.reviewed_at.desc())
            ).all()
            if assessment_ids
            else []
        )
        approvals = (
            session.scalars(
                select(ClinicianApproval)
                .where(ClinicianApproval.assessment_version_id.in_(assessment_ids))
                .order_by(ClinicianApproval.created_at.desc())
            ).all()
            if assessment_ids
            else []
        )
        audit_entity_ids = [
            encounter_id,
            *(item.id for item in assessments),
            *(item.id for item in executions),
        ]
        audit_entity_ids.extend(item.id for item in complaints)
        audit_entity_ids.extend(item.id for item in observations)
        audit_entity_ids.extend(item.id for item in reviews)
        audit_entity_ids.extend(item.id for item in approvals)
        audit = (
            session.scalars(
                select(AuditLog)
                .where(AuditLog.entity_id.in_(audit_entity_ids))
                .order_by(AuditLog.occurred_at.desc())
            ).all()
            if audit_entity_ids
            else []
        )
        return {
            "encounter": {
                "id": str(encounter.id),
                "status": encounter.status,
                "started_at": encounter.started_at.isoformat(),
                "patient_reference": patient.synthetic_reference
                if patient
                else "SYN-UNKNOWN",
            },
            "complaints": [
                {"id": str(item.id), "code": item.concept_code, "summary": item.summary}
                for item in complaints
            ],
            "observations": [
                {
                    "id": str(item.id),
                    "measurement": item.measurement,
                    "value": str(item.value),
                    "unit": item.unit,
                    "measured_at": item.measured_at.isoformat(),
                    "source": item.source,
                    "source_reference": item.source_reference,
                }
                for item in observations
            ],
            "assessments": [
                {
                    "id": str(item.id),
                    "version": item.version,
                    "recommendation": item.recommendation,
                    "reviews": sorted(
                        [
                            {
                                "id": str(review.id),
                                "decision": review.decision,
                                "clinician_reference": review.clinician_reference,
                                "reviewed_at": review.reviewed_at.isoformat(),
                                "override_summary": review.override_summary,
                                "override_reason_code": review.override_reason_code,
                            }
                            for review in reviews
                            if review.assessment_version_id == item.id
                        ]
                        + [
                            {
                                "id": str(approval.id),
                                "decision": "approved",
                                "clinician_reference": approval.clinician_reference,
                                "reviewed_at": approval.created_at.isoformat(),
                                "override_summary": None,
                                "override_reason_code": None,
                            }
                            for approval in approvals
                            if approval.assessment_version_id == item.id
                        ],
                        key=lambda decision: decision["reviewed_at"],
                        reverse=True,
                    ),
                }
                for item in assessments
            ],
            "workflows": [
                {
                    "id": str(item.id),
                    "assessment_version_id": str(item.assessment_version_id),
                    "status": item.status,
                    "started_at": item.started_at.isoformat(),
                    "validation": item.validation_result,
                    "evaluation": item.evaluation_result,
                    "recommendation": item.recommendation_result,
                    "protocol_reference": item.protocol_reference,
                    "reference_retrieval": item.reference_retrieval,
                    "error_code": item.error_code,
                    "transitions": item.transitions,
                }
                for item in executions
            ],
            "review_requests": [
                {
                    "id": str(item.id),
                    "assessment_version_id": str(item.assessment_version_id),
                    "reason": item.reason,
                    "created_at": item.created_at.isoformat(),
                    "pending": session.scalar(
                        select(ClinicianApproval.id).where(
                            ClinicianApproval.request_id == item.id
                        )
                    )
                    is None
                    and not any(
                        review.assessment_version_id == item.assessment_version_id
                        for review in reviews
                    ),
                }
                for item in requests
            ],
            "audit_history": [
                {
                    "action": item.action,
                    "actor_reference": item.actor_reference,
                    "occurred_at": item.occurred_at.isoformat(),
                }
                for item in audit
            ],
        }


@dashboard_router.post("/assessments/{assessment_id}/review")
async def record_dashboard_review(
    assessment_id: UUID, body: DashboardReviewRequest, request: Request
) -> dict[str, str]:
    clinician = await _authenticated_principal(request)
    _require_permission(clinician, APPROVE_PERMISSION)
    if body.decision == "accepted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use the protected workflow approval endpoint to approve and commit",
        )
    factory = _session_factory()
    reviewed_at = datetime.now(UTC)
    with factory.begin() as session:
        assessment = session.scalar(
            select(AssessmentVersion)
            .where(AssessmentVersion.id == assessment_id)
            .with_for_update()
        )
        if assessment is None:
            raise HTTPException(status_code=404, detail="Assessment was not found")
        latest_version = session.scalar(
            select(func.max(AssessmentVersion.version)).where(
                AssessmentVersion.encounter_id == assessment.encounter_id
            )
        )
        if assessment.version != latest_version:
            raise HTTPException(status_code=409, detail="Assessment version is stale")
        review = AssessmentRepository(session).add_review(
            assessment.id,
            ClinicianReviewCreate(
                clinician_reference=clinician.reference,
                decision=body.decision,
                reviewed_at=reviewed_at,
                override_summary=body.override_summary,
                override_reason_code=body.override_reason_code,
            ),
        )
        EventRepository(session).add_audit_log(
            AuditLogCreate(
                entity_type=AuditEntityType.CLINICIAN_REVIEW,
                entity_id=review.id,
                action=f"clinician.review.{review.decision}",
                actor_reference=clinician.reference,
                occurred_at=reviewed_at,
            )
        )
        return {"review_id": str(review.id), "decision": review.decision}


def _make_workflow(
    *, clinician: AuthenticatedClinician | None = None
) -> TriageWorkflow:
    settings = WorkflowAPISettings()
    return TriageWorkflow(
        StdioMCPToolCaller(
            command=settings.triage_mcp_command,
            cwd=Path(__file__).resolve().parents[2],
            clinician=clinician,
        ),
        OllamaStructuredModel(
            model_id=settings.triage_ollama_model,
            base_url=settings.triage_ollama_url,
        ),
        reference_client=(
            FilesystemMCPReferenceClient(
                approved_directory=settings.triage_protocol_reference_directory,
                image=settings.triage_filesystem_mcp_image,
            )
            if settings.triage_protocol_reference_directory
            else None
        ),
    )


@router.post("/triage")
async def start_triage_workflow(
    body: WorkflowRequest, request: Request
) -> WorkflowState:
    clinician = await _authenticated_principal(request)
    _require_permission(clinician, WORKFLOW_RUN_PERMISSION)
    try:
        state = await _make_workflow().run(body)
        persist_workflow_execution(_session_factory(), body, state)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow execution could not be recorded",
        ) from error
    return {
        key: value
        for key, value in state.items()
        if key not in {"request", "protocol_reference"}
    }


@router.post("/{workflow_id}/approve")
async def approve_and_resume_workflow(
    workflow_id: UUID,
    body: ApprovalResumeRequest,
    request: Request,
) -> WorkflowState:
    clinician = await _authenticated_principal(request)
    _require_permission(clinician, APPROVE_PERMISSION)
    _require_permission(clinician, COMMIT_PERMISSION)
    factory = _session_factory()
    with factory() as session:
        execution = WorkflowExecutionRepository(session).get(workflow_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="Workflow was not found")
        _assert_resumable(execution)
        assessment = session.get(AssessmentVersion, execution.assessment_version_id)
        if assessment is None or assessment.version != body.assessment_number:
            raise HTTPException(status_code=409, detail="Assessment version is stale")
        execution_data = {
            "id": execution.id,
            "encounter_id": execution.encounter_id,
            "assessment_version_id": execution.assessment_version_id,
            "model_identifier": execution.model_identifier,
            "status": execution.status,
            "transitions": list(execution.transitions),
            "validation_result": execution.validation_result,
            "validation_valid": execution.validation_valid,
            "validation_complete": execution.validation_complete,
            "evaluation_result": execution.evaluation_result,
            "evaluation_action_id": execution.evaluation_action_id,
            "recommendation_result": execution.recommendation_result,
            "recommendation_valid": execution.recommendation_valid,
            "review_request_result": execution.review_request_result,
            "review_request_id": execution.review_request_id,
            "reference_retrieval": execution.reference_retrieval,
            "protocol_reference": execution.protocol_reference,
        }

    assert execution_data["review_request_id"] is not None
    service = TriageToolsService(factory)
    try:
        approval_id = await asyncio.to_thread(
            service.approve_review_request,
            execution_data["review_request_id"],
            clinician,
        )
        state = _resume_state(execution_data)
        resumed = await _make_workflow(clinician=clinician).commit_approved(
            state,
            clinician=clinician,
            approval_id=approval_id,
            assessment_number=body.assessment_number,
            queue_name=body.queue_name,
            idempotency_key=body.idempotency_key,
        )
    except (ToolFailure, PermissionError, ValueError, RuntimeError) as error:
        _record_resume_failure(factory, workflow_id, str(error))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval or protected queue commit was rejected",
        ) from error
    _record_resume_success(factory, workflow_id, resumed)
    return resumed


def _assert_resumable(execution: WorkflowExecution) -> None:
    if (
        execution.status != "pending_review"
        or not execution.validation_valid
        or not execution.validation_complete
        or execution.evaluation_action_id is None
        or not execution.recommendation_valid
        or execution.review_request_id is None
        or execution.reference_retrieval.get("status") != "succeeded"
        or execution.protocol_reference is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow is not eligible for an approved queue commit",
        )


def _resume_state(execution: dict[str, Any]) -> WorkflowState:
    return {
        "workflow_id": str(execution["id"]),
        "assessment_version_id": str(execution["assessment_version_id"]),
        "model_id": execution["model_identifier"],
        "status": "pending_review",
        "transitions": execution["transitions"],
        "validation": execution["validation_result"],
        "evaluation": execution["evaluation_result"],
        "recommendation": execution["recommendation_result"],
        "review_request": execution["review_request_result"],
        "reference_retrieval": execution["reference_retrieval"],
        "protocol_reference_metadata": execution["protocol_reference"],
    }


def _record_resume_failure(factory: Any, workflow_id: UUID, message: str) -> None:
    with factory.begin() as session:
        execution = WorkflowExecutionRepository(session).get(workflow_id)
        if execution is None:
            return
        execution.error_code = "APPROVAL_OR_COMMIT_REJECTED"
        execution.transitions = [
            *execution.transitions,
            {"state": "queue_commit_rejected", "at": datetime.now(UTC).isoformat()},
        ]


def _record_resume_success(
    factory: Any, workflow_id: UUID, state: WorkflowState
) -> None:
    digest = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    commit = state["queue_commit"]
    with factory.begin() as session:
        execution = WorkflowExecutionRepository(session).get(workflow_id)
        if execution is None:
            raise RuntimeError("Workflow disappeared while recording commit")
        execution.status = "committed"
        execution.error_code = None
        execution.result_digest = digest
        execution.completed_at = datetime.now(UTC)
        execution.transitions = state["transitions"]
        execution.commit_action_id = UUID(commit["action_id"])

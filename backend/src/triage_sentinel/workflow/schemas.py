from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
)

from triage_sentinel.evaluation.schemas import (
    EvaluateTriageInput,
    EvaluateTriageOutput,
)
from triage_tools_mcp.schemas import (
    CommitApprovedQueueUpdateOutput,
    ValidateVitalsOutput,
)

WorkflowStatus = Literal[
    "received",
    "validating",
    "evaluating",
    "generating_recommendation",
    "requesting_review",
    "awaiting_clinician_approval",
    "committing",
    "completed",
    "failed",
]


class WorkflowSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowStartInput(EvaluateTriageInput):
    queue_name: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            strip_whitespace=True,
        ),
    ] = "SYN-QUEUE-TRIAGE"


class ModelAcknowledgement(WorkflowSchema):
    evaluator_status: Literal[
        "evaluated",
        "red_escalation",
        "clinician_review_required",
        "indeterminate",
    ]
    matched_rule_ids: list[str]


class WorkflowRecommendation(WorkflowSchema):
    evaluator_status: Literal[
        "evaluated",
        "red_escalation",
        "clinician_review_required",
        "indeterminate",
    ]
    triage_category: Literal["red", "yellow", "green"] | None
    matched_rule_ids: list[str]
    supporting_evidence: list[str]
    missing_information: list[str]
    evaluation_uncertainties: list[str]
    unsupported_criteria: list[str]
    summary: str
    requires_clinician_review: Literal[True] = True


class WorkflowEventOutput(WorkflowSchema):
    state: str
    event_type: str
    action_id: UUID | None
    details: dict[str, object]
    occurred_at: AwareDatetime


class WorkflowOutput(WorkflowSchema):
    workflow_id: UUID
    encounter_id: UUID
    assessment_version_id: UUID
    assessment_number: int
    status: WorkflowStatus
    model_id: str
    protocol_id: str
    protocol_version: str
    input_digest: str
    validation_result: ValidateVitalsOutput | None
    evaluation_result: EvaluateTriageOutput | None
    recommendation: WorkflowRecommendation | None
    review_request_id: UUID | None
    approval_id: UUID | None
    commit_result: CommitApprovedQueueUpdateOutput | None
    error_code: str | None
    events: list[WorkflowEventOutput]
    created_at: AwareDatetime
    updated_at: AwareDatetime

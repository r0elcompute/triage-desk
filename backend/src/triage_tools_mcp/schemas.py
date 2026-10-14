from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from triage_sentinel.data.enums import Measurement
from triage_sentinel.evaluation.schemas import (
    EvaluateTriageInput as EvaluateTriageInput,
)
from triage_sentinel.evaluation.schemas import (
    EvaluateTriageOutput as EvaluateTriageOutput,
)
from triage_sentinel.evaluation.schemas import (
    TriageFinding as TriageFinding,
)

IdempotencyKey = Annotated[
    str,
    StringConstraints(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        strip_whitespace=True,
    ),
]


class ToolSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    measurement: str | None = None
    value: Decimal | int | float | str | None = None
    unit: str | None = None
    measured_at: datetime | str | None = None
    source: str | None = None
    source_reference: str | None = None


class ValidateVitalsInput(ToolSchema):
    encounter_id: UUID
    observations: list[ObservationPayload]
    required_measurements: list[Measurement] = Field(min_length=1)
    validated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class ValidationIssue(ToolSchema):
    code: str
    path: str
    message: str


class ValidateVitalsOutput(ToolSchema):
    valid: bool
    complete: bool
    missing_measurements: list[Measurement]
    issues: list[ValidationIssue]
    validated_at: AwareDatetime
    action_id: UUID


class RequestClinicianReviewInput(ToolSchema):
    assessment_version_id: UUID
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class RequestClinicianReviewOutput(ToolSchema):
    status: Literal["pending"]
    request_id: UUID
    assessment_version_id: UUID
    created_at: AwareDatetime
    action_id: UUID


class CommitApprovedQueueUpdateInput(ToolSchema):
    assessment_version_id: UUID
    assessment_number: int = Field(gt=0)
    approval_id: UUID
    queue_name: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            strip_whitespace=True,
        ),
    ]
    idempotency_key: IdempotencyKey


class CommitApprovedQueueUpdateOutput(ToolSchema):
    status: Literal["committed", "already_committed"]
    queue_event_id: UUID
    commit_id: UUID
    assessment_version_id: UUID
    approval_id: UUID
    action_id: UUID


class ToolError(ToolSchema):
    code: str
    message: str
    action_id: UUID
    retryable: bool = False


class AuthenticatedClinician(ToolSchema):
    subject: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    reference: Annotated[
        str,
        StringConstraints(
            min_length=5,
            max_length=68,
            pattern=r"^SYN-[A-Z0-9][A-Z0-9-]{1,63}$",
        ),
    ]
    permissions: frozenset[str]

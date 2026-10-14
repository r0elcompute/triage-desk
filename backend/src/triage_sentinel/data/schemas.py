from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from triage_sentinel.data.enums import (
    AuditEntityType,
    EncounterStatus,
    EvidenceKind,
    Measurement,
    ObservationSource,
    QueueEventType,
    ReviewDecision,
    ToolExecutionStatus,
    Unit,
)

SyntheticReference = Annotated[
    str,
    StringConstraints(
        min_length=5,
        max_length=68,
        pattern=r"^SYN-[A-Z0-9][A-Z0-9-]{1,63}$",
        strip_whitespace=True,
    ),
]
ShortCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        strip_whitespace=True,
    ),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DataSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PatientCreate(DataSchema):
    synthetic_reference: SyntheticReference


class PatientRead(PatientCreate):
    id: UUID
    created_at: AwareDatetime


class EncounterCreate(DataSchema):
    started_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    status: EncounterStatus = EncounterStatus.OPEN
    closed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_closed_timestamp(self) -> "EncounterCreate":
        if (self.status == EncounterStatus.CLOSED) != (self.closed_at is not None):
            raise ValueError("closed encounters require a closed_at timestamp")
        if self.closed_at is not None and self.closed_at < self.started_at:
            raise ValueError("closed_at must be at or after started_at")
        return self


class EncounterRead(EncounterCreate):
    id: UUID
    patient_id: UUID
    closed_at: AwareDatetime | None
    created_at: AwareDatetime


class PresentingComplaintCreate(DataSchema):
    concept_code: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            strip_whitespace=True,
        ),
    ]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=500)]

    @field_validator("summary")
    @classmethod
    def strip_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary must contain non-whitespace characters")
        return value


class PresentingComplaintRead(PresentingComplaintCreate):
    id: UUID
    encounter_id: UUID
    created_at: AwareDatetime


class VitalObservationCreate(DataSchema):
    measurement: Measurement
    value: Decimal = Field(max_digits=12, decimal_places=4)
    unit: Unit
    measured_at: AwareDatetime
    source: ObservationSource
    source_reference: SyntheticReference | None = None

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("value must be finite")
        return value

    @model_validator(mode="after")
    def unit_matches_measurement(self) -> "VitalObservationCreate":
        supported_units = {
            Measurement.HEART_RATE: {Unit.BEATS_PER_MINUTE},
            Measurement.RESPIRATORY_RATE: {Unit.BREATHS_PER_MINUTE},
            Measurement.TEMPERATURE: {Unit.CELSIUS, Unit.FAHRENHEIT},
            Measurement.SYSTOLIC_BLOOD_PRESSURE: {Unit.MILLIMETERS_OF_MERCURY},
            Measurement.DIASTOLIC_BLOOD_PRESSURE: {Unit.MILLIMETERS_OF_MERCURY},
            Measurement.OXYGEN_SATURATION: {Unit.PERCENT},
            Measurement.BODY_WEIGHT: {Unit.KILOGRAMS},
            Measurement.BODY_HEIGHT: {Unit.CENTIMETERS},
            Measurement.BLOOD_GLUCOSE: {Unit.MILLIMOLES_PER_LITER},
        }
        if self.unit not in supported_units[self.measurement]:
            raise ValueError(
                f"{self.unit.value} is not a supported unit for "
                f"{self.measurement.value}"
            )
        return self


class VitalObservationRead(VitalObservationCreate):
    id: UUID
    encounter_id: UUID
    created_at: AwareDatetime


class AssessmentVersionCreate(DataSchema):
    recommendation: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    created_by_reference: SyntheticReference

    @field_validator("recommendation")
    @classmethod
    def strip_recommendation(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("recommendation must contain non-whitespace characters")
        return value


class AssessmentVersionRead(AssessmentVersionCreate):
    id: UUID
    encounter_id: UUID
    version: int
    created_at: AwareDatetime


class AssessmentEvidenceCreate(DataSchema):
    kind: EvidenceKind
    complaint_id: UUID | None = None
    vital_observation_id: UUID | None = None

    @model_validator(mode="after")
    def exactly_one_matching_source(self) -> "AssessmentEvidenceCreate":
        if (self.complaint_id is None) == (self.vital_observation_id is None):
            raise ValueError("provide exactly one evidence source reference")
        if self.kind == EvidenceKind.PRESENTING_COMPLAINT and self.complaint_id is None:
            raise ValueError("presenting complaint evidence requires complaint_id")
        if (
            self.kind == EvidenceKind.VITAL_OBSERVATION
            and self.vital_observation_id is None
        ):
            raise ValueError("vital observation evidence requires vital_observation_id")
        return self


class AssessmentEvidenceRead(AssessmentEvidenceCreate):
    id: UUID
    assessment_version_id: UUID
    created_at: AwareDatetime


class ClinicianReviewCreate(DataSchema):
    clinician_reference: SyntheticReference
    decision: ReviewDecision
    reviewed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    override_summary: (
        Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None
    ) = None
    override_reason_code: ShortCode | None = None

    @model_validator(mode="after")
    def validate_override_fields(self) -> "ClinicianReviewCreate":
        has_override = (
            self.override_summary is not None and self.override_reason_code is not None
        )
        if self.decision == ReviewDecision.OVERRIDDEN and not has_override:
            raise ValueError(
                "overridden decisions require an override summary and reason code"
            )
        if self.decision != ReviewDecision.OVERRIDDEN and (
            self.override_summary is not None or self.override_reason_code is not None
        ):
            raise ValueError("override fields are only valid for overridden decisions")
        if self.override_summary is not None:
            self.override_summary = self.override_summary.strip()
            if not self.override_summary:
                raise ValueError("override_summary must not be blank")
        return self


class ClinicianReviewRead(ClinicianReviewCreate):
    id: UUID
    assessment_version_id: UUID
    created_at: AwareDatetime


class QueueEventCreate(DataSchema):
    queue_name: ShortCode
    event_type: QueueEventType
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    actor_reference: SyntheticReference | None = None


class QueueEventRead(QueueEventCreate):
    id: UUID
    encounter_id: UUID
    created_at: AwareDatetime


class ToolExecutionCreate(DataSchema):
    tool_name: ShortCode
    status: ToolExecutionStatus
    started_at: AwareDatetime
    completed_at: AwareDatetime
    input_digest: Sha256Digest | None = None
    output_digest: Sha256Digest | None = None
    error_code: ShortCode | None = None

    @model_validator(mode="after")
    def validate_execution_result(self) -> "ToolExecutionCreate":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must be at or after started_at")
        if self.status == ToolExecutionStatus.FAILED and self.error_code is None:
            raise ValueError("failed executions require an error_code")
        if self.status != ToolExecutionStatus.FAILED and self.error_code is not None:
            raise ValueError("error_code is only valid for failed executions")
        return self


class ToolExecutionRead(ToolExecutionCreate):
    id: UUID
    created_at: AwareDatetime


class AuditLogCreate(DataSchema):
    entity_type: AuditEntityType
    entity_id: UUID
    action: ShortCode
    actor_reference: SyntheticReference
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditLogRead(AuditLogCreate):
    id: UUID
    created_at: AwareDatetime

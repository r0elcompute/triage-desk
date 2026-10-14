from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
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

from triage_sentinel.data.enums import ObservationSource, Unit

SyntheticId = Annotated[
    str,
    StringConstraints(
        min_length=5,
        max_length=68,
        pattern=r"^SYN-[A-Z0-9][A-Z0-9-]{1,63}$",
        strip_whitespace=True,
    ),
]
ComplaintCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        strip_whitespace=True,
    ),
]


class EvaluationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClinicalMeasurement(StrEnum):
    HEART_RATE = "heart_rate"
    RESPIRATORY_RATE = "respiratory_rate"
    TEMPERATURE = "temperature"
    OXYGEN_SATURATION = "oxygen_saturation"
    AVPU = "avpu"


class AVPUValue(StrEnum):
    ALERT = "A"
    VOICE = "V"
    PAIN = "P"
    UNRESPONSIVE = "U"


class PresentingComplaint(EvaluationSchema):
    complaint_id: SyntheticId
    concept_code: ComplaintCode
    summary: Annotated[str, StringConstraints(min_length=1, max_length=500)]

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary must contain non-whitespace characters")
        return value


class ClinicalObservation(EvaluationSchema):
    observation_id: SyntheticId
    measurement: ClinicalMeasurement
    value: Decimal | AVPUValue
    unit: Unit | None = None
    measured_at: AwareDatetime
    source: ObservationSource

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean_values(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("boolean values are not valid observations")
        return value

    @field_validator("value")
    @classmethod
    def numeric_value_must_be_finite(
        cls, value: Decimal | AVPUValue
    ) -> Decimal | AVPUValue:
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("value must be finite")
        return value

    @model_validator(mode="after")
    def validate_measurement_value_and_unit(self) -> "ClinicalObservation":
        if self.measurement == ClinicalMeasurement.AVPU:
            if not isinstance(self.value, AVPUValue) or self.unit is not None:
                raise ValueError("AVPU requires an A/V/P/U value and no unit")
            return self

        supported_units = {
            ClinicalMeasurement.HEART_RATE: {Unit.BEATS_PER_MINUTE},
            ClinicalMeasurement.RESPIRATORY_RATE: {Unit.BREATHS_PER_MINUTE},
            ClinicalMeasurement.TEMPERATURE: {Unit.CELSIUS, Unit.FAHRENHEIT},
            ClinicalMeasurement.OXYGEN_SATURATION: {Unit.PERCENT},
        }
        if not isinstance(self.value, Decimal):
            raise ValueError("numeric measurements require a numeric value")
        if self.unit not in supported_units[self.measurement]:
            supported = ", ".join(
                sorted(unit.value for unit in supported_units[self.measurement])
            )
            raise ValueError(
                f"{self.measurement.value} requires a supported unit: {supported}"
            )
        return self


class EvaluateTriageInput(EvaluationSchema):
    encounter_id: UUID
    assessment_version_id: UUID
    age_months: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    presenting_complaints: list[PresentingComplaint] = Field(default_factory=list)
    observations: list[ClinicalObservation] = Field(default_factory=list)
    evaluated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("age_months")
    @classmethod
    def age_must_be_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("age_months must be finite")
        return value

    @model_validator(mode="after")
    def validate_observation_set(self) -> "EvaluateTriageInput":
        seen: set[ClinicalMeasurement] = set()
        observation_ids: set[str] = set()
        for observation in self.observations:
            if observation.measurement in seen:
                raise ValueError(
                    f"duplicate observation for {observation.measurement.value}"
                )
            seen.add(observation.measurement)
            if observation.observation_id in observation_ids:
                raise ValueError(
                    f"duplicate observation_id: {observation.observation_id}"
                )
            observation_ids.add(observation.observation_id)
            if observation.measured_at > self.evaluated_at:
                raise ValueError(
                    f"{observation.observation_id} is measured after evaluated_at"
                )
        return self


class RuleContextEvidence(EvaluationSchema):
    input_field: Literal["age_months"]
    value: Decimal


class TriageFinding(EvaluationSchema):
    code: str
    summary: str
    severity: Literal["info", "warning", "urgent"]
    evidence: list[str]
    context_evidence: list[RuleContextEvidence]
    rule_id: str
    rule_state: Literal["met", "not_met", "indeterminate"]
    source_reference: str


class EvaluateTriageOutput(EvaluationSchema):
    status: Literal[
        "evaluated",
        "red_escalation",
        "clinician_review_required",
        "indeterminate",
    ]
    assessment_version_id: UUID
    protocol_id: str
    protocol_version: str
    assessment_output_version: str
    triage_category: Literal["red", "yellow", "green"] | None = None
    findings: list[TriageFinding]
    supporting_evidence: list[str]
    missing_information: list[str]
    evaluation_uncertainties: list[str]
    unsupported_criteria: list[str]
    evaluated_at: AwareDatetime
    action_id: UUID = Field(default_factory=lambda: UUID(int=0))

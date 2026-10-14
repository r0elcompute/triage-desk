from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from typing import Final
from uuid import UUID

from triage_sentinel.data.enums import Unit
from triage_sentinel.evaluation.schemas import (
    AVPUValue,
    ClinicalMeasurement,
    ClinicalObservation,
    EvaluateTriageInput,
    EvaluateTriageOutput,
    RuleContextEvidence,
    TriageFinding,
)

PROTOCOL_ID: Final = "WHO-IITT-ROUTINE"
PROTOCOL_VERSION: Final = "source-snapshot-2026-10-15/ruleset-0.1.0"
ASSESSMENT_OUTPUT_VERSION: Final = "1.0.0"
SOURCE_ADULT = (
    "WHO/ICRC/MSF, Interagency Integrated Triage Tool: Age >= 12, "
    "1-page poster, Red criteria and high-risk vital signs; "
    "https://cdn.who.int/media/docs/default-source/integrated-health-services-"
    "(ihs)/csy/iitt/iitt_adult.pdf?sfvrsn=b2a91431_1"
)
SOURCE_CHILD = (
    "WHO/ICRC/MSF, Interagency Integrated Triage Tool: Age < 12, "
    "1-page poster, Red criteria and high-risk vital signs; "
    "https://cdn.who.int/media/docs/default-source/integrated-health-services-"
    "(ihs)/csy/iitt/iitt_pediatric.pdf?sfvrsn=15161bfb_1"
)

_ADULT_UNSUPPORTED: Final = [
    "IITT-ADULT-RED-CRITERIA-OTHER-THAN-HEART-RATE",
    "IITT-ADULT-YELLOW-CRITERIA",
    "IITT-REFERENCE-CARD-TRAUMA-EXPOSURE-AND-OTHER-CRITERIA",
    "IITT-GREEN-DISPOSITION",
]
_CHILD_UNSUPPORTED: Final = [
    "IITT-CHILD-RED-CRITERIA-OTHER-THAN-UNDER-2-MONTH-TEMPERATURE",
    "IITT-CHILD-YELLOW-CRITERIA",
    "IITT-REFERENCE-CARD-TRAUMA-EXPOSURE-AND-OTHER-CRITERIA",
    "IITT-GREEN-DISPOSITION",
]


class IittDeterministicEvaluator:
    """Evaluate a verified subset of routine WHO IITT vital-sign criteria."""

    def evaluate(self, data: EvaluateTriageInput) -> EvaluateTriageOutput:
        adult = data.age_months >= Decimal("144")
        source = SOURCE_ADULT if adult else SOURCE_CHILD
        observations = {item.measurement: item for item in data.observations}
        age_evidence = [
            RuleContextEvidence(input_field="age_months", value=data.age_months)
        ]
        findings: list[TriageFinding] = []
        missing: set[str] = set()
        uncertainties: set[str] = set()
        red_rule_matched = False
        high_risk_matched = False

        if adult:
            self._evaluate_threshold(
                findings,
                missing,
                observations,
                ClinicalMeasurement.HEART_RATE,
                "IITT-ADULT-RED-HEART-RATE",
                source,
                lambda value: value < 50 or value > 150,
                "red",
                age_evidence,
            )
            red_rule_matched = any(
                item.rule_id == "IITT-ADULT-RED-HEART-RATE"
                and item.rule_state == "met"
                for item in findings
            )
            high_risk_matched |= self._evaluate_threshold(
                findings,
                missing,
                observations,
                ClinicalMeasurement.HEART_RATE,
                "IITT-ADULT-HIGH-RISK-HEART-RATE",
                source,
                lambda value: value < 60 or value > 130,
                "high-risk",
                age_evidence,
            )
            high_risk_matched |= self._evaluate_threshold(
                findings,
                missing,
                observations,
                ClinicalMeasurement.RESPIRATORY_RATE,
                "IITT-ADULT-HIGH-RISK-RESPIRATORY-RATE",
                source,
                lambda value: value < 10 or value > 30,
                "high-risk",
                age_evidence,
            )
        else:
            heart_low, heart_high, respiratory_low, respiratory_high = (
                self._child_vital_limits(data.age_months)
            )
            high_risk_matched |= self._evaluate_threshold(
                findings,
                missing,
                observations,
                ClinicalMeasurement.HEART_RATE,
                "IITT-CHILD-HIGH-RISK-HEART-RATE",
                source,
                lambda value: self._pediatric_heart_cutoff(
                    value, heart_low, heart_high
                ),
                "high-risk",
                age_evidence,
                uncertainties,
            )
            high_risk_matched |= self._evaluate_threshold(
                findings,
                missing,
                observations,
                ClinicalMeasurement.RESPIRATORY_RATE,
                "IITT-CHILD-HIGH-RISK-RESPIRATORY-RATE",
                source,
                lambda value: self._pediatric_cutoff(
                    value, respiratory_low, respiratory_high
                ),
                "high-risk",
                age_evidence,
                uncertainties,
            )

            if data.age_months < Decimal("2"):
                red_rule_matched |= self._evaluate_temperature(
                    findings,
                    missing,
                    observations,
                    "IITT-CHILD-RED-TEMPERATURE-UNDER-2-MONTHS",
                    source,
                    lambda celsius: celsius < 36 or celsius > 39,
                    "red",
                    age_evidence,
                )

        high_risk_matched |= self._evaluate_temperature(
            findings,
            missing,
            observations,
            (
                "IITT-ADULT-HIGH-RISK-TEMPERATURE"
                if adult
                else "IITT-CHILD-HIGH-RISK-TEMPERATURE"
            ),
            source,
            lambda celsius: celsius < 36 or celsius > 39,
            "high-risk",
            age_evidence,
        )
        high_risk_matched |= self._evaluate_threshold(
            findings,
            missing,
            observations,
            ClinicalMeasurement.OXYGEN_SATURATION,
            (
                "IITT-ADULT-HIGH-RISK-OXYGEN-SATURATION"
                if adult
                else "IITT-CHILD-HIGH-RISK-OXYGEN-SATURATION"
            ),
            source,
            lambda value: value < 92,
            "high-risk",
            age_evidence,
        )
        high_risk_matched |= self._evaluate_avpu(
            findings,
            missing,
            observations,
            source,
            age_evidence,
            adult=adult,
        )

        if not data.presenting_complaints:
            missing.add("presenting_complaints")

        if red_rule_matched:
            status = "red_escalation"
            category = "red"
        elif high_risk_matched:
            status = "clinician_review_required"
            category = None
        else:
            status = "indeterminate"
            category = None

        supporting_evidence = sorted(
            {
                evidence
                for finding in findings
                if finding.rule_state == "met"
                for evidence in finding.evidence
            }
        )
        return EvaluateTriageOutput(
            status=status,
            assessment_version_id=data.assessment_version_id,
            protocol_id=PROTOCOL_ID,
            protocol_version=PROTOCOL_VERSION,
            assessment_output_version=ASSESSMENT_OUTPUT_VERSION,
            triage_category=category,
            findings=findings,
            supporting_evidence=supporting_evidence,
            missing_information=sorted(missing),
            evaluation_uncertainties=sorted(uncertainties),
            unsupported_criteria=(
                _ADULT_UNSUPPORTED if adult else _CHILD_UNSUPPORTED
            ),
            evaluated_at=data.evaluated_at,
            action_id=UUID(int=0),
        )

    @staticmethod
    def _child_vital_limits(age_months: Decimal) -> tuple[int, int, int, int]:
        if age_months < Decimal("12"):
            return 90, 180, 25, 50
        if age_months < Decimal("60"):
            return 80, 160, 20, 40
        return 70, 140, 10, 30

    @staticmethod
    def _pediatric_cutoff(value: Decimal, low: int, high: int) -> bool | None:
        if value == high or value == low:
            return None
        return value < low or value > high

    @staticmethod
    def _pediatric_heart_cutoff(
        value: Decimal, low: int, high: int
    ) -> bool | None:
        if value == high:
            return None
        return value < low or value > high

    @classmethod
    def _evaluate_threshold(
        cls,
        findings: list[TriageFinding],
        missing: set[str],
        observations: dict[ClinicalMeasurement, ClinicalObservation],
        measurement: ClinicalMeasurement,
        rule_id: str,
        source: str,
        criterion: Callable[[Decimal], bool | None],
        kind: str,
        context_evidence: list[RuleContextEvidence],
        uncertainties: set[str] | None = None,
    ) -> bool:
        observation = observations.get(measurement)
        if observation is None:
            missing.add(measurement.value)
            return False
        value = observation.value
        if not isinstance(value, Decimal):
            raise ValueError(f"{rule_id} requires a numeric observation")
        matched = criterion(value)
        if matched is None:
            if uncertainties is not None:
                uncertainties.add(rule_id)
        cls._append_finding(
            findings,
            observation,
            rule_id,
            source,
            matched,
            kind,
            context_evidence,
        )
        return matched is True

    @classmethod
    def _evaluate_temperature(
        cls,
        findings: list[TriageFinding],
        missing: set[str],
        observations: dict[ClinicalMeasurement, ClinicalObservation],
        rule_id: str,
        source: str,
        criterion: Callable[[Fraction], bool],
        kind: str,
        context_evidence: list[RuleContextEvidence],
    ) -> bool:
        observation = observations.get(ClinicalMeasurement.TEMPERATURE)
        if observation is None:
            missing.add(ClinicalMeasurement.TEMPERATURE.value)
            return False
        if not isinstance(observation.value, Decimal):
            raise ValueError(f"{rule_id} requires a numeric observation")
        celsius = Fraction(observation.value)
        if observation.unit == Unit.FAHRENHEIT:
            celsius = (celsius - 32) * Fraction(5, 9)
        matched = criterion(celsius)
        cls._append_finding(
            findings,
            observation,
            rule_id,
            source,
            matched,
            kind,
            context_evidence,
        )
        return matched

    @classmethod
    def _evaluate_avpu(
        cls,
        findings: list[TriageFinding],
        missing: set[str],
        observations: dict[ClinicalMeasurement, ClinicalObservation],
        source: str,
        context_evidence: list[RuleContextEvidence],
        *,
        adult: bool,
    ) -> bool:
        measurement = ClinicalMeasurement.AVPU
        observation = observations.get(measurement)
        if observation is None:
            missing.add(measurement.value)
            return False
        if not isinstance(observation.value, AVPUValue):
            raise ValueError("AVPU rule requires an A/V/P/U value")
        matched = observation.value != AVPUValue.ALERT
        cls._append_finding(
            findings,
            observation,
            (
                "IITT-ADULT-HIGH-RISK-AVPU"
                if adult
                else "IITT-CHILD-HIGH-RISK-AVPU"
            ),
            source,
            matched,
            "high-risk",
            context_evidence,
        )
        return matched

    @staticmethod
    def _append_finding(
        findings: list[TriageFinding],
        observation: ClinicalObservation,
        rule_id: str,
        source: str,
        matched: bool | None,
        kind: str,
        context_evidence: list[RuleContextEvidence],
    ) -> None:
        findings.append(
            TriageFinding(
                code=rule_id,
                summary=(
                    "Verified protocol criterion met"
                    if matched is True
                    else (
                        "Protocol cutoff notation is ambiguous at this value"
                        if matched is None
                        else "Verified protocol criterion not met"
                    )
                ),
                severity=(
                    "urgent"
                    if matched is True and kind in {"red", "high-risk"}
                    else "warning"
                    if matched is None
                    else "info"
                ),
                evidence=[observation.observation_id],
                context_evidence=context_evidence,
                rule_id=rule_id,
                rule_state=(
                    "met"
                    if matched is True
                    else "indeterminate"
                    if matched is None
                    else "not_met"
                ),
                source_reference=source,
            )
        )

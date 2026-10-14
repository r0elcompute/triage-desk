from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError
from triage_sentinel.evaluation.iitt import (
    PROTOCOL_ID,
    PROTOCOL_VERSION,
    IittDeterministicEvaluator,
)
from triage_sentinel.evaluation.schemas import EvaluateTriageInput

EVALUATED_AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
ENCOUNTER_ID = UUID("00000000-0000-0000-0000-000000000001")
ASSESSMENT_ID = UUID("00000000-0000-0000-0000-000000000002")
EVALUATOR = IittDeterministicEvaluator()


def observation(
    measurement: str,
    value: str,
    unit: str | None,
    suffix: str,
    *,
    measured_at: str = "2026-10-15T11:00:00Z",
) -> dict[str, str | None]:
    return {
        "observation_id": f"SYN-OBS-{suffix}",
        "measurement": measurement,
        "value": value,
        "unit": unit,
        "measured_at": measured_at,
        "source": "clinician",
    }


def complete_case(
    age_months: int | str,
    *,
    heart_rate: str,
    respiratory_rate: str,
    temperature: str = "37",
    temperature_unit: str = "degC",
    oxygen_saturation: str = "98",
    avpu: str = "A",
) -> EvaluateTriageInput:
    observations = [
        observation("heart_rate", heart_rate, "bpm", "HR"),
        observation(
            "respiratory_rate", respiratory_rate, "breaths/min", "RR"
        ),
        observation("temperature", temperature, temperature_unit, "TEMP"),
        observation("oxygen_saturation", oxygen_saturation, "%", "SPO2"),
        observation("avpu", avpu, None, "AVPU"),
    ]
    return EvaluateTriageInput(
        encounter_id=ENCOUNTER_ID,
        assessment_version_id=ASSESSMENT_ID,
        age_months=age_months,
        presenting_complaints=[
            {
                "complaint_id": "SYN-COMPLAINT-1",
                "concept_code": "SYN-TEST",
                "summary": "Synthetic complaint for protocol rule tests",
            }
        ],
        observations=observations,
        evaluated_at=EVALUATED_AT,
    )


def finding(result, rule_id: str):
    return next(item for item in result.findings if item.rule_id == rule_id)


def test_adult_red_heart_rate_rule_is_evidence_linked_and_deterministic() -> None:
    data = complete_case(600, heart_rate="49", respiratory_rate="18")

    first = EVALUATOR.evaluate(data)
    second = EVALUATOR.evaluate(data)

    assert first == second
    assert first.status == "red_escalation"
    assert first.triage_category == "red"
    assert first.protocol_id == PROTOCOL_ID
    assert first.protocol_version == PROTOCOL_VERSION
    assert first.assessment_output_version == "1.0.0"
    red = finding(first, "IITT-ADULT-RED-HEART-RATE")
    assert red.rule_state == "met"
    assert red.evidence == ["SYN-OBS-HR"]
    assert red.context_evidence[0].input_field == "age_months"
    assert red.context_evidence[0].value == 600
    assert "iitt_adult.pdf" in red.source_reference
    assert first.supporting_evidence == ["SYN-OBS-HR"]


@pytest.mark.parametrize(
    ("age_months", "heart_rate", "respiratory_rate", "hr_rule", "rr_rule"),
    [
        (
            6,
            "90",
            "25",
            "IITT-CHILD-HIGH-RISK-HEART-RATE",
            "IITT-CHILD-HIGH-RISK-RESPIRATORY-RATE",
        ),
        (
            48,
            "80",
            "20",
            "IITT-CHILD-HIGH-RISK-HEART-RATE",
            "IITT-CHILD-HIGH-RISK-RESPIRATORY-RATE",
        ),
        (
            12,
            "80",
            "20",
            "IITT-CHILD-HIGH-RISK-HEART-RATE",
            "IITT-CHILD-HIGH-RISK-RESPIRATORY-RATE",
        ),
        (
            60,
            "70",
            "10",
            "IITT-CHILD-HIGH-RISK-HEART-RATE",
            "IITT-CHILD-HIGH-RISK-RESPIRATORY-RATE",
        ),
    ],
)
def test_pediatric_table_cutpoints_are_not_assumed_normal(
    age_months: int,
    heart_rate: str,
    respiratory_rate: str,
    hr_rule: str,
    rr_rule: str,
) -> None:
    result = EVALUATOR.evaluate(
        complete_case(
            age_months,
            heart_rate=heart_rate,
            respiratory_rate=respiratory_rate,
        )
    )

    assert finding(result, hr_rule).rule_state == "not_met"
    assert finding(result, rr_rule).rule_state == "indeterminate"
    assert rr_rule in result.evaluation_uncertainties
    assert result.status == "indeterminate"
    assert result.triage_category is None


def test_adult_high_risk_heart_rate_requests_review_not_red_category() -> None:
    result = EVALUATOR.evaluate(
        complete_case(600, heart_rate="55", respiratory_rate="18")
    )

    assert result.status == "clinician_review_required"
    assert result.triage_category is None
    assert finding(result, "IITT-ADULT-RED-HEART-RATE").rule_state == "not_met"
    assert finding(result, "IITT-ADULT-HIGH-RISK-HEART-RATE").rule_state == "met"


@pytest.mark.parametrize(
    ("overrides", "rule_id", "evidence_id"),
    [
        ({}, "IITT-ADULT-HIGH-RISK-HEART-RATE", "SYN-OBS-HR"),
        (
            {"respiratory_rate": "31"},
            "IITT-ADULT-HIGH-RISK-RESPIRATORY-RATE",
            "SYN-OBS-RR",
        ),
        (
            {"temperature": "35.9"},
            "IITT-ADULT-HIGH-RISK-TEMPERATURE",
            "SYN-OBS-TEMP",
        ),
        (
            {"oxygen_saturation": "91"},
            "IITT-ADULT-HIGH-RISK-OXYGEN-SATURATION",
            "SYN-OBS-SPO2",
        ),
        ({"avpu": "V"}, "IITT-ADULT-HIGH-RISK-AVPU", "SYN-OBS-AVPU"),
    ],
)
def test_each_adult_high_risk_vital_rule_requires_clinician_review(
    overrides: dict[str, str], rule_id: str, evidence_id: str
) -> None:
    values = {"heart_rate": "55", "respiratory_rate": "18"}
    values.update(overrides)
    result = EVALUATOR.evaluate(complete_case(600, **values))

    matched = finding(result, rule_id)
    assert result.status == "clinician_review_required"
    assert result.triage_category is None
    assert matched.rule_state == "met"
    assert matched.evidence == [evidence_id]
    assert matched.context_evidence[0].input_field == "age_months"


@pytest.mark.parametrize(
    ("overrides", "rule_id", "evidence_id"),
    [
        (
            {"heart_rate": "181"},
            "IITT-CHILD-HIGH-RISK-HEART-RATE",
            "SYN-OBS-HR",
        ),
        (
            {"respiratory_rate": "51"},
            "IITT-CHILD-HIGH-RISK-RESPIRATORY-RATE",
            "SYN-OBS-RR",
        ),
        (
            {"temperature": "35.9"},
            "IITT-CHILD-HIGH-RISK-TEMPERATURE",
            "SYN-OBS-TEMP",
        ),
        (
            {"oxygen_saturation": "91"},
            "IITT-CHILD-HIGH-RISK-OXYGEN-SATURATION",
            "SYN-OBS-SPO2",
        ),
        ({"avpu": "V"}, "IITT-CHILD-HIGH-RISK-AVPU", "SYN-OBS-AVPU"),
    ],
)
def test_each_child_high_risk_vital_rule_requires_clinician_review(
    overrides: dict[str, str], rule_id: str, evidence_id: str
) -> None:
    values = {"heart_rate": "110", "respiratory_rate": "30"}
    values.update(overrides)
    result = EVALUATOR.evaluate(
        complete_case(6, **values)
    )

    matched = finding(result, rule_id)
    assert result.status == "clinician_review_required"
    assert result.triage_category is None
    assert matched.rule_state == "met"
    assert matched.evidence == [evidence_id]


def test_adult_red_heart_rate_upper_cutoff_is_source_traceable() -> None:
    result = EVALUATOR.evaluate(
        complete_case(600, heart_rate="151", respiratory_rate="18")
    )

    assert result.status == "red_escalation"
    assert finding(result, "IITT-ADULT-RED-HEART-RATE").rule_state == "met"


@pytest.mark.parametrize("heart_rate", ["50", "150"])
def test_adult_red_heart_rate_exact_cutoffs_are_not_red(
    heart_rate: str,
) -> None:
    result = EVALUATOR.evaluate(
        complete_case(600, heart_rate=heart_rate, respiratory_rate="18")
    )

    assert finding(result, "IITT-ADULT-RED-HEART-RATE").rule_state == "not_met"
    assert result.triage_category is None
    assert result.status == "clinician_review_required"


def test_age_144_month_boundary_selects_adult_protocol() -> None:
    adult_result = EVALUATOR.evaluate(
        complete_case(144, heart_rate="55", respiratory_rate="18")
    )
    child_result = EVALUATOR.evaluate(
        complete_case("143.9999", heart_rate="55", respiratory_rate="18")
    )

    assert finding(adult_result, "IITT-ADULT-HIGH-RISK-HEART-RATE").rule_state == "met"
    assert (
        finding(child_result, "IITT-CHILD-HIGH-RISK-HEART-RATE").rule_state == "met"
    )
    assert (
        finding(
            child_result, "IITT-CHILD-HIGH-RISK-HEART-RATE"
        ).context_evidence[0].value
        == Decimal("143.9999")
    )


def test_child_under_two_months_temperature_red_criterion() -> None:
    result = EVALUATOR.evaluate(
        complete_case(
            1,
            heart_rate="120",
            respiratory_rate="40",
            temperature="35.9",
        )
    )

    red = finding(result, "IITT-CHILD-RED-TEMPERATURE-UNDER-2-MONTHS")
    assert result.status == "red_escalation"
    assert result.triage_category == "red"
    assert red.rule_state == "met"
    assert red.evidence == ["SYN-OBS-TEMP"]


def test_child_at_two_month_boundary_does_not_use_under_two_month_red_rule() -> None:
    result = EVALUATOR.evaluate(
        complete_case(
            2,
            heart_rate="120",
            respiratory_rate="40",
            temperature="35.9",
        )
    )

    assert (
        "IITT-CHILD-RED-TEMPERATURE-UNDER-2-MONTHS"
        not in {item.rule_id for item in result.findings}
    )
    assert result.status == "clinician_review_required"
    assert result.triage_category is None


def test_child_high_risk_heart_rate_requests_supervising_clinician_review() -> None:
    result = EVALUATOR.evaluate(
        complete_case(36, heart_rate="161", respiratory_rate="30")
    )

    assert result.status == "clinician_review_required"
    assert result.triage_category is None
    assert (
        finding(result, "IITT-CHILD-HIGH-RISK-HEART-RATE").rule_state == "met"
    )


def test_adult_normal_subset_remains_indeterminate_not_green() -> None:
    result = EVALUATOR.evaluate(
        complete_case(600, heart_rate="75", respiratory_rate="20")
    )

    assert result.status == "indeterminate"
    assert result.triage_category is None
    assert result.missing_information == []
    assert result.unsupported_criteria
    assert all(item.rule_state == "not_met" for item in result.findings)


def test_missing_observations_are_reported_without_normal_defaults() -> None:
    result = EVALUATOR.evaluate(
        EvaluateTriageInput(
            encounter_id=ENCOUNTER_ID,
            assessment_version_id=ASSESSMENT_ID,
            age_months=600,
            evaluated_at=EVALUATED_AT,
        )
    )

    assert result.status == "indeterminate"
    assert result.triage_category is None
    assert set(result.missing_information) == {
        "avpu",
        "heart_rate",
        "oxygen_saturation",
        "presenting_complaints",
        "respiratory_rate",
        "temperature",
    }
    assert result.findings == []


def test_temperature_fahrenheit_uses_exact_celsius_comparison() -> None:
    at_boundary = EVALUATOR.evaluate(
        complete_case(
            600,
            heart_rate="75",
            respiratory_rate="20",
            temperature="96.8",
            temperature_unit="degF",
        )
    )
    below_boundary = EVALUATOR.evaluate(
        complete_case(
            600,
            heart_rate="75",
            respiratory_rate="20",
            temperature="96.79",
            temperature_unit="degF",
        )
    )

    assert (
        finding(at_boundary, "IITT-ADULT-HIGH-RISK-TEMPERATURE").rule_state
        == "not_met"
    )
    assert (
        finding(below_boundary, "IITT-ADULT-HIGH-RISK-TEMPERATURE").rule_state
        == "met"
    )


def test_invalid_units_naive_times_duplicate_and_future_times_are_rejected() -> None:
    base = observation("heart_rate", "75", "degC", "BAD")
    with pytest.raises(ValidationError, match="requires a supported unit"):
        EvaluateTriageInput(
            encounter_id=ENCOUNTER_ID,
            assessment_version_id=ASSESSMENT_ID,
            age_months=600,
            observations=[base],
            evaluated_at=EVALUATED_AT,
        )

    naive = observation(
        "heart_rate",
        "75",
        "bpm",
        "NAIVE",
        measured_at="2026-10-15T11:00:00",
    )
    with pytest.raises(ValidationError):
        EvaluateTriageInput(
            encounter_id=ENCOUNTER_ID,
            assessment_version_id=ASSESSMENT_ID,
            age_months=600,
            observations=[naive],
            evaluated_at=EVALUATED_AT,
        )

    valid = observation("heart_rate", "75", "bpm", "DUP")
    duplicate = {**valid, "observation_id": "SYN-OBS-DUP-2"}
    with pytest.raises(ValidationError, match="duplicate observation"):
        EvaluateTriageInput(
            encounter_id=ENCOUNTER_ID,
            assessment_version_id=ASSESSMENT_ID,
            age_months=600,
            observations=[valid, duplicate],
            evaluated_at=EVALUATED_AT,
        )

    duplicate_id_different_measurement = observation(
        "respiratory_rate", "18", "breaths/min", "DUP"
    )
    with pytest.raises(ValidationError, match="duplicate observation_id"):
        EvaluateTriageInput(
            encounter_id=ENCOUNTER_ID,
            assessment_version_id=ASSESSMENT_ID,
            age_months=600,
            observations=[valid, duplicate_id_different_measurement],
            evaluated_at=EVALUATED_AT,
        )

    future = observation(
        "heart_rate",
        "75",
        "bpm",
        "FUTURE",
        measured_at="2026-10-15T13:00:00Z",
    )
    with pytest.raises(ValidationError, match="measured after evaluated_at"):
        EvaluateTriageInput(
            encounter_id=ENCOUNTER_ID,
            assessment_version_id=ASSESSMENT_ID,
            age_months=600,
            observations=[future],
            evaluated_at=EVALUATED_AT,
        )

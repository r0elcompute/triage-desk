from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from triage_sentinel.data.enums import Measurement, ObservationSource, ReviewDecision
from triage_sentinel.data.models import ClinicianReview, ClinicianReviewRequest
from triage_sentinel.data.repositories import (
    AssessmentRepository,
    EncounterRepository,
    PatientRepository,
)
from triage_sentinel.data.schemas import (
    AssessmentVersionCreate,
    ClinicianReviewCreate,
    EncounterCreate,
    PatientCreate,
    VitalObservationCreate,
)
from triage_tools_mcp.service import TriageToolsService, ToolFailure


def _build_service(session_factory):
    return TriageToolsService(session_factory)


def _new_encounter(session):
    patient = PatientRepository(session).create(
        PatientCreate(synthetic_reference="SYN-PATIENT-REASSESSMENT")
    )
    encounter = EncounterRepository(session).create(
        patient.id, EncounterCreate(started_at=datetime.now(UTC))
    )
    assessment = AssessmentRepository(session).create_version(
        encounter.id,
        AssessmentVersionCreate(
            recommendation="Synthetic reassessment baseline",
            created_by_reference="SYN-CLINICIAN-REASSESSMENT",
        ),
    )
    return patient, encounter, assessment


def test_duplicate_observation_triggers_reassessment_review(db_session):
    service = _build_service(db_session.bind)
    _, encounter, assessment = _new_encounter(db_session)
    base = VitalObservationCreate(
        measurement=Measurement.HEART_RATE,
        value=72,
        unit="bpm",
        measured_at=datetime.now(UTC) - timedelta(minutes=10),
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-REASSESS-01",
    )
    EncounterRepository(db_session).add_vital_observation(encounter.id, base)

    duplicate = VitalObservationCreate(
        measurement=Measurement.HEART_RATE,
        value=72,
        unit="bpm",
        measured_at=base.measured_at,
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-REASSESS-01",
    )

    result = service.request_reassessment_review(
        encounter.id,
        duplicate,
        rule_reference="triage.protocol.reassessment.duplicate_observation",
    )

    assert result.status == "pending"
    assert result.request_id is not None
    assert result.rule_reference == "triage.protocol.reassessment.duplicate_observation"
    assert result.encounter_id == encounter.id
    assert result.assessment_version_id == assessment.id
    with db_session:
        review_request = db_session.scalar(
            select(ClinicianReviewRequest).where(
                ClinicianReviewRequest.id == result.request_id
            )
        )
    assert review_request is not None
    assert "duplicate_observation" in review_request.reason


def test_new_valid_observation_does_not_trigger_reassessment(db_session):
    service = _build_service(db_session.bind)
    _, encounter, _ = _new_encounter(db_session)
    first = VitalObservationCreate(
        measurement=Measurement.HEART_RATE,
        value=72,
        unit="bpm",
        measured_at=datetime.now(UTC) - timedelta(minutes=10),
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-REASSESS-02",
    )
    EncounterRepository(db_session).add_vital_observation(encounter.id, first)

    second = VitalObservationCreate(
        measurement=Measurement.OXYGEN_SATURATION,
        value=98,
        unit="%",
        measured_at=datetime.now(UTC),
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-REASSESS-03",
    )

    result = service.request_reassessment_review(
        encounter.id,
        second,
        rule_reference="triage.protocol.reassessment.duplicate_observation",
    )

    assert result.status == "not_triggered"
    assert result.request_id is None
    assert result.rule_reference is None


def test_stale_observation_triggers_reassessment_review(db_session):
    service = _build_service(db_session.bind)
    _, encounter, assessment = _new_encounter(db_session)
    current = datetime.now(UTC)
    EncounterRepository(db_session).add_vital_observation(
        encounter.id,
        VitalObservationCreate(
            measurement=Measurement.HEART_RATE,
            value=72,
            unit="bpm",
            measured_at=current - timedelta(minutes=10),
            source=ObservationSource.DEVICE,
            source_reference="SYN-DEVICE-REASSESS-04",
        ),
    )
    stale = VitalObservationCreate(
        measurement=Measurement.HEART_RATE,
        value=74,
        unit="bpm",
        measured_at=current - timedelta(hours=3),
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-REASSESS-05",
    )

    result = service.request_reassessment_review(
        encounter.id,
        stale,
        rule_reference="triage.protocol.reassessment.stale_observation",
    )

    assert result.status == "pending"
    assert result.rule_reference == "triage.protocol.reassessment.stale_observation"
    assert result.assessment_version_id == assessment.id


def test_invalid_observation_cannot_silently_affect_assessment(db_session):
    service = _build_service(db_session.bind)
    _, encounter, _ = _new_encounter(db_session)
    invalid = VitalObservationCreate(
        measurement=Measurement.HEART_RATE,
        value=72,
        unit="bpm",
        measured_at=datetime.now(UTC),
        source=ObservationSource.DEVICE,
        source_reference="REAL-PATIENT-REFERENCE",
    )

    with pytest.raises(ToolFailure, match="INVALID_OBSERVATION"):
        service.request_reassessment_review(
            encounter.id,
            invalid,
            rule_reference="triage.protocol.reassessment.invalid_observation",
        )

    with db_session:
        count = db_session.scalar(
            select(ClinicianReviewRequest.count())
        )
    assert count == 0


def test_clinician_decision_is_recorded_after_reassessment(db_session):
    service = _build_service(db_session.bind)
    _, encounter, assessment = _new_encounter(db_session)
    observation = VitalObservationCreate(
        measurement=Measurement.HEART_RATE,
        value=72,
        unit="bpm",
        measured_at=datetime.now(UTC),
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-REASSESS-06",
    )
    duplicate = VitalObservationCreate(
        measurement=Measurement.HEART_RATE,
        value=72,
        unit="bpm",
        measured_at=observation.measured_at,
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-REASSESS-06",
    )
    EncounterRepository(db_session).add_vital_observation(encounter.id, observation)

    request = service.request_reassessment_review(
        encounter.id,
        duplicate,
        rule_reference="triage.protocol.reassessment.duplicate_observation",
    )

    decision = AssessmentRepository(db_session).add_review(
        assessment.id,
        ClinicianReviewCreate(
            clinician_reference="SYN-CLINICIAN-REASSESSMENT",
            decision=ReviewDecision.ACCEPTED,
            override_summary=None,
            override_reason_code=None,
        ),
    )

    assert request.status == "pending"
    assert decision.assessment_version_id == assessment.id
    assert decision.decision == ReviewDecision.ACCEPTED.value
    with db_session:
        persisted = db_session.scalar(
            select(ClinicianReview).where(ClinicianReview.id == decision.id)
        )
    assert persisted is not None

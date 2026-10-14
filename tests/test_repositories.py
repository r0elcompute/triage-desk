from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import inspect, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from triage_sentinel.data.database import create_session_factory, session_scope
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
from triage_sentinel.data.models import (
    AssessmentEvidence,
    AssessmentVersion,
    Base,
    VitalObservation,
)
from triage_sentinel.data.repositories import (
    AssessmentRepository,
    EncounterRepository,
    EventRepository,
    PatientRepository,
)
from triage_sentinel.data.schemas import (
    AssessmentEvidenceCreate,
    AssessmentEvidenceRead,
    AssessmentVersionCreate,
    AssessmentVersionRead,
    AuditLogCreate,
    AuditLogRead,
    ClinicianReviewCreate,
    ClinicianReviewRead,
    EncounterCreate,
    EncounterRead,
    PatientCreate,
    PatientRead,
    PresentingComplaintCreate,
    PresentingComplaintRead,
    QueueEventCreate,
    QueueEventRead,
    ToolExecutionCreate,
    ToolExecutionRead,
    VitalObservationCreate,
    VitalObservationRead,
)


def synthetic_reference(kind: str) -> str:
    return f"SYN-{kind}-{uuid4().hex.upper()}"


def create_patient_and_encounter(session: Session):
    patient = PatientRepository(session).create(
        PatientCreate(synthetic_reference=synthetic_reference("PATIENT"))
    )
    encounter = EncounterRepository(session).create(patient.id, EncounterCreate())
    return patient, encounter


def vital_input(
    *,
    measurement: Measurement = Measurement.HEART_RATE,
    value: Decimal = Decimal(72),
    unit: Unit = Unit.BEATS_PER_MINUTE,
    measured_at: datetime | None = None,
) -> VitalObservationCreate:
    return VitalObservationCreate(
        measurement=measurement,
        value=value,
        unit=unit,
        measured_at=measured_at or datetime.now(UTC),
        source=ObservationSource.DEVICE,
        source_reference="SYN-DEVICE-01",
    )


def test_migration_creates_required_tables(migrated_test_engine) -> None:
    tables = set(inspect(migrated_test_engine).get_table_names())

    assert {
        "patients",
        "triage_encounters",
        "presenting_complaints",
        "vital_observations",
        "assessment_versions",
        "assessment_evidence",
        "clinician_reviews",
        "queue_events",
        "tool_executions",
        "audit_logs",
        "alembic_version",
    } <= tables


def test_migrated_schema_matches_sqlalchemy_models(migrated_test_engine) -> None:
    with migrated_test_engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        differences = compare_metadata(migration_context, Base.metadata)

    assert differences == []


def test_patient_encounter_and_observations_are_associated(db_session: Session) -> None:
    patients = PatientRepository(db_session)
    encounters = EncounterRepository(db_session)
    patient, encounter = create_patient_and_encounter(db_session)
    complaint = encounters.add_complaint(
        encounter.id,
        PresentingComplaintCreate(
            concept_code="SYN-COMPLAINT-1",
            summary="Synthetic complaint summary",
        ),
    )
    observation = encounters.add_vital_observation(
        encounter.id,
        vital_input(),
    )

    retrieved_patient = patients.get(patient.id)
    retrieved_encounter = encounters.get(encounter.id)

    assert retrieved_patient is not None
    assert retrieved_patient.synthetic_reference == patient.synthetic_reference
    assert retrieved_encounter is not None
    assert retrieved_encounter.patient_id == patient.id
    assert retrieved_encounter.patient.id == patient.id
    assert retrieved_encounter.complaints[0].id == complaint.id
    assert retrieved_encounter.vital_observations[0].id == observation.id
    assert observation.value == Decimal("72.0000")
    assert observation.measured_at.tzinfo is not None
    assert PatientRead.model_validate(retrieved_patient).id == patient.id
    assert EncounterRead.model_validate(retrieved_encounter).patient_id == patient.id
    assert PresentingComplaintRead.model_validate(complaint).id == complaint.id
    assert VitalObservationRead.model_validate(observation).id == observation.id


def test_repository_records_persist_across_sessions(migrated_test_engine) -> None:
    factory = create_session_factory(migrated_test_engine)
    with session_scope(factory) as session:
        patient, encounter = create_patient_and_encounter(session)
        patient_id = patient.id
        encounter_id = encounter.id

    with factory() as session:
        persisted_patient = PatientRepository(session).get(patient_id)
        persisted_encounter = EncounterRepository(session).get(encounter_id)

    assert persisted_patient is not None
    assert persisted_encounter is not None
    assert persisted_encounter.patient_id == persisted_patient.id


def test_assessment_versions_evidence_and_reviews_are_append_only(
    db_session: Session,
) -> None:
    _, encounter = create_patient_and_encounter(db_session)
    encounters = EncounterRepository(db_session)
    assessments = AssessmentRepository(db_session)
    complaint = encounters.add_complaint(
        encounter.id,
        PresentingComplaintCreate(
            concept_code="SYN-COMPLAINT-2",
            summary="Synthetic complaint summary",
        ),
    )
    observation = encounters.add_vital_observation(encounter.id, vital_input())

    first = assessments.create_version(
        encounter.id,
        AssessmentVersionCreate(
            recommendation="Synthetic recommendation version one",
            created_by_reference=synthetic_reference("SYSTEM"),
        ),
    )
    evidence = assessments.add_evidence(
        first.id,
        AssessmentEvidenceCreate(
            kind=EvidenceKind.PRESENTING_COMPLAINT,
            complaint_id=complaint.id,
        ),
    )
    assessments.add_evidence(
        first.id,
        AssessmentEvidenceCreate(
            kind=EvidenceKind.VITAL_OBSERVATION,
            vital_observation_id=observation.id,
        ),
    )
    review = assessments.add_review(
        first.id,
        ClinicianReviewCreate(
            clinician_reference=synthetic_reference("CLINICIAN"),
            decision=ReviewDecision.OVERRIDDEN,
            override_summary="Synthetic reviewer override",
            override_reason_code="SYN-REASON-1",
        ),
    )
    second = assessments.create_version(
        encounter.id,
        AssessmentVersionCreate(
            recommendation="Synthetic recommendation version two",
            created_by_reference=synthetic_reference("SYSTEM"),
        ),
    )

    versions = assessments.list_versions(encounter.id)
    loaded_first = assessments.get_version(first.id)
    evidence_rows = assessments.list_evidence(first.id)
    reviews = assessments.list_reviews(first.id)

    assert [(row.version, row.recommendation) for row in versions] == [
        (1, "Synthetic recommendation version one"),
        (2, "Synthetic recommendation version two"),
    ]
    assert loaded_first is not None
    assert loaded_first.recommendation == "Synthetic recommendation version one"
    assert len(evidence_rows) == 2
    assert evidence.id in {row.id for row in evidence_rows}
    assert AssessmentVersionRead.model_validate(loaded_first).version == 1
    assert all(
        AssessmentEvidenceRead.model_validate(row).assessment_version_id == first.id
        for row in evidence_rows
    )
    assert reviews[0].id == review.id
    assert review.override_reason_code == "SYN-REASON-1"
    assert (
        ClinicianReviewRead.model_validate(review).decision == ReviewDecision.OVERRIDDEN
    )

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            update(AssessmentVersion)
            .where(AssessmentVersion.id == first.id)
            .values(recommendation="Replaced recommendation")
        )

    assert second.version == 2


def test_evidence_cannot_cross_encounter_boundaries(db_session: Session) -> None:
    _, first_encounter = create_patient_and_encounter(db_session)
    _, other_encounter = create_patient_and_encounter(db_session)
    encounters = EncounterRepository(db_session)
    assessments = AssessmentRepository(db_session)
    complaint = encounters.add_complaint(
        other_encounter.id,
        PresentingComplaintCreate(
            concept_code="SYN-COMPLAINT-3",
            summary="Synthetic complaint summary",
        ),
    )
    assessment = assessments.create_version(
        first_encounter.id,
        AssessmentVersionCreate(
            recommendation="Synthetic recommendation",
            created_by_reference=synthetic_reference("SYSTEM"),
        ),
    )

    with pytest.raises(ValueError, match="same encounter"):
        assessments.add_evidence(
            assessment.id,
            AssessmentEvidenceCreate(
                kind=EvidenceKind.PRESENTING_COMPLAINT,
                complaint_id=complaint.id,
            ),
        )

    invalid_evidence = AssessmentEvidence(
        assessment_version_id=assessment.id,
        encounter_id=first_encounter.id,
        kind=EvidenceKind.PRESENTING_COMPLAINT.value,
        complaint_id=complaint.id,
    )
    db_session.add(invalid_evidence)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_queue_tool_and_audit_records_can_be_created_and_retrieved(
    db_session: Session,
) -> None:
    _, encounter = create_patient_and_encounter(db_session)
    events = EventRepository(db_session)
    event = events.add_queue_event(
        encounter.id,
        QueueEventCreate(
            queue_name="SYN-QUEUE-1",
            event_type=QueueEventType.ENTERED,
            actor_reference=synthetic_reference("ACTOR"),
        ),
    )
    started_at = datetime.now(UTC)
    execution = events.add_tool_execution(
        ToolExecutionCreate(
            tool_name="synthetic.lookup",
            status=ToolExecutionStatus.SUCCEEDED,
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=1),
            input_digest="a" * 64,
            output_digest="b" * 64,
        )
    )
    audit = events.add_audit_log(
        AuditLogCreate(
            entity_type=AuditEntityType.ENCOUNTER,
            entity_id=encounter.id,
            action="synthetic.created",
            actor_reference=synthetic_reference("ACTOR"),
        )
    )

    assert [row.id for row in events.list_queue_events(encounter.id)] == [event.id]
    assert events.get_tool_execution(execution.id) is not None
    audit_rows = events.list_audit_logs("encounter", encounter.id)
    assert [row.id for row in audit_rows] == [audit.id]
    assert QueueEventRead.model_validate(event).encounter_id == encounter.id
    assert ToolExecutionRead.model_validate(execution).id == execution.id
    assert AuditLogRead.model_validate(audit).entity_id == encounter.id


@pytest.mark.parametrize(
    "data",
    [
        {"synthetic_reference": "PATIENT-REAL"},
        {"synthetic_reference": "SYN-x-invalid"},
        {"synthetic_reference": "SYN-OK", "unexpected": "value"},
    ],
)
def test_patient_validation_rejects_non_synthetic_or_extra_data(data) -> None:
    with pytest.raises(ValidationError):
        PatientCreate.model_validate(data)


def test_vital_validation_rejects_invalid_units_values_and_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="not a supported unit"):
        vital_input(unit=Unit.MILLIMETERS_OF_MERCURY)
    with pytest.raises(ValidationError, match="finite"):
        vital_input(value=Decimal("NaN"))
    with pytest.raises(ValidationError, match="timezone"):
        vital_input(measured_at=datetime(2026, 1, 1))


def test_vital_database_constraint_rejects_invalid_unit(db_session: Session) -> None:
    _, encounter = create_patient_and_encounter(db_session)
    invalid_observation = VitalObservation(
        encounter_id=encounter.id,
        measurement=Measurement.HEART_RATE.value,
        value=Decimal(72),
        unit=Unit.MILLIMETERS_OF_MERCURY.value,
        measured_at=datetime.now(UTC),
        source=ObservationSource.DEVICE.value,
    )
    db_session.add(invalid_observation)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_timestamp_override_and_evidence_validation() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        EncounterCreate(started_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError, match="closed_at"):
        EncounterCreate(status=EncounterStatus.CLOSED)
    with pytest.raises(ValidationError, match="exactly one"):
        AssessmentEvidenceCreate(kind=EvidenceKind.VITAL_OBSERVATION)
    with pytest.raises(ValidationError, match="override"):
        ClinicianReviewCreate(
            clinician_reference=synthetic_reference("CLINICIAN"),
            decision=ReviewDecision.OVERRIDDEN,
        )


def test_tool_execution_validation_requires_ordered_timestamps_and_digest() -> None:
    started_at = datetime.now(UTC)
    with pytest.raises(ValidationError, match="at or after"):
        ToolExecutionCreate(
            tool_name="synthetic.tool",
            status=ToolExecutionStatus.SUCCEEDED,
            started_at=started_at,
            completed_at=started_at - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="64"):
        ToolExecutionCreate(
            tool_name="synthetic.tool",
            status=ToolExecutionStatus.SUCCEEDED,
            started_at=started_at,
            completed_at=started_at,
            input_digest="not-a-digest",
        )
    with pytest.raises(ValidationError, match="error_code"):
        ToolExecutionCreate(
            tool_name="synthetic.tool",
            status=ToolExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=started_at,
        )

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from triage_sentinel.data.enums import EvidenceKind
from triage_sentinel.data.models import (
    AssessmentEvidence,
    AssessmentVersion,
    AuditLog,
    ClinicianReview,
    Patient,
    PresentingComplaint,
    QueueEvent,
    ToolExecution,
    TriageEncounter,
    VitalObservation,
)
from triage_sentinel.data.schemas import (
    AssessmentEvidenceCreate,
    AssessmentVersionCreate,
    AuditLogCreate,
    ClinicianReviewCreate,
    EncounterCreate,
    PatientCreate,
    PresentingComplaintCreate,
    QueueEventCreate,
    ToolExecutionCreate,
    VitalObservationCreate,
)


class PatientRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, data: PatientCreate) -> Patient:
        patient = Patient(synthetic_reference=data.synthetic_reference)
        self.session.add(patient)
        self.session.flush()
        return patient

    def get(self, patient_id: UUID) -> Patient | None:
        return self.session.get(Patient, patient_id)


class EncounterRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, patient_id: UUID, data: EncounterCreate) -> TriageEncounter:
        encounter = TriageEncounter(
            patient_id=patient_id,
            started_at=data.started_at,
            status=data.status.value,
            closed_at=data.closed_at,
        )
        self.session.add(encounter)
        self.session.flush()
        return encounter

    def get(self, encounter_id: UUID) -> TriageEncounter | None:
        statement = (
            select(TriageEncounter)
            .where(TriageEncounter.id == encounter_id)
            .options(
                selectinload(TriageEncounter.patient),
                selectinload(TriageEncounter.complaints),
                selectinload(TriageEncounter.vital_observations),
            )
        )
        return self.session.scalar(statement)

    def add_complaint(
        self, encounter_id: UUID, data: PresentingComplaintCreate
    ) -> PresentingComplaint:
        complaint = PresentingComplaint(
            encounter_id=encounter_id,
            concept_code=data.concept_code,
            summary=data.summary,
        )
        self.session.add(complaint)
        self.session.flush()
        return complaint

    def get_complaint(self, complaint_id: UUID) -> PresentingComplaint | None:
        return self.session.get(PresentingComplaint, complaint_id)

    def list_complaints(self, encounter_id: UUID) -> Sequence[PresentingComplaint]:
        statement = (
            select(PresentingComplaint)
            .where(PresentingComplaint.encounter_id == encounter_id)
            .order_by(PresentingComplaint.created_at, PresentingComplaint.id)
        )
        return self.session.scalars(statement).all()

    def add_vital_observation(
        self, encounter_id: UUID, data: VitalObservationCreate
    ) -> VitalObservation:
        observation = VitalObservation(
            encounter_id=encounter_id,
            measurement=data.measurement.value,
            value=data.value,
            unit=data.unit.value,
            measured_at=data.measured_at,
            source=data.source.value,
            source_reference=data.source_reference,
        )
        self.session.add(observation)
        self.session.flush()
        return observation

    def get_vital_observation(self, observation_id: UUID) -> VitalObservation | None:
        return self.session.get(VitalObservation, observation_id)

    def list_vital_observations(self, encounter_id: UUID) -> Sequence[VitalObservation]:
        statement = (
            select(VitalObservation)
            .where(VitalObservation.encounter_id == encounter_id)
            .order_by(VitalObservation.measured_at, VitalObservation.id)
        )
        return self.session.scalars(statement).all()


class AssessmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_version(
        self, encounter_id: UUID, data: AssessmentVersionCreate
    ) -> AssessmentVersion:
        encounter = self.session.scalar(
            select(TriageEncounter)
            .where(TriageEncounter.id == encounter_id)
            .with_for_update()
        )
        if encounter is None:
            raise ValueError(f"Encounter {encounter_id} does not exist")
        current_version = self.session.scalar(
            select(func.max(AssessmentVersion.version)).where(
                AssessmentVersion.encounter_id == encounter_id
            )
        )
        assessment = AssessmentVersion(
            encounter_id=encounter_id,
            version=(current_version or 0) + 1,
            recommendation=data.recommendation,
            created_by_reference=data.created_by_reference,
        )
        self.session.add(assessment)
        self.session.flush()
        return assessment

    def get_version(self, assessment_id: UUID) -> AssessmentVersion | None:
        statement = (
            select(AssessmentVersion)
            .where(AssessmentVersion.id == assessment_id)
            .options(
                selectinload(AssessmentVersion.evidence),
                selectinload(AssessmentVersion.reviews),
            )
        )
        return self.session.scalar(statement)

    def list_versions(self, encounter_id: UUID) -> Sequence[AssessmentVersion]:
        statement = (
            select(AssessmentVersion)
            .where(AssessmentVersion.encounter_id == encounter_id)
            .options(selectinload(AssessmentVersion.evidence))
            .order_by(AssessmentVersion.version)
        )
        return self.session.scalars(statement).all()

    def add_evidence(
        self, assessment_id: UUID, data: AssessmentEvidenceCreate
    ) -> AssessmentEvidence:
        assessment = self.session.get(AssessmentVersion, assessment_id)
        if assessment is None:
            raise ValueError(f"Assessment version {assessment_id} does not exist")
        if data.kind == EvidenceKind.PRESENTING_COMPLAINT:
            source = self.session.scalar(
                select(PresentingComplaint).where(
                    PresentingComplaint.id == data.complaint_id,
                    PresentingComplaint.encounter_id == assessment.encounter_id,
                )
            )
            if source is None:
                raise ValueError("Complaint evidence must belong to the same encounter")
        else:
            source = self.session.scalar(
                select(VitalObservation).where(
                    VitalObservation.id == data.vital_observation_id,
                    VitalObservation.encounter_id == assessment.encounter_id,
                )
            )
            if source is None:
                raise ValueError("Vital evidence must belong to the same encounter")
        evidence = AssessmentEvidence(
            assessment_version_id=assessment_id,
            encounter_id=assessment.encounter_id,
            kind=data.kind.value,
            complaint_id=data.complaint_id,
            vital_observation_id=data.vital_observation_id,
        )
        self.session.add(evidence)
        self.session.flush()
        return evidence

    def list_evidence(self, assessment_id: UUID) -> Sequence[AssessmentEvidence]:
        statement = (
            select(AssessmentEvidence)
            .where(AssessmentEvidence.assessment_version_id == assessment_id)
            .order_by(AssessmentEvidence.created_at, AssessmentEvidence.id)
        )
        return self.session.scalars(statement).all()

    def add_review(
        self, assessment_id: UUID, data: ClinicianReviewCreate
    ) -> ClinicianReview:
        if self.session.get(AssessmentVersion, assessment_id) is None:
            raise ValueError(f"Assessment version {assessment_id} does not exist")
        review = ClinicianReview(
            assessment_version_id=assessment_id,
            clinician_reference=data.clinician_reference,
            decision=data.decision.value,
            reviewed_at=data.reviewed_at,
            override_summary=data.override_summary,
            override_reason_code=data.override_reason_code,
        )
        self.session.add(review)
        self.session.flush()
        return review

    def list_reviews(self, assessment_id: UUID) -> Sequence[ClinicianReview]:
        statement = (
            select(ClinicianReview)
            .where(ClinicianReview.assessment_version_id == assessment_id)
            .order_by(ClinicianReview.reviewed_at, ClinicianReview.id)
        )
        return self.session.scalars(statement).all()


class EventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_queue_event(self, encounter_id: UUID, data: QueueEventCreate) -> QueueEvent:
        event = QueueEvent(
            encounter_id=encounter_id,
            queue_name=data.queue_name,
            event_type=data.event_type.value,
            occurred_at=data.occurred_at,
            actor_reference=data.actor_reference,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_queue_events(self, encounter_id: UUID) -> Sequence[QueueEvent]:
        statement = (
            select(QueueEvent)
            .where(QueueEvent.encounter_id == encounter_id)
            .order_by(QueueEvent.occurred_at, QueueEvent.id)
        )
        return self.session.scalars(statement).all()

    def add_tool_execution(self, data: ToolExecutionCreate) -> ToolExecution:
        execution = ToolExecution(
            tool_name=data.tool_name,
            status=data.status.value,
            started_at=data.started_at,
            completed_at=data.completed_at,
            input_digest=data.input_digest,
            output_digest=data.output_digest,
            error_code=data.error_code,
        )
        self.session.add(execution)
        self.session.flush()
        return execution

    def get_tool_execution(self, execution_id: UUID) -> ToolExecution | None:
        return self.session.get(ToolExecution, execution_id)

    def add_audit_log(self, data: AuditLogCreate) -> AuditLog:
        log = AuditLog(
            entity_type=data.entity_type.value,
            entity_id=data.entity_id,
            action=data.action,
            actor_reference=data.actor_reference,
            occurred_at=data.occurred_at,
        )
        self.session.add(log)
        self.session.flush()
        return log

    def list_audit_logs(self, entity_type: str, entity_id: UUID) -> Sequence[AuditLog]:
        statement = (
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(AuditLog.occurred_at, AuditLog.id)
        )
        return self.session.scalars(statement).all()

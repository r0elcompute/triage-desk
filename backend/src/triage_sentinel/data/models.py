from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, foreign, mapped_column, relationship

from triage_sentinel.data.enums import EncounterStatus


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Patient(Base, TimestampMixin):
    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint(
            "synthetic_reference LIKE 'SYN-%'",
            name="ck_patients_synthetic_reference",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    synthetic_reference: Mapped[str] = mapped_column(
        String(68), nullable=False, unique=True
    )
    encounters: Mapped[list["TriageEncounter"]] = relationship(
        back_populates="patient", passive_deletes=True
    )


class TriageEncounter(Base, TimestampMixin):
    __tablename__ = "triage_encounters"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closed', 'void')", name="ck_encounters_status"
        ),
        CheckConstraint(
            "closed_at IS NULL OR closed_at >= started_at",
            name="ck_encounters_closed_after_start",
        ),
        CheckConstraint(
            "(status = 'closed' AND closed_at IS NOT NULL) OR "
            "(status != 'closed' AND closed_at IS NULL)",
            name="ck_encounters_closed_timestamp",
        ),
        Index("ix_encounters_patient_started", "patient_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    patient_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EncounterStatus.OPEN.value
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    patient: Mapped[Patient] = relationship(back_populates="encounters")
    complaints: Mapped[list["PresentingComplaint"]] = relationship(
        back_populates="encounter", passive_deletes=True
    )
    vital_observations: Mapped[list["VitalObservation"]] = relationship(
        back_populates="encounter", passive_deletes=True
    )
    assessment_versions: Mapped[list["AssessmentVersion"]] = relationship(
        back_populates="encounter", passive_deletes=True
    )
    queue_events: Mapped[list["QueueEvent"]] = relationship(
        back_populates="encounter", passive_deletes=True
    )


class PresentingComplaint(Base, TimestampMixin):
    __tablename__ = "presenting_complaints"
    __table_args__ = (
        CheckConstraint("length(concept_code) > 0", name="ck_complaints_code_nonempty"),
        CheckConstraint("length(summary) > 0", name="ck_complaints_summary_nonempty"),
        CheckConstraint(
            "concept_code ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_complaints_code_format",
        ),
        UniqueConstraint("id", "encounter_id", name="uq_complaints_id_encounter"),
        Index("ix_complaints_encounter", "encounter_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    encounter_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("triage_encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    concept_code: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)

    encounter: Mapped[TriageEncounter] = relationship(back_populates="complaints")
    assessment_evidence: Mapped[list["AssessmentEvidence"]] = relationship(
        back_populates="complaint",
        primaryjoin=lambda: and_(
            PresentingComplaint.id == foreign(AssessmentEvidence.complaint_id),
            PresentingComplaint.encounter_id == AssessmentEvidence.encounter_id,
        ),
        foreign_keys=lambda: [AssessmentEvidence.complaint_id],
        passive_deletes=True,
    )


class VitalObservation(Base, TimestampMixin):
    __tablename__ = "vital_observations"
    __table_args__ = (
        CheckConstraint(
            "(measurement = 'heart_rate' AND unit = 'bpm') OR "
            "(measurement = 'respiratory_rate' AND unit = 'breaths/min') OR "
            "(measurement = 'temperature' AND unit IN ('degC', 'degF')) OR "
            "(measurement IN ('systolic_blood_pressure', 'diastolic_blood_pressure') "
            "AND unit = 'mmHg') OR "
            "(measurement = 'oxygen_saturation' AND unit = '%') OR "
            "(measurement = 'body_weight' AND unit = 'kg') OR "
            "(measurement = 'body_height' AND unit = 'cm') OR "
            "(measurement = 'blood_glucose' AND unit = 'mmol/L')",
            name="ck_vitals_unit_matches_measurement",
        ),
        CheckConstraint(
            "value NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_vitals_value_finite",
        ),
        CheckConstraint(
            "source IN ('clinician', 'device', 'patient_reported', 'imported')",
            name="ck_vitals_source",
        ),
        CheckConstraint(
            "source_reference IS NULL OR source_reference LIKE 'SYN-%'",
            name="ck_vitals_source_reference_synthetic",
        ),
        UniqueConstraint("id", "encounter_id", name="uq_vitals_id_encounter"),
        Index("ix_vitals_encounter_measured", "encounter_id", "measured_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    encounter_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("triage_encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    measurement: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(68))

    encounter: Mapped[TriageEncounter] = relationship(
        back_populates="vital_observations"
    )
    assessment_evidence: Mapped[list["AssessmentEvidence"]] = relationship(
        back_populates="vital_observation",
        primaryjoin=lambda: and_(
            VitalObservation.id == foreign(AssessmentEvidence.vital_observation_id),
            VitalObservation.encounter_id == AssessmentEvidence.encounter_id,
        ),
        foreign_keys=lambda: [AssessmentEvidence.vital_observation_id],
        passive_deletes=True,
    )


class AssessmentVersion(Base, TimestampMixin):
    __tablename__ = "assessment_versions"
    __table_args__ = (
        CheckConstraint(
            "requested_by_reference LIKE 'SYN-%'",
            name="ck_review_request_actor_synthetic",
        ),
        CheckConstraint("length(reason) > 0", name="ck_review_request_reason_nonempty"),
        UniqueConstraint(
            "encounter_id", "version", name="uq_assessment_encounter_version"
        ),
        UniqueConstraint("id", "encounter_id", name="uq_assessments_id_encounter"),
        CheckConstraint("version > 0", name="ck_assessment_version_positive"),
        CheckConstraint(
            "length(recommendation) > 0", name="ck_assessment_recommendation_nonempty"
        ),
        CheckConstraint(
            "created_by_reference LIKE 'SYN-%'",
            name="ck_assessment_creator_synthetic",
        ),
        Index("ix_assessments_encounter_created", "encounter_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    encounter_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("triage_encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_reference: Mapped[str] = mapped_column(String(68), nullable=False)

    encounter: Mapped[TriageEncounter] = relationship(
        back_populates="assessment_versions"
    )
    evidence: Mapped[list["AssessmentEvidence"]] = relationship(
        back_populates="assessment_version",
        primaryjoin=lambda: and_(
            AssessmentVersion.id == foreign(AssessmentEvidence.assessment_version_id),
            AssessmentVersion.encounter_id == AssessmentEvidence.encounter_id,
        ),
        foreign_keys=lambda: [AssessmentEvidence.assessment_version_id],
        passive_deletes=True,
        order_by="AssessmentEvidence.created_at",
    )
    reviews: Mapped[list["ClinicianReview"]] = relationship(
        back_populates="assessment_version",
        passive_deletes=True,
        order_by="ClinicianReview.reviewed_at",
    )
    review_requests: Mapped[list["ClinicianReviewRequest"]] = relationship(
        back_populates="assessment_version", passive_deletes=True
    )


class AssessmentEvidence(Base, TimestampMixin):
    __tablename__ = "assessment_evidence"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'presenting_complaint' AND complaint_id IS NOT NULL "
            "AND vital_observation_id IS NULL) OR "
            "(kind = 'vital_observation' AND vital_observation_id IS NOT NULL "
            "AND complaint_id IS NULL)",
            name="ck_evidence_kind_matches_source",
        ),
        ForeignKeyConstraint(
            ["assessment_version_id", "encounter_id"],
            ["assessment_versions.id", "assessment_versions.encounter_id"],
            ondelete="RESTRICT",
            name="fk_evidence_assessment_encounter",
        ),
        ForeignKeyConstraint(
            ["complaint_id", "encounter_id"],
            ["presenting_complaints.id", "presenting_complaints.encounter_id"],
            ondelete="RESTRICT",
            name="fk_evidence_complaint_encounter",
        ),
        ForeignKeyConstraint(
            ["vital_observation_id", "encounter_id"],
            ["vital_observations.id", "vital_observations.encounter_id"],
            ondelete="RESTRICT",
            name="fk_evidence_vital_encounter",
        ),
        UniqueConstraint(
            "assessment_version_id",
            "complaint_id",
            name="uq_evidence_assessment_complaint",
        ),
        UniqueConstraint(
            "assessment_version_id",
            "vital_observation_id",
            name="uq_evidence_assessment_vital",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    assessment_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    encounter_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    complaint_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    vital_observation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))

    assessment_version: Mapped[AssessmentVersion] = relationship(
        back_populates="evidence",
        primaryjoin=lambda: and_(
            AssessmentVersion.id == foreign(AssessmentEvidence.assessment_version_id),
            AssessmentVersion.encounter_id == AssessmentEvidence.encounter_id,
        ),
        foreign_keys=[assessment_version_id],
    )
    complaint: Mapped[PresentingComplaint | None] = relationship(
        back_populates="assessment_evidence",
        primaryjoin=lambda: and_(
            PresentingComplaint.id == foreign(AssessmentEvidence.complaint_id),
            PresentingComplaint.encounter_id == AssessmentEvidence.encounter_id,
        ),
        foreign_keys=[complaint_id],
    )
    vital_observation: Mapped[VitalObservation | None] = relationship(
        back_populates="assessment_evidence",
        primaryjoin=lambda: and_(
            VitalObservation.id == foreign(AssessmentEvidence.vital_observation_id),
            VitalObservation.encounter_id == AssessmentEvidence.encounter_id,
        ),
        foreign_keys=[vital_observation_id],
    )


class ClinicianReview(Base, TimestampMixin):
    __tablename__ = "clinician_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('accepted', 'rejected', 'overridden')",
            name="ck_reviews_decision",
        ),
        CheckConstraint(
            "(decision = 'overridden' AND override_summary IS NOT NULL "
            "AND override_reason_code IS NOT NULL) OR "
            "(decision != 'overridden' AND override_summary IS NULL "
            "AND override_reason_code IS NULL)",
            name="ck_reviews_override_fields",
        ),
        CheckConstraint(
            "clinician_reference LIKE 'SYN-%'",
            name="ck_reviews_clinician_synthetic",
        ),
        CheckConstraint(
            "override_summary IS NULL OR length(override_summary) > 0",
            name="ck_reviews_override_summary_nonempty",
        ),
        CheckConstraint(
            "override_reason_code IS NULL OR "
            "override_reason_code ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_reviews_override_reason_format",
        ),
        Index("ix_reviews_assessment_reviewed", "assessment_version_id", "reviewed_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    assessment_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assessment_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    clinician_reference: Mapped[str] = mapped_column(String(68), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    override_summary: Mapped[str | None] = mapped_column(String(2000))
    override_reason_code: Mapped[str | None] = mapped_column(String(64))

    assessment_version: Mapped[AssessmentVersion] = relationship(
        back_populates="reviews"
    )


class ClinicianReviewRequest(Base):
    __tablename__ = "clinician_review_requests"
    __table_args__ = (
        UniqueConstraint(
            "id", "assessment_version_id", name="uq_review_requests_id_assessment"
        ),
        ForeignKeyConstraint(
            ["assessment_version_id", "encounter_id"],
            ["assessment_versions.id", "assessment_versions.encounter_id"],
            ondelete="RESTRICT",
            name="fk_review_requests_assessment_encounter",
        ),
        Index(
            "ix_review_requests_assessment_created",
            "assessment_version_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    assessment_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    encounter_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    requested_by_reference: Mapped[str] = mapped_column(String(68), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    assessment_version: Mapped[AssessmentVersion] = relationship(
        back_populates="review_requests"
    )

    @classmethod
    def count(cls):
        return func.count(cls.id)


class ClinicianApproval(Base):
    __tablename__ = "clinician_approvals"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_clinician_approvals_request"),
        UniqueConstraint(
            "id", "assessment_version_id", name="uq_approvals_id_assessment"
        ),
        ForeignKeyConstraint(
            ["request_id", "assessment_version_id"],
            [
                "clinician_review_requests.id",
                "clinician_review_requests.assessment_version_id",
            ],
            ondelete="RESTRICT",
            name="fk_approvals_request_assessment",
        ),
        CheckConstraint(
            "assessment_number > 0", name="ck_approval_assessment_number_positive"
        ),
        CheckConstraint(
            "length(clinician_subject) > 0",
            name="ck_approval_subject_nonempty",
        ),
        CheckConstraint(
            "clinician_reference LIKE 'SYN-%'",
            name="ck_approval_clinician_synthetic",
        ),
        Index(
            "ix_approvals_assessment_created",
            "assessment_version_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    assessment_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    assessment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    clinician_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    clinician_reference: Mapped[str] = mapped_column(String(68), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClinicianApprovalRevocation(Base):
    __tablename__ = "clinician_approval_revocations"
    __table_args__ = (
        CheckConstraint(
            "revoked_by_reference LIKE 'SYN-%'",
            name="ck_approval_revoker_synthetic",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    approval_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("clinician_approvals.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    revoked_by_reference: Mapped[str] = mapped_column(String(68), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class QueueCommit(Base):
    __tablename__ = "queue_commits"
    __table_args__ = (
        CheckConstraint(
            "assessment_number > 0", name="ck_queue_commit_assessment_positive"
        ),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 16 AND 128",
            name="ck_queue_commit_key_length",
        ),
        CheckConstraint(
            "queue_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_queue_commit_name_format",
        ),
        CheckConstraint(
            "committed_by_reference LIKE 'SYN-%'",
            name="ck_queue_commit_actor_synthetic",
        ),
        CheckConstraint(
            "length(committed_by_subject) > 0",
            name="ck_queue_commit_subject_nonempty",
        ),
        UniqueConstraint("idempotency_key", name="uq_queue_commits_idempotency"),
        UniqueConstraint("approval_id", name="uq_queue_commits_approval"),
        UniqueConstraint("queue_event_id", name="uq_queue_commits_event"),
        ForeignKeyConstraint(
            ["approval_id", "assessment_version_id"],
            ["clinician_approvals.id", "clinician_approvals.assessment_version_id"],
            ondelete="RESTRICT",
            name="fk_queue_commits_approval_assessment",
        ),
        Index(
            "ix_queue_commits_assessment_created",
            "assessment_version_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    assessment_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    assessment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    queue_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("queue_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_by_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    committed_by_reference: Mapped[str] = mapped_column(String(68), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review', 'failed', 'committed')",
            name="ck_workflow_executions_status",
        ),
        CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_input_digest_sha256",
        ),
        CheckConstraint(
            "result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_result_digest_sha256",
        ),
        CheckConstraint(
            "completed_at >= started_at",
            name="ck_workflow_execution_time_order",
        ),
        ForeignKeyConstraint(
            ["assessment_version_id", "encounter_id"],
            ["assessment_versions.id", "assessment_versions.encounter_id"],
            ondelete="RESTRICT",
            name="fk_workflows_assessment_encounter",
        ),
        ForeignKeyConstraint(
            ["review_request_id"],
            ["clinician_review_requests.id"],
            ondelete="RESTRICT",
            name="fk_workflows_review_request",
        ),
        ForeignKeyConstraint(
            ["commit_action_id"],
            ["tool_executions.id"],
            ondelete="RESTRICT",
            name="fk_workflows_commit_action",
        ),
        Index("ix_workflows_assessment_created", "assessment_version_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    encounter_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assessment_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    model_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    transitions: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    reference_retrieval: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    protocol_reference: Mapped[dict[str, object] | None] = mapped_column(JSON)
    validation_result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    validation_valid: Mapped[bool] = mapped_column(nullable=False)
    validation_complete: Mapped[bool] = mapped_column(nullable=False)
    evaluation_result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    evaluation_action_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    recommendation_result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    recommendation_valid: Mapped[bool] = mapped_column(nullable=False)
    review_request_result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    review_request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    commit_action_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QueueEvent(Base, TimestampMixin):
    __tablename__ = "queue_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('entered', 'claimed', 'released', 'removed')",
            name="ck_queue_events_type",
        ),
        CheckConstraint(
            "actor_reference IS NULL OR actor_reference LIKE 'SYN-%'",
            name="ck_queue_actor_synthetic",
        ),
        CheckConstraint(
            "queue_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_queue_name_format",
        ),
        Index("ix_queue_events_encounter_occurred", "encounter_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    encounter_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("triage_encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    actor_reference: Mapped[str | None] = mapped_column(String(68))

    encounter: Mapped[TriageEncounter] = relationship(back_populates="queue_events")


class ToolExecution(Base, TimestampMixin):
    __tablename__ = "tool_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'cancelled')",
            name="ck_tool_executions_status",
        ),
        CheckConstraint(
            "completed_at >= started_at", name="ck_tool_execution_time_order"
        ),
        CheckConstraint(
            "input_digest IS NULL OR input_digest ~ '^[0-9a-f]{64}$'",
            name="ck_tool_input_digest_sha256",
        ),
        CheckConstraint(
            "output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$'",
            name="ck_tool_output_digest_sha256",
        ),
        CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status != 'failed' AND error_code IS NULL)",
            name="ck_tool_execution_error_code",
        ),
        CheckConstraint(
            "tool_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_tool_name_format",
        ),
        Index("ix_tool_executions_started", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    input_digest: Mapped[str | None] = mapped_column(String(64))
    output_digest: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "length(action) > 0 AND length(actor_reference) > 0",
            name="ck_audit_log_action_actor_nonempty",
        ),
        CheckConstraint(
            "entity_type IN ('patient', 'encounter', 'complaint', "
            "'vital_observation', 'assessment_version', 'clinician_review', "
            "'queue_event', 'tool_execution', 'workflow_execution')",
            name="ck_audit_entity_type",
        ),
        CheckConstraint(
            "actor_reference LIKE 'SYN-%'",
            name="ck_audit_actor_synthetic",
        ),
        CheckConstraint(
            "action ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_audit_action_format",
        ),
        Index(
            "ix_audit_logs_entity_occurred", "entity_type", "entity_id", "occurred_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_reference: Mapped[str] = mapped_column(String(68), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WorkflowExecutionEvent(Base):
    __tablename__ = "workflow_execution_events"
    __table_args__ = (
        CheckConstraint(
            "length(state) > 0 AND length(event_type) > 0",
            name="ck_workflow_event_state_type_nonempty",
        ),
        Index(
            "ix_workflow_events_execution_created",
            "workflow_execution_id",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_executions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    action_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

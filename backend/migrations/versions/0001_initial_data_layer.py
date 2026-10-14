"""Create the synthetic triage persistence schema.

Revision ID: 0001_initial_data_layer
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_data_layer"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("synthetic_reference", sa.String(length=68), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "synthetic_reference LIKE 'SYN-%'",
            name="ck_patients_synthetic_reference",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("synthetic_reference"),
    )
    op.create_table(
        "triage_encounters",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'void')", name="ck_encounters_status"
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= started_at",
            name="ck_encounters_closed_after_start",
        ),
        sa.CheckConstraint(
            "(status = 'closed' AND closed_at IS NOT NULL) OR "
            "(status != 'closed' AND closed_at IS NULL)",
            name="ck_encounters_closed_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_encounters_patient_started",
        "triage_encounters",
        ["patient_id", "started_at"],
    )
    op.create_table(
        "presenting_complaints",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("concept_code", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(concept_code) > 0", name="ck_complaints_code_nonempty"
        ),
        sa.CheckConstraint(
            "concept_code ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_complaints_code_format",
        ),
        sa.CheckConstraint("length(summary) > 0", name="ck_complaints_summary_nonempty"),
        sa.ForeignKeyConstraint(
            ["encounter_id"], ["triage_encounters.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "encounter_id", name="uq_complaints_id_encounter"),
    )
    op.create_index(
        "ix_complaints_encounter", "presenting_complaints", ["encounter_id"]
    )
    op.create_table(
        "vital_observations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("measurement", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("source_reference", sa.String(length=68), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "value NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_vitals_value_finite",
        ),
        sa.CheckConstraint(
            "source IN ('clinician', 'device', 'patient_reported', 'imported')",
            name="ck_vitals_source",
        ),
        sa.CheckConstraint(
            "source_reference IS NULL OR source_reference LIKE 'SYN-%'",
            name="ck_vitals_source_reference_synthetic",
        ),
        sa.ForeignKeyConstraint(
            ["encounter_id"], ["triage_encounters.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "encounter_id", name="uq_vitals_id_encounter"),
    )
    op.create_index(
        "ix_vitals_encounter_measured",
        "vital_observations",
        ["encounter_id", "measured_at"],
    )
    op.create_table(
        "assessment_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("created_by_reference", sa.String(length=68), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_assessment_version_positive"),
        sa.CheckConstraint(
            "length(recommendation) > 0",
            name="ck_assessment_recommendation_nonempty",
        ),
        sa.CheckConstraint(
            "created_by_reference LIKE 'SYN-%'",
            name="ck_assessment_creator_synthetic",
        ),
        sa.ForeignKeyConstraint(
            ["encounter_id"], ["triage_encounters.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "encounter_id", "version", name="uq_assessment_encounter_version"
        ),
        sa.UniqueConstraint("id", "encounter_id", name="uq_assessments_id_encounter"),
    )
    op.create_index(
        "ix_assessments_encounter_created",
        "assessment_versions",
        ["encounter_id", "created_at"],
    )
    op.create_table(
        "assessment_evidence",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("complaint_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("vital_observation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'presenting_complaint' AND complaint_id IS NOT NULL "
            "AND vital_observation_id IS NULL) OR "
            "(kind = 'vital_observation' AND vital_observation_id IS NOT NULL "
            "AND complaint_id IS NULL)",
            name="ck_evidence_kind_matches_source",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_version_id", "encounter_id"],
            ["assessment_versions.id", "assessment_versions.encounter_id"],
            ondelete="RESTRICT",
            name="fk_evidence_assessment_encounter",
        ),
        sa.ForeignKeyConstraint(
            ["complaint_id", "encounter_id"],
            ["presenting_complaints.id", "presenting_complaints.encounter_id"],
            ondelete="RESTRICT",
            name="fk_evidence_complaint_encounter",
        ),
        sa.ForeignKeyConstraint(
            ["vital_observation_id", "encounter_id"],
            ["vital_observations.id", "vital_observations.encounter_id"],
            ondelete="RESTRICT",
            name="fk_evidence_vital_encounter",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assessment_version_id",
            "complaint_id",
            name="uq_evidence_assessment_complaint",
        ),
        sa.UniqueConstraint(
            "assessment_version_id",
            "vital_observation_id",
            name="uq_evidence_assessment_vital",
        ),
    )
    op.create_table(
        "clinician_reviews",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("clinician_reference", sa.String(length=68), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("override_summary", sa.String(length=2000), nullable=True),
        sa.Column("override_reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected', 'overridden')",
            name="ck_reviews_decision",
        ),
        sa.CheckConstraint(
            "(decision = 'overridden' AND override_summary IS NOT NULL "
            "AND override_reason_code IS NOT NULL) OR "
            "(decision != 'overridden' AND override_summary IS NULL "
            "AND override_reason_code IS NULL)",
            name="ck_reviews_override_fields",
        ),
        sa.CheckConstraint(
            "clinician_reference LIKE 'SYN-%'",
            name="ck_reviews_clinician_synthetic",
        ),
        sa.CheckConstraint(
            "override_summary IS NULL OR length(override_summary) > 0",
            name="ck_reviews_override_summary_nonempty",
        ),
        sa.CheckConstraint(
            "override_reason_code IS NULL OR "
            "override_reason_code ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_reviews_override_reason_format",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_version_id"],
            ["assessment_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reviews_assessment_reviewed",
        "clinician_reviews",
        ["assessment_version_id", "reviewed_at"],
    )
    op.create_table(
        "queue_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_reference", sa.String(length=68), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('entered', 'claimed', 'released', 'removed')",
            name="ck_queue_events_type",
        ),
        sa.CheckConstraint(
            "actor_reference IS NULL OR actor_reference LIKE 'SYN-%'",
            name="ck_queue_actor_synthetic",
        ),
        sa.CheckConstraint(
            "queue_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_queue_name_format",
        ),
        sa.ForeignKeyConstraint(
            ["encounter_id"], ["triage_encounters.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_queue_events_encounter_occurred",
        "queue_events",
        ["encounter_id", "occurred_at"],
    )
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=True),
        sa.Column("output_digest", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'cancelled')",
            name="ck_tool_executions_status",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at", name="ck_tool_execution_time_order"
        ),
        sa.CheckConstraint(
            "input_digest IS NULL OR input_digest ~ '^[0-9a-f]{64}$'",
            name="ck_tool_input_digest_sha256",
        ),
        sa.CheckConstraint(
            "output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$'",
            name="ck_tool_output_digest_sha256",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status != 'failed' AND error_code IS NULL)",
            name="ck_tool_execution_error_code",
        ),
        sa.CheckConstraint(
            "tool_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_tool_name_format",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_executions_started", "tool_executions", ["started_at"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor_reference", sa.String(length=68), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('patient', 'encounter', 'complaint', "
            "'vital_observation', 'assessment_version', 'clinician_review', "
            "'queue_event', 'tool_execution')",
            name="ck_audit_entity_type",
        ),
        sa.CheckConstraint(
            "length(action) > 0 AND length(actor_reference) > 0",
            name="ck_audit_log_action_actor_nonempty",
        ),
        sa.CheckConstraint(
            "actor_reference LIKE 'SYN-%'",
            name="ck_audit_actor_synthetic",
        ),
        sa.CheckConstraint(
            "action ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_audit_action_format",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_entity_occurred",
        "audit_logs",
        ["entity_type", "entity_id", "occurred_at"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_append_only_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "assessment_versions",
        "assessment_evidence",
        "clinician_reviews",
        "queue_events",
        "tool_executions",
        "audit_logs",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table in (
        "audit_logs",
        "tool_executions",
        "queue_events",
        "clinician_reviews",
        "assessment_evidence",
        "assessment_versions",
        "vital_observations",
        "presenting_complaints",
        "triage_encounters",
        "patients",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION reject_append_only_mutation()")

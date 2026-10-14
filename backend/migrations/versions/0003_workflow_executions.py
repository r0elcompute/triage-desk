"""Persist workflow state and immutable transition events.

Revision ID: 0003_workflow_executions
Revises: 0002_mcp_review_approvals
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_workflow_executions"
down_revision = "0002_mcp_review_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_audit_entity_type", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_entity_type",
        "audit_logs",
        "entity_type IN ('patient', 'encounter', 'complaint', "
        "'vital_observation', 'assessment_version', 'clinician_review', "
        "'queue_event', 'tool_execution', 'workflow_execution')",
    )

    op.create_table(
        "workflow_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_number", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("protocol_id", sa.String(length=64), nullable=False),
        sa.Column("protocol_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("evaluation_result", sa.JSON(), nullable=True),
        sa.Column("recommendation", sa.JSON(), nullable=True),
        sa.Column("review_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("commit_result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('received', 'validating', 'evaluating', "
            "'generating_recommendation', 'requesting_review', "
            "'awaiting_clinician_approval', 'committing', 'completed', 'failed')",
            name="ck_workflow_executions_status",
        ),
        sa.CheckConstraint(
            "length(model_id) > 0", name="ck_workflow_execution_model_nonempty"
        ),
        sa.CheckConstraint(
            "assessment_number > 0",
            name="ck_workflow_execution_assessment_positive",
        ),
        sa.CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_execution_digest_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_version_id", "encounter_id"],
            ["assessment_versions.id", "assessment_versions.encounter_id"],
            name="fk_workflow_execution_assessment_encounter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_request_id"],
            ["clinician_review_requests.id"],
            name="fk_workflow_execution_review_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["clinician_approvals.id"],
            name="fk_workflow_execution_approval",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "assessment_version_id"),
    )
    op.create_index(
        "ix_workflow_execution_status_updated",
        "workflow_executions",
        ["status", "updated_at"],
    )
    op.create_table(
        "workflow_execution_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("action_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(state) > 0 AND length(event_type) > 0",
            name="ck_workflow_event_state_type_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_execution_id"],
            ["workflow_executions.id"],
            name="fk_workflow_event_execution",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_events_execution_created",
        "workflow_execution_events",
        ["workflow_execution_id", "occurred_at"],
    )
    op.execute(
        "CREATE TRIGGER workflow_execution_events_append_only "
        "BEFORE UPDATE OR DELETE ON workflow_execution_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER workflow_execution_events_append_only "
        "ON workflow_execution_events"
    )
    op.drop_index(
        "ix_workflow_events_execution_created",
        table_name="workflow_execution_events",
    )
    op.drop_table("workflow_execution_events")
    op.drop_index(
        "ix_workflow_execution_status_updated", table_name="workflow_executions"
    )
    op.drop_table("workflow_executions")
    op.drop_constraint("ck_audit_entity_type", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_entity_type",
        "audit_logs",
        "entity_type IN ('patient', 'encounter', 'complaint', "
        "'vital_observation', 'assessment_version', 'clinician_review', "
        "'queue_event', 'tool_execution')",
    )

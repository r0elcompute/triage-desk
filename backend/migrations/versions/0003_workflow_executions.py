"""Record LangGraph workflow executions and resumable review state.

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
    op.create_table(
        "workflow_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model_identifier", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("result_digest", sa.String(length=64), nullable=False),
        sa.Column("transitions", sa.JSON(), nullable=False),
        sa.Column("reference_retrieval", sa.JSON(), nullable=False),
        sa.Column("protocol_reference", sa.JSON(), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=False),
        sa.Column("validation_valid", sa.Boolean(), nullable=False),
        sa.Column("validation_complete", sa.Boolean(), nullable=False),
        sa.Column("evaluation_result", sa.JSON(), nullable=True),
        sa.Column("evaluation_action_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("recommendation_result", sa.JSON(), nullable=True),
        sa.Column("recommendation_valid", sa.Boolean(), nullable=False),
        sa.Column("review_request_result", sa.JSON(), nullable=True),
        sa.Column("review_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("commit_action_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'failed', 'committed')",
            name="ck_workflow_executions_status",
        ),
        sa.CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_input_digest_sha256",
        ),
        sa.CheckConstraint(
            "result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_result_digest_sha256",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name="ck_workflow_execution_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_version_id", "encounter_id"],
            ["assessment_versions.id", "assessment_versions.encounter_id"],
            name="fk_workflows_assessment_encounter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_request_id"],
            ["clinician_review_requests.id"],
            name="fk_workflows_review_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["commit_action_id"],
            ["tool_executions.id"],
            name="fk_workflows_commit_action",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflows_assessment_created",
        "workflow_executions",
        ["assessment_version_id", "created_at"],
    )
    op.drop_constraint("ck_audit_entity_type", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_entity_type",
        "audit_logs",
        "entity_type IN ('patient', 'encounter', 'complaint', "
        "'vital_observation', 'assessment_version', 'clinician_review', "
        "'queue_event', 'tool_execution', 'workflow_execution')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_entity_type", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_entity_type",
        "audit_logs",
        "entity_type IN ('patient', 'encounter', 'complaint', "
        "'vital_observation', 'assessment_version', 'clinician_review', "
        "'queue_event', 'tool_execution')",
    )
    op.drop_index(
        "ix_workflows_assessment_created", table_name="workflow_executions"
    )
    op.drop_table("workflow_executions")
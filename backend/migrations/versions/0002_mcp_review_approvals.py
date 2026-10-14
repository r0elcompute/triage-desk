"""Add MCP review, approval and idempotent queue commit records.

Revision ID: 0002_mcp_review_approvals
Revises: 0001_initial_data_layer
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_mcp_review_approvals"
down_revision = "0001_initial_data_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clinician_review_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_reference", sa.String(length=68), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "requested_by_reference LIKE 'SYN-%'",
            name="ck_review_request_actor_synthetic",
        ),
        sa.CheckConstraint(
            "length(reason) > 0", name="ck_review_request_reason_nonempty"
        ),
        sa.ForeignKeyConstraint(
            ["assessment_version_id", "encounter_id"],
            ["assessment_versions.id", "assessment_versions.encounter_id"],
            name="fk_review_requests_assessment_encounter",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "assessment_version_id", name="uq_review_requests_id_assessment"
        ),
    )
    op.create_index(
        "ix_review_requests_assessment_created",
        "clinician_review_requests",
        ["assessment_version_id", "created_at"],
    )
    op.create_table(
        "clinician_approvals",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_number", sa.Integer(), nullable=False),
        sa.Column("clinician_subject", sa.String(length=128), nullable=False),
        sa.Column("clinician_reference", sa.String(length=68), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assessment_number > 0", name="ck_approval_assessment_number_positive"
        ),
        sa.CheckConstraint(
            "length(clinician_subject) > 0",
            name="ck_approval_subject_nonempty",
        ),
        sa.CheckConstraint(
            "clinician_reference LIKE 'SYN-%'",
            name="ck_approval_clinician_synthetic",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "assessment_version_id"],
            [
                "clinician_review_requests.id",
                "clinician_review_requests.assessment_version_id",
            ],
            name="fk_approvals_request_assessment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_clinician_approvals_request"),
        sa.UniqueConstraint(
            "id", "assessment_version_id", name="uq_approvals_id_assessment"
        ),
    )
    op.create_index(
        "ix_approvals_assessment_created",
        "clinician_approvals",
        ["assessment_version_id", "created_at"],
    )
    op.create_table(
        "clinician_approval_revocations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revoked_by_reference", sa.String(length=68), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revoked_by_reference LIKE 'SYN-%'",
            name="ck_approval_revoker_synthetic",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["clinician_approvals.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id"),
    )
    op.create_table(
        "queue_commits",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_number", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("queue_event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("committed_by_subject", sa.String(length=128), nullable=False),
        sa.Column("committed_by_reference", sa.String(length=68), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assessment_number > 0",
            name="ck_queue_commit_assessment_positive",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 16 AND 128",
            name="ck_queue_commit_key_length",
        ),
        sa.CheckConstraint(
            "queue_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
            name="ck_queue_commit_name_format",
        ),
        sa.CheckConstraint(
            "committed_by_reference LIKE 'SYN-%'",
            name="ck_queue_commit_actor_synthetic",
        ),
        sa.CheckConstraint(
            "length(committed_by_subject) > 0",
            name="ck_queue_commit_subject_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id", "assessment_version_id"],
            ["clinician_approvals.id", "clinician_approvals.assessment_version_id"],
            name="fk_queue_commits_approval_assessment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["queue_event_id"], ["queue_events.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id", name="uq_queue_commits_approval"),
        sa.UniqueConstraint("idempotency_key", name="uq_queue_commits_idempotency"),
        sa.UniqueConstraint("queue_event_id", name="uq_queue_commits_event"),
    )
    op.create_index(
        "ix_queue_commits_assessment_created",
        "queue_commits",
        ["assessment_version_id", "created_at"],
    )

    for table in (
        "clinician_review_requests",
        "clinician_approvals",
        "clinician_approval_revocations",
        "queue_commits",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table in (
        "queue_commits",
        "clinician_approval_revocations",
        "clinician_approvals",
        "clinician_review_requests",
    ):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.drop_index("ix_queue_commits_assessment_created", table_name="queue_commits")
    op.drop_index("ix_approvals_assessment_created", table_name="clinician_approvals")
    op.drop_index(
        "ix_review_requests_assessment_created",
        table_name="clinician_review_requests",
    )
    op.drop_table("queue_commits")
    op.drop_table("clinician_approval_revocations")
    op.drop_table("clinician_approvals")
    op.drop_table("clinician_review_requests")

"""initial schema

Revision ID: 20260401_0001
Revises: None
Create Date: 2026-04-01 16:20:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260401_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "module_configs",
        sa.Column("module_code", sa.String(length=32), nullable=False),
        sa.Column("module_name", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_cron", sa.String(length=64), nullable=True),
        sa.Column("extra_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("module_code"),
    )

    op.create_table(
        "source_snapshots",
        sa.Column("module_code", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_doc_key", sa.String(length=128), nullable=False),
        sa.Column("source_view_key", sa.String(length=128), nullable=True),
        sa.Column("sync_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("data_source", sa.String(length=32), nullable=False),
        sa.Column("sync_status", sa.String(length=32), nullable=False),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("raw_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_source_snapshots_module_time", "source_snapshots", ["module_code", "sync_time"], unique=False)

    op.create_table(
        "normalized_records",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module_code", sa.String(length=32), nullable=False),
        sa.Column("source_row_id", sa.String(length=128), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("normalized_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("field_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("field_confidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("field_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("field_samples", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("unresolved_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recognition_status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["snapshot_id"], ["source_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_normalized_records_snapshot", "normalized_records", ["snapshot_id"], unique=False)
    op.create_index("idx_normalized_records_module_customer", "normalized_records", ["module_code", "customer_name"], unique=False)

    op.create_table(
        "task_plans",
        sa.Column("module_code", sa.String(length=32), nullable=False),
        sa.Column("normalized_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("eligibility", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("planner_version", sa.String(length=32), nullable=False),
        sa.Column("plan_status", sa.String(length=32), nullable=False, server_default=sa.text("'planned'")),
        sa.Column("planned_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["normalized_record_id"], ["normalized_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_plans_module_status", "task_plans", ["module_code", "plan_status"], unique=False)

    op.create_table(
        "task_runs",
        sa.Column("task_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("run_status", sa.String(length=32), nullable=False),
        sa.Column("manual_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("final_link", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("executor_version", sa.String(length=32), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["task_plan_id"], ["task_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_runs_task_plan", "task_runs", ["task_plan_id", "run_time"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_task_runs_task_plan", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_index("idx_task_plans_module_status", table_name="task_plans")
    op.drop_table("task_plans")
    op.drop_index("idx_normalized_records_module_customer", table_name="normalized_records")
    op.drop_index("idx_normalized_records_snapshot", table_name="normalized_records")
    op.drop_table("normalized_records")
    op.drop_index("idx_source_snapshots_module_time", table_name="source_snapshots")
    op.drop_table("source_snapshots")
    op.drop_table("module_configs")

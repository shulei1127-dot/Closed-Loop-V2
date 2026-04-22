"""add task batch persistence tables

Revision ID: 20260411_0003
Revises: 20260401_0002
Create Date: 2026-04-11 11:50:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260411_0003"
down_revision = "20260401_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module_code", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("requested_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enqueued_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("running_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("finished_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("closed_success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("manual_required_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pending_confirmation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_batches_module_created", "task_batches", ["module_code", "created_at"], unique=False)

    op.create_table(
        "task_batch_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("terminal_status", sa.String(length=32), nullable=True),
        sa.Column("run_status", sa.String(length=32), nullable=True),
        sa.Column("manual_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["batch_id"], ["task_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_batch_jobs_batch", "task_batch_jobs", ["batch_id", "created_at"], unique=False)
    op.create_index("idx_task_batch_jobs_task_plan", "task_batch_jobs", ["task_plan_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_task_batch_jobs_task_plan", table_name="task_batch_jobs")
    op.drop_index("idx_task_batch_jobs_batch", table_name="task_batch_jobs")
    op.drop_table("task_batch_jobs")
    op.drop_index("idx_task_batches_module_created", table_name="task_batches")
    op.drop_table("task_batches")

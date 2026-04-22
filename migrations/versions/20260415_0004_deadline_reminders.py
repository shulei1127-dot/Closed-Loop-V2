"""add deadline reminders table

Revision ID: 20260415_0004
Revises: 20260411_0003
Create Date: 2026-04-15 14:20:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260415_0004"
down_revision = "20260411_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deadline_reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module_code", sa.String(length=32), nullable=False, server_default=sa.text("'inspection'")),
        sa.Column("pts_work_order_id", sa.String(length=128), nullable=False),
        sa.Column("pts_work_order_link", sa.Text(), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("service_type", sa.String(length=64), nullable=True),
        sa.Column("status_text", sa.String(length=64), nullable=True),
        sa.Column("remind_type", sa.String(length=32), nullable=False),
        sa.Column("deadline_date", sa.Date(), nullable=False),
        sa.Column("plan_finish_time_raw", sa.String(length=128), nullable=True),
        sa.Column("send_status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("message_channel", sa.String(length=32), nullable=True),
        sa.Column("sender_type", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("send_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pts_work_order_id",
            "remind_type",
            name="uq_deadline_reminders_pts_work_order_remind_type",
        ),
    )
    op.create_index(
        "idx_deadline_reminders_send_status_created",
        "deadline_reminders",
        ["send_status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_deadline_reminders_module_created",
        "deadline_reminders",
        ["module_code", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_deadline_reminders_module_created", table_name="deadline_reminders")
    op.drop_index("idx_deadline_reminders_send_status_created", table_name="deadline_reminders")
    op.drop_table("deadline_reminders")

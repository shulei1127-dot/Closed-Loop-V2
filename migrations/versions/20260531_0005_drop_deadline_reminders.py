"""drop deprecated deadline_reminders table

Revision ID: 20260531_0005
Revises: 20260415_0004
Create Date: 2026-05-31 12:00:00
"""

from alembic import op


revision = "20260531_0005"
down_revision = "20260415_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_deadline_reminders_module_created", table_name="deadline_reminders")
    op.drop_index("idx_deadline_reminders_send_status_created", table_name="deadline_reminders")
    op.drop_table("deadline_reminders")


def downgrade() -> None:
    pass

"""expand module configs for real collectors

Revision ID: 20260401_0002
Revises: 20260401_0001
Create Date: 2026-04-01 18:10:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260401_0002"
down_revision = "20260401_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "module_configs",
        sa.Column("source_doc_key", sa.String(length=128), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "module_configs",
        sa.Column("source_view_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "module_configs",
        sa.Column("collector_type", sa.String(length=32), nullable=False, server_default=sa.text("'fixture'")),
    )


def downgrade() -> None:
    op.drop_column("module_configs", "collector_type")
    op.drop_column("module_configs", "source_view_key")
    op.drop_column("module_configs", "source_doc_key")


"""create audit_events table

Revision ID: 710d78dcb974
Revises:
Create Date: 2026-08-22 13:37:11.692778

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "710d78dcb974"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("sequence_number", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("resource_type", sa.String(), nullable=True),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "payload_field_commitments", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("sequence_number"),
    )
    # Query filter support (req 4a-4c).
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index(
        "ix_audit_events_resource_type_resource_id",
        "audit_events",
        ["resource_type", "resource_id"],
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_timestamp", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_resource_type_resource_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_table("audit_events")

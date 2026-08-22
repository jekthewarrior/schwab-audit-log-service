"""create app_role and maintenance_role

Revision ID: adde66daf218
Revises: 710d78dcb974
Create Date: 2026-08-22 13:43:02.187601

Implements REQUIREMENTS.md 2a (least-privilege app_role) and the "DB privilege"
decision under Scenario B Group 3 (shared, narrowly-scoped maintenance_role for
redaction/retention, using column-level grants that never cover sequence_number,
content_hash, or prev_hash).

Passwords come from Settings (audit_log_service.core.config), not hardcoded, but are
interpolated directly into DDL rather than bound as query parameters — CREATE
ROLE/GRANT don't support parameter binding for identifiers/passwords the way DML
does. Safe here because these are trusted internal config values, not user input.
"""

from collections.abc import Sequence

from alembic import op

from audit_log_service.core.config import settings

revision: str = "adde66daf218"
down_revision: str | None = "710d78dcb974"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Columns maintenance_role may UPDATE: everything redaction (B1) and retention (B2)
# touch. Deliberately excludes sequence_number, content_hash, prev_hash — those stay
# permanently un-updatable by anything, including this role.
MAINTENANCE_UPDATABLE_COLUMNS = (
    "event_type",
    "actor_id",
    "resource_type",
    "resource_id",
    "payload",
    "payload_field_commitments",
    "timestamp",
    "recorded_at",
    "archived",
    "archived_at",
)


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_role') THEN
                CREATE ROLE app_role LOGIN PASSWORD '{settings.app_role_password}';
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'maintenance_role') THEN
                CREATE ROLE maintenance_role LOGIN PASSWORD '{settings.maintenance_role_password}';
            END IF;
        END
        $$;
        """
    )

    op.execute("GRANT CONNECT ON DATABASE audit_log TO app_role, maintenance_role")
    op.execute("GRANT USAGE ON SCHEMA public TO app_role, maintenance_role")

    # app_role: append-only, per 2a. No UPDATE/DELETE/DDL, ever.
    op.execute("GRANT SELECT, INSERT ON audit_events TO app_role")

    # maintenance_role: SELECT (read tail/current record), INSERT (append its own
    # FIELD_REDACTED/RECORD_ARCHIVED system events in the same transaction as the
    # column update, for atomicity), and UPDATE scoped to the columns above only.
    op.execute("GRANT SELECT, INSERT ON audit_events TO maintenance_role")
    columns = ", ".join(MAINTENANCE_UPDATABLE_COLUMNS)
    op.execute(f"GRANT UPDATE ({columns}) ON audit_events TO maintenance_role")


def downgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON audit_events FROM app_role, maintenance_role")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_role, maintenance_role")
    op.execute("REVOKE CONNECT ON DATABASE audit_log FROM app_role, maintenance_role")
    op.execute("DROP ROLE IF EXISTS app_role")
    op.execute("DROP ROLE IF EXISTS maintenance_role")

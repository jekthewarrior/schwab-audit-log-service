"""B4.5 — DB-level immutability. Proves REQUIREMENTS.md 2a's least-privilege design
is actually enforced by Postgres, not just documented intent — mirrors the live
`psql`-based negative tests done during A1.3/B1/B2's implementation, now automated.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.services.append import append_event


async def _write_one(app_session: AsyncSession) -> None:
    await append_event(
        app_session,
        event_type="USER_LOGIN",
        actor_id="user-1",
        resource_type="SESSION",
        resource_id="sess-1",
        payload={},
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await app_session.commit()


async def test_app_role_cannot_update_any_column(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_one(maintenance_session)
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_session.execute(
            text("UPDATE audit_events SET content_hash = 'x' WHERE sequence_number = 1")
        )
        await app_session.commit()


async def test_app_role_cannot_delete(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_one(maintenance_session)
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_session.execute(text("DELETE FROM audit_events WHERE sequence_number = 1"))
        await app_session.commit()


async def test_maintenance_role_cannot_update_content_hash(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_one(app_session)
    with pytest.raises(DBAPIError, match="permission denied"):
        await maintenance_session.execute(
            text("UPDATE audit_events SET content_hash = 'x' WHERE sequence_number = 1")
        )
        await maintenance_session.commit()


async def test_maintenance_role_cannot_update_sequence_number(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_one(app_session)
    with pytest.raises(DBAPIError, match="permission denied"):
        await maintenance_session.execute(
            text("UPDATE audit_events SET sequence_number = 999 WHERE sequence_number = 1")
        )
        await maintenance_session.commit()


async def test_maintenance_role_can_update_payload(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    """Confirms the negative tests above are actually meaningful — maintenance_role
    isn't broadly denied, only denied on the specific protected columns.
    """
    await _write_one(app_session)
    await maintenance_session.execute(
        text("UPDATE audit_events SET payload = '{}' WHERE sequence_number = 1")
    )
    await maintenance_session.commit()

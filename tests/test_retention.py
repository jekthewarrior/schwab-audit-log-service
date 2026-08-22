"""B4.2 — archival preserves chain validity, no false positive, and gap detection
stays unaffected (REQUIREMENTS.md Scenario B 1d, 2a, 2b, 2c).
"""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.models import AuditEvent
from audit_log_service.services.append import append_event
from audit_log_service.services.retention import sweep_retention
from audit_log_service.services.verify import verify_chain


async def _backdate(session: AsyncSession, sequence_number: int, years: int) -> None:
    await session.execute(
        text(
            f"UPDATE audit_events SET recorded_at = now() - interval '{years} years' "
            "WHERE sequence_number = :seq"
        ),
        {"seq": sequence_number},
    )
    await session.commit()


async def test_sweep_archives_only_old_records(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    for i in range(2):
        await append_event(
            app_session,
            event_type="USER_LOGIN",
            actor_id=f"user-{i}",
            resource_type="SESSION",
            resource_id=f"sess-{i}",
            payload={},
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        await app_session.commit()

    await _backdate(maintenance_session, 1, years=2)

    archived = await sweep_retention(maintenance_session, actor_id="cron")
    await maintenance_session.commit()

    assert archived == [1]
    record1 = await app_session.get(AuditEvent, 1, populate_existing=True)
    record2 = await app_session.get(AuditEvent, 2, populate_existing=True)
    assert record1 is not None and record1.archived is True
    assert record2 is not None and record2.archived is False


async def test_verify_stays_intact_after_archival(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
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
    await _backdate(maintenance_session, 1, years=2)

    await sweep_retention(maintenance_session, actor_id="cron")
    await maintenance_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is True


async def test_archived_record_keeps_its_sequence_slot(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    """2c: archiving never removes a row, so gap detection is unaffected."""
    for _ in range(3):
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
    await _backdate(maintenance_session, 2, years=2)

    await sweep_retention(maintenance_session, actor_id="cron")
    await maintenance_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is True


async def test_sweep_is_idempotent(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
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
    await _backdate(maintenance_session, 1, years=2)

    first = await sweep_retention(maintenance_session, actor_id="cron")
    await maintenance_session.commit()
    second = await sweep_retention(maintenance_session, actor_id="cron")
    await maintenance_session.commit()

    assert first == [1]
    assert second == []


async def test_archival_leaves_content_hash_and_prev_hash_unchanged(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
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
    before = await app_session.get(AuditEvent, 1)
    assert before is not None
    original_content_hash, original_prev_hash = before.content_hash, before.prev_hash

    await _backdate(maintenance_session, 1, years=2)
    await sweep_retention(maintenance_session, actor_id="cron")
    await maintenance_session.commit()

    after = await app_session.get(AuditEvent, 1, populate_existing=True)
    assert after is not None
    assert after.content_hash == original_content_hash
    assert after.prev_hash == original_prev_hash

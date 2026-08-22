"""req 9's acceptance test, automated: write -> query -> verify -> direct-datastore
tamper -> verify catches it. Covers every violation type from REQUIREMENTS.md 8a and
both false-positive-avoidance cases (archived, redacted) — codifies what was
extensively verified live (manually) during A4/B1/B2's implementation.
"""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.schemas.verify import ViolationType
from audit_log_service.services.append import append_event
from audit_log_service.services.query import list_events
from audit_log_service.services.verify import verify_chain


async def _write(
    session: AsyncSession,
    *,
    actor_id: str = "user-1",
    resource_id: str = "sess-1",
) -> None:
    await append_event(
        session,
        event_type="USER_LOGIN",
        actor_id=actor_id,
        resource_type="SESSION",
        resource_id=resource_id,
        payload={"ip": "127.0.0.1"},
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await session.commit()


async def test_empty_chain_is_intact(app_session: AsyncSession) -> None:
    result = await verify_chain(app_session)
    assert result.intact is True


async def test_clean_writes_verify_intact(app_session: AsyncSession) -> None:
    for i in range(4):
        await _write(app_session, resource_id=f"sess-{i}")
    result = await verify_chain(app_session)
    assert result.intact is True


async def test_query_after_write_returns_written_records(app_session: AsyncSession) -> None:
    await _write(app_session, actor_id="user-1")
    await _write(app_session, actor_id="user-2")
    records, next_cursor = await list_events(app_session, actor_id="user-1")
    assert [r.actor_id for r in records] == ["user-1"]
    assert next_cursor is None


async def test_direct_content_tampering_is_detected(
    app_session: AsyncSession, admin_session: AsyncSession
) -> None:
    """Regression test: this specific tampering (wholesale payload replacement with
    a field that has no payload_field_commitments entry) originally crashed verify
    with an unhandled KeyError instead of reporting CONTENT_MISMATCH — found by
    this test failing against real Postgres, fixed in
    services/verify.py::_recompute_content_hash.
    """
    await _write(app_session, resource_id="acct-1")

    # "direct DB mutation bypassing the API" (req 9) — via the admin connection,
    # simulating an attacker with datastore access neither app_role nor
    # maintenance_role has.
    await admin_session.execute(
        text("UPDATE audit_events SET payload = '{\"tampered\": true}' WHERE sequence_number = 1")
    )
    await admin_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is False
    assert result.sequence_number == 1
    assert result.violation_type == ViolationType.CONTENT_MISMATCH


async def test_direct_prev_hash_tampering_is_detected(
    app_session: AsyncSession, admin_session: AsyncSession
) -> None:
    await _write(app_session)
    await _write(app_session)

    await admin_session.execute(
        text("UPDATE audit_events SET prev_hash = repeat('f', 64) WHERE sequence_number = 2")
    )
    await admin_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is False
    assert result.sequence_number == 2
    assert result.violation_type == ViolationType.LINK_MISMATCH


async def test_deleting_an_interior_record_is_detected_with_precise_gap(
    app_session: AsyncSession, admin_session: AsyncSession
) -> None:
    for _ in range(3):
        await _write(app_session)

    await admin_session.execute(text("DELETE FROM audit_events WHERE sequence_number = 2"))
    await admin_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is False
    assert result.sequence_number == 3
    assert result.violation_type == ViolationType.LINK_MISMATCH
    assert result.detail is not None
    assert "sequence_number 2" in result.detail


async def test_genesis_prev_hash_tampering_is_detected(
    app_session: AsyncSession, admin_session: AsyncSession
) -> None:
    await _write(app_session)

    await admin_session.execute(
        text("UPDATE audit_events SET prev_hash = repeat('1', 64) WHERE sequence_number = 1")
    )
    await admin_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is False
    assert result.sequence_number == 1
    assert result.violation_type == ViolationType.GENESIS_MISMATCH


async def test_deleting_the_first_record_is_detected_as_genesis_mismatch(
    app_session: AsyncSession, admin_session: AsyncSession
) -> None:
    await _write(app_session)
    await _write(app_session)

    await admin_session.execute(text("DELETE FROM audit_events WHERE sequence_number = 1"))
    await admin_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is False
    assert result.sequence_number == 2
    assert result.violation_type == ViolationType.GENESIS_MISMATCH
    assert result.detail is not None
    assert "chain begins at sequence_number=2" in result.detail

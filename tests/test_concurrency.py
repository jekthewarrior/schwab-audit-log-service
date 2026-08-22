"""A5.4 — concurrent writers. Confirms the advisory lock (REQUIREMENTS.md 7c)
actually serializes appends: no duplicate or gapped sequence_numbers under
concurrent load, and the resulting chain still verifies as intact.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from audit_log_service.models import AuditEvent
from audit_log_service.services.append import append_event
from audit_log_service.services.verify import verify_chain
from tests.conftest import DbEngines

CONCURRENT_WRITERS = 20


async def test_concurrent_appends_produce_no_duplicate_or_gapped_sequence_numbers(
    db_engines: DbEngines, clean_db: None
) -> None:
    session_local = async_sessionmaker(db_engines.app, expire_on_commit=False)

    async def _write(i: int) -> None:
        async with session_local() as session:
            await append_event(
                session,
                event_type="USER_LOGIN",
                actor_id=f"user-{i}",
                resource_type="SESSION",
                resource_id=f"sess-{i}",
                payload={},
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            )
            await session.commit()

    await asyncio.gather(*(_write(i) for i in range(CONCURRENT_WRITERS)))

    async with session_local() as session:
        rows = (
            await session.scalars(select(AuditEvent).order_by(AuditEvent.sequence_number))
        ).all()
        sequence_numbers = [r.sequence_number for r in rows]
        assert sequence_numbers == list(range(1, CONCURRENT_WRITERS + 1))

        result = await verify_chain(session)
        assert result.intact is True

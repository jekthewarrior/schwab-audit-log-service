"""Write-throughput load test — turns 7c's "fully serialized appends" from a
qualitative, accepted trade-off into a measured number, per TESTING.md's
previously-open "load/scale testing" gap.

Not a strict pass/fail performance gate: hardware varies too much across
machines/CI for a hard throughput threshold to be meaningful without becoming
flaky. Asserts correctness (every write succeeds, the resulting chain has no
gaps/duplicates, sequence_numbers are exactly 1..N) and reports throughput as
informational output — run with `-s` to see it; pytest captures stdout otherwise.

Goes through the real HTTP layer (the `client` fixture, ASGI in-process — no real
network socket), so this measures the application + advisory-lock + DB layer's
serialization overhead, not wire-level HTTP/TCP cost, which would be near-zero on
localhost regardless.
"""

import asyncio
import time

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.models import AuditEvent

WRITE_COUNT = 100


async def test_write_throughput_under_concurrent_load(
    client: AsyncClient, admin_session: AsyncSession
) -> None:
    async def _write(i: int) -> int:
        response = await client.post(
            "/audit/events",
            json={
                "eventType": "USER_LOGIN",
                "actorId": f"user-{i}",
                "resourceType": "SESSION",
                "resourceId": f"sess-{i}",
                "payload": {},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        return response.status_code

    start = time.perf_counter()
    statuses = await asyncio.gather(*(_write(i) for i in range(WRITE_COUNT)))
    elapsed = time.perf_counter() - start

    assert all(status == 201 for status in statuses)

    throughput = WRITE_COUNT / elapsed
    print(
        f"\n[load] {WRITE_COUNT} concurrent writes via HTTP in {elapsed:.2f}s "
        f"({throughput:.1f} writes/sec) — fully serialized by the advisory lock, "
        "per REQUIREMENTS.md 7c"
    )

    rows = (
        await admin_session.scalars(select(AuditEvent).order_by(AuditEvent.sequence_number))
    ).all()
    sequence_numbers = [r.sequence_number for r in rows]
    assert sequence_numbers == list(range(1, WRITE_COUNT + 1))

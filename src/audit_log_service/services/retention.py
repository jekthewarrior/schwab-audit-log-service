from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.config import settings
from audit_log_service.models import AuditEvent
from audit_log_service.services.append import APPEND_LOCK_KEY, append_event


async def sweep_retention(session: AsyncSession, *, actor_id: str) -> list[int]:
    """Archives records older than the configured retention window.

    Eligibility is based on recorded_at (server-assigned), not the caller-supplied
    timestamp — same trust-boundary reasoning as 7d's chain order and 4a's decision
    to keep the two timestamps' roles separate. A caller reporting a misleading
    timestamp shouldn't be able to influence when their own record becomes eligible
    for archival.

    Nulls detail columns and sets archived/archived_at (Scenario B "Mechanism");
    sequence_number/content_hash/prev_hash are never touched. Appends one
    RECORD_ARCHIVED system event per sweep run (not one per record), documenting
    every sequence_number archived, in the same transaction as the archival
    updates. Idempotent — an already-archived record is never a candidate again,
    and a sweep that finds nothing eligible appends no event.

    Acquires the same advisory lock append_event uses, up front, before reading
    candidates — not in the original task description, but needed for correctness:
    without it, two concurrent sweep calls could both read the same candidate set
    before either archives it, producing two RECORD_ARCHIVED events over the same
    records. Safe to acquire twice in one transaction (append_event acquires it
    again internally) — Postgres advisory locks are reentrant within a transaction.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEND_LOCK_KEY})

    cutoff = datetime.now(UTC) - timedelta(days=settings.retention_window_days)
    candidates = (
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.archived.is_(False),
                AuditEvent.recorded_at < cutoff,
            )
        )
    ).all()

    if not candidates:
        return []

    archived_at = datetime.now(UTC)
    archived_sequence_numbers = [record.sequence_number for record in candidates]

    for record in candidates:
        record.event_type = None
        record.actor_id = None
        record.resource_type = None
        record.resource_id = None
        record.payload = None
        record.payload_field_commitments = None
        record.timestamp = None
        record.recorded_at = None
        record.archived = True
        record.archived_at = archived_at

    await append_event(
        session,
        event_type="RECORD_ARCHIVED",
        actor_id=actor_id,
        resource_type="AUDIT_LOG",
        resource_id="retention-sweep",
        payload={"archivedSequenceNumbers": archived_sequence_numbers},
        timestamp=archived_at,
    )

    await session.flush()
    return archived_sequence_numbers

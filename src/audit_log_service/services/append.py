from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.hashing import (
    GENESIS_HASH,
    compute_content_hash,
    compute_payload_commitments,
    record_hash,
)
from audit_log_service.models import AuditEvent

# Arbitrary fixed key for pg_advisory_xact_lock — serializes the append critical
# section (read tail -> compute sequence_number/prev_hash -> insert) across all
# writers. See REQUIREMENTS.md 7c: chosen over SELECT ... FOR UPDATE on the tail row
# specifically because it also covers the empty-table/genesis bootstrap case, which
# a row lock can't (there's no row to lock yet).
APPEND_LOCK_KEY = 727_001


async def append_event(
    session: AsyncSession,
    *,
    event_type: str,
    actor_id: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, object],
    timestamp: datetime,
) -> AuditEvent:
    """Append a new record to the chain. Must run inside a transaction — the
    advisory lock is held for the transaction's duration and released on commit.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEND_LOCK_KEY})

    tail = await session.scalar(
        select(AuditEvent).order_by(AuditEvent.sequence_number.desc()).limit(1)
    )

    if tail is None:
        sequence_number = 1
        prev_hash = GENESIS_HASH
    else:
        sequence_number = tail.sequence_number + 1
        prev_hash = record_hash(tail.content_hash, tail.prev_hash)

    recorded_at = datetime.now(UTC)
    payload_field_commitments = compute_payload_commitments(payload)
    content_hash = compute_content_hash(
        sequence_number=sequence_number,
        recorded_at=recorded_at,
        event_type=event_type,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        timestamp=timestamp,
        payload_field_commitments=payload_field_commitments,
    )

    event = AuditEvent(
        sequence_number=sequence_number,
        event_type=event_type,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload,
        payload_field_commitments=payload_field_commitments,
        timestamp=timestamp,
        recorded_at=recorded_at,
        content_hash=content_hash,
        prev_hash=prev_hash,
    )
    session.add(event)
    await session.flush()
    return event

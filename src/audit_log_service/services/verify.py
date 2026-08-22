from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.hashing import (
    GENESIS_HASH,
    compute_content_hash,
    field_hash,
    payload_commitment_from_hashes,
    record_hash,
)
from audit_log_service.core.redaction import is_redaction_marker
from audit_log_service.models import AuditEvent
from audit_log_service.schemas.verify import VerifyResult, ViolationType


def _recompute_content_hash(record: AuditEvent) -> str:
    """Recomputes content_hash from a non-archived record's current stored fields.

    For each payload field: a redaction marker uses the retained hash directly
    (Scenario B 3a/3f — proves nothing else about that field, by design); any other
    field is freshly rehashed from its current value, so tampering with a
    non-redacted field is caught exactly like tampering with any other field.
    """
    assert record.event_type is not None
    assert record.actor_id is not None
    assert record.resource_type is not None
    assert record.resource_id is not None
    assert record.timestamp is not None
    assert record.recorded_at is not None

    payload = record.payload or {}
    commitments = record.payload_field_commitments or {}

    field_hashes: dict[str, str] = {}
    for key, value in payload.items():
        if is_redaction_marker(value):
            field_hashes[key] = commitments[key]["hash"]
        else:
            field_hashes[key] = field_hash(commitments[key]["salt"], key, value)

    return compute_content_hash(
        sequence_number=record.sequence_number,
        recorded_at=record.recorded_at,
        event_type=record.event_type,
        actor_id=record.actor_id,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        timestamp=record.timestamp,
        payload_commitment_value=payload_commitment_from_hashes(field_hashes),
    )


async def verify_chain(session: AsyncSession) -> VerifyResult:
    """Walks the full chain in sequence_number order, fail-fast (8b): stops and
    reports at the first inconsistency. DB-streamed (7e) — iterates a server-side
    cursor rather than loading every record into memory at once.
    """
    query = select(AuditEvent).order_by(AuditEvent.sequence_number.asc())
    stream = await session.stream_scalars(query)

    expected_sequence_number = 1
    previous: AuditEvent | None = None

    async for record in stream:
        if record.sequence_number != expected_sequence_number:
            if expected_sequence_number == 1:
                return VerifyResult(
                    intact=False,
                    sequence_number=record.sequence_number,
                    violation_type=ViolationType.GENESIS_MISMATCH,
                    detail=(
                        "No record with sequence_number=1 found; chain begins at "
                        f"sequence_number={record.sequence_number}"
                    ),
                )
            missing_from = expected_sequence_number
            missing_to = record.sequence_number - 1
            missing = (
                str(missing_from)
                if missing_from == missing_to
                else f"{missing_from}-{missing_to}"
            )
            return VerifyResult(
                intact=False,
                sequence_number=record.sequence_number,
                violation_type=ViolationType.LINK_MISMATCH,
                detail=f"Missing record(s) with sequence_number {missing}",
            )

        if record.sequence_number == 1:
            if record.prev_hash != GENESIS_HASH:
                return VerifyResult(
                    intact=False,
                    sequence_number=1,
                    violation_type=ViolationType.GENESIS_MISMATCH,
                    detail="Record 1's prev_hash does not match the genesis constant",
                )
        else:
            assert previous is not None
            expected_prev_hash = record_hash(previous.content_hash, previous.prev_hash)
            if record.prev_hash != expected_prev_hash:
                return VerifyResult(
                    intact=False,
                    sequence_number=record.sequence_number,
                    violation_type=ViolationType.LINK_MISMATCH,
                    detail=(
                        f"Record {record.sequence_number}'s prev_hash does not match "
                        f"the expected chain hash of record {previous.sequence_number}"
                    ),
                )

        if not record.archived:
            recomputed = _recompute_content_hash(record)
            if recomputed != record.content_hash:
                return VerifyResult(
                    intact=False,
                    sequence_number=record.sequence_number,
                    violation_type=ViolationType.CONTENT_MISMATCH,
                    detail=(
                        f"Record {record.sequence_number}'s content_hash does not "
                        "match a fresh recomputation from its stored fields"
                    ),
                )

        expected_sequence_number = record.sequence_number + 1
        previous = record

    return VerifyResult(intact=True)

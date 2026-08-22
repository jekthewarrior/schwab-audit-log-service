from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.hashing import canonical_timestamp
from audit_log_service.core.invariants import require_not_none
from audit_log_service.core.redaction import is_redaction_marker
from audit_log_service.models import AuditEvent
from audit_log_service.services.append import append_event


class RecordNotFoundError(Exception):
    pass


class RecordArchivedError(Exception):
    pass


class FieldNotFoundError(Exception):
    pass


class FieldAlreadyRedactedError(Exception):
    pass


async def redact_field(
    session: AsyncSession, *, sequence_number: int, field: str, actor_id: str
) -> AuditEvent:
    """Redacts one top-level payload field on a record. Must run via a session
    bound to maintenance_role — app_role has no UPDATE privilege at all
    (REQUIREMENTS.md 2a; Scenario B's DB privilege decision).

    content_hash is never touched: it's built from payload_field_commitments (6c
    amendment / 3a), retained unchanged forever, so the record continues to verify
    correctly with no special-casing needed by verify itself (3f). Irreversible by
    design (3c) — the raw value is overwritten, not archived elsewhere.

    Appends a FIELD_REDACTED system event in the same transaction (3b) — the
    authorization trail that makes "this record looks incomplete" verifiable rather
    than suspicious.
    """
    target = await session.get(AuditEvent, sequence_number)
    if target is None:
        raise RecordNotFoundError(sequence_number)
    if target.archived:
        # Nothing to redact — retention already discarded this record's content.
        raise RecordArchivedError(sequence_number)

    payload = target.payload or {}
    if field not in payload:
        raise FieldNotFoundError(field)
    if is_redaction_marker(payload[field]):
        raise FieldAlreadyRedactedError(field)

    invariant = "non-archived record must have this field populated"
    resource_type = require_not_none(target.resource_type, invariant)
    resource_id = require_not_none(target.resource_id, invariant)

    redaction_event = await append_event(
        session,
        event_type="FIELD_REDACTED",
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        payload={"redactedSequenceNumber": sequence_number, "field": field},
        timestamp=datetime.now(UTC),
    )

    new_payload = dict(payload)
    new_payload[field] = {
        "__redacted__": True,
        "redactedAt": canonical_timestamp(datetime.now(UTC)),
        "redactionEventSeq": redaction_event.sequence_number,
    }
    target.payload = new_payload

    await session.flush()
    return target

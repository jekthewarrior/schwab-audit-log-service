"""B4.1 — redaction preserves chain validity, and non-redacted fields in the same
record remain independently tamper-detectable. This is the property that justified
per-field commitments over a whole-payload hash (REQUIREMENTS.md Scenario B 3a).
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.models import AuditEvent
from audit_log_service.schemas.verify import ViolationType
from audit_log_service.services.append import append_event
from audit_log_service.services.query import list_events
from audit_log_service.services.redact import (
    FieldAlreadyRedactedError,
    FieldNotFoundError,
    RecordArchivedError,
    RecordNotFoundError,
    redact_field,
)
from audit_log_service.services.retention import sweep_retention
from audit_log_service.services.verify import verify_chain


async def _write_with_sensitive_field(session: AsyncSession) -> None:
    await append_event(
        session,
        event_type="RECORD_UPDATED",
        actor_id="user-1",
        resource_type="ACCOUNT",
        resource_id="acct-1",
        payload={"accountNumber": "123-45-6789", "note": "routine update"},
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await session.commit()


async def test_redaction_leaves_content_hash_unchanged(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_with_sensitive_field(app_session)
    before = await app_session.get(AuditEvent, 1)
    assert before is not None
    original_content_hash = before.content_hash

    await redact_field(
        maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
    )
    await maintenance_session.commit()

    after = await app_session.get(AuditEvent, 1, populate_existing=True)
    assert after is not None
    assert after.content_hash == original_content_hash
    assert after.payload is not None
    redacted_field = after.payload["accountNumber"]
    assert isinstance(redacted_field, dict)
    assert redacted_field["__redacted__"] is True


async def test_verify_stays_intact_after_redaction(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_with_sensitive_field(app_session)
    await redact_field(
        maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
    )
    await maintenance_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is True


async def test_redact_returns_the_target_record_not_the_audit_event(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    """redact_field returns the redacted target record (its new state, with the
    marker in place) — matching what the API endpoint hands back to its caller —
    not the FIELD_REDACTED event it appends alongside it.
    """
    await _write_with_sensitive_field(app_session)
    result = await redact_field(
        maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
    )
    await maintenance_session.commit()

    assert result.sequence_number == 1
    assert result.event_type == "RECORD_UPDATED"


async def test_field_redacted_event_is_discoverable(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    """3f's discoverability answer: an investigator finds redaction actions via the
    ordinary query endpoint, not via redact_field's return value.
    """
    await _write_with_sensitive_field(app_session)
    await redact_field(
        maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
    )
    await maintenance_session.commit()

    records, _ = await list_events(app_session, event_type="FIELD_REDACTED")
    assert len(records) == 1
    event = records[0]
    assert event.sequence_number == 2
    assert event.payload == {"redactedSequenceNumber": 1, "field": "accountNumber"}


async def test_tampering_a_non_redacted_field_is_still_caught(
    app_session: AsyncSession, maintenance_session: AsyncSession, admin_session: AsyncSession
) -> None:
    """The core property motivating per-field commitments (REQUIREMENTS.md Scenario
    B 3a): legitimately redacting one field must not disable tamper-detection for
    every other field in the same payload.
    """
    await _write_with_sensitive_field(app_session)
    await redact_field(
        maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
    )
    await maintenance_session.commit()

    await admin_session.execute(
        text(
            "UPDATE audit_events SET payload = jsonb_set(payload, '{note}', '\"TAMPERED\"') "
            "WHERE sequence_number = 1"
        )
    )
    await admin_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is False
    assert result.sequence_number == 1
    assert result.violation_type == ViolationType.CONTENT_MISMATCH


async def test_tampering_the_retained_hash_of_a_redacted_field_is_caught(
    app_session: AsyncSession, maintenance_session: AsyncSession, admin_session: AsyncSession
) -> None:
    """The retained per-field hash is itself protected transitively via
    content_hash — not a trusted exemption from the chain's guarantee.
    """
    await _write_with_sensitive_field(app_session)
    await redact_field(
        maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
    )
    await maintenance_session.commit()

    await admin_session.execute(
        text(
            "UPDATE audit_events SET payload_field_commitments = "
            "jsonb_set(payload_field_commitments, '{accountNumber,hash}', "
            "'\"" + ("0" * 64) + "\"') WHERE sequence_number = 1"
        )
    )
    await admin_session.commit()

    result = await verify_chain(app_session)
    assert result.intact is False
    assert result.violation_type == ViolationType.CONTENT_MISMATCH


async def test_redact_nonexistent_record_raises(maintenance_session: AsyncSession) -> None:
    with pytest.raises(RecordNotFoundError):
        await redact_field(
            maintenance_session, sequence_number=999, field="x", actor_id="compliance-1"
        )


async def test_redact_nonexistent_field_raises(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_with_sensitive_field(app_session)
    with pytest.raises(FieldNotFoundError):
        await redact_field(
            maintenance_session, sequence_number=1, field="nope", actor_id="compliance-1"
        )


async def test_redact_already_redacted_field_raises(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_with_sensitive_field(app_session)
    await redact_field(
        maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
    )
    await maintenance_session.commit()

    with pytest.raises(FieldAlreadyRedactedError):
        await redact_field(
            maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
        )


async def test_redact_archived_record_raises(
    app_session: AsyncSession, maintenance_session: AsyncSession
) -> None:
    await _write_with_sensitive_field(app_session)
    await maintenance_session.execute(
        text(
            "UPDATE audit_events SET recorded_at = now() - interval '10 years' "
            "WHERE sequence_number = 1"
        )
    )
    await maintenance_session.commit()
    await sweep_retention(maintenance_session, actor_id="cron")
    await maintenance_session.commit()

    with pytest.raises(RecordArchivedError):
        await redact_field(
            maintenance_session, sequence_number=1, field="accountNumber", actor_id="compliance-1"
        )

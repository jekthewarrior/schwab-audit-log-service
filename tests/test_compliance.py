"""C2 — Scenario C's compliance-reporting scenario, end to end: a mix of access and
non-access events for one account, exported scoped to just the access events within
a time window, independently verifiable. Confirms the Clarified Requirement
Statement's technical design (reusing Scenario B's export with eventType/from/to
filters) actually delivers what a compliance officer would need.
"""

import json
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.signing import public_key_hex
from audit_log_service.services.append import append_event
from audit_log_service.services.export import export_bundle


async def test_compliance_export_scopes_to_access_events_in_window(
    app_session: AsyncSession,
) -> None:
    # Mix of access and non-access events for the same account, spanning a wider
    # window than the report will request.
    await append_event(
        app_session,
        event_type="ACCOUNT_VIEWED",
        actor_id="employee-1",
        resource_type="ACCOUNT",
        resource_id="acct-123",
        payload={},
        timestamp=datetime(2026, 1, 15, tzinfo=UTC),  # in window
    )
    await append_event(
        app_session,
        event_type="RECORD_UPDATED",
        actor_id="employee-1",
        resource_type="ACCOUNT",
        resource_id="acct-123",
        payload={"field": "balance"},
        timestamp=datetime(2026, 2, 1, tzinfo=UTC),  # in window, but not an access event
    )
    await append_event(
        app_session,
        event_type="ACCOUNT_VIEWED",
        actor_id="employee-2",
        resource_type="ACCOUNT",
        resource_id="acct-123",
        payload={},
        timestamp=datetime(2026, 8, 1, tzinfo=UTC),  # an access event, but outside the window
    )
    await app_session.commit()

    bundle = await export_bundle(
        app_session,
        resource_id="acct-123",
        event_type="ACCOUNT_VIEWED",
        from_=datetime(2026, 1, 1, tzinfo=UTC),
        to=datetime(2026, 6, 30, tzinfo=UTC),
    )

    assert len(bundle.records) == 1
    assert bundle.records[0].event_type == "ACCOUNT_VIEWED"
    assert bundle.records[0].actor_id == "employee-1"

    # Independently verifiable — the point of a compliance report handed to a
    # regulator who has no other access to this system.
    bundle_json = bundle.model_dump(mode="json", by_alias=True)
    signature_hex = bundle_json.pop("signature")
    canonical = json.dumps(
        bundle_json, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex()))
    try:
        public_key.verify(bytes.fromhex(signature_hex), canonical)
    except InvalidSignature:
        raise AssertionError("compliance export bundle signature did not verify") from None

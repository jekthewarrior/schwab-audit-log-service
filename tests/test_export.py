"""B4.4 — export bundle signature. Verifies independently, using only
`cryptography`'s primitives directly against the bundle's own JSON content (mirrors
the manual third-party verification done live during B3's implementation) — proves
the signature is meaningful, not just present.
"""

import json
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.signing import public_key_hex
from audit_log_service.services.append import append_event
from audit_log_service.services.export import NoFilterProvidedError, export_bundle


def _independently_verify(bundle_json: dict[str, object]) -> bool:
    bundle = dict(bundle_json)
    signature_hex = bundle.pop("signature")
    assert isinstance(signature_hex, str)
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex()))
    try:
        public_key.verify(bytes.fromhex(signature_hex), canonical)
        return True
    except InvalidSignature:
        return False


async def test_export_signature_verifies_independently(app_session: AsyncSession) -> None:
    await append_event(
        app_session,
        event_type="ACCOUNT_VIEWED",
        actor_id="user-1",
        resource_type="ACCOUNT",
        resource_id="acct-1",
        payload={"note": "view"},
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await app_session.commit()

    bundle = await export_bundle(app_session, resource_id="acct-1")
    bundle_json = bundle.model_dump(mode="json", by_alias=True)

    assert _independently_verify(bundle_json) is True


async def test_export_signature_rejects_content_and_hash_tampered_together(
    app_session: AsyncSession,
) -> None:
    """The exact attack self-consistency checks alone can't catch (REQUIREMENTS.md
    Scenario B 5a) — an attacker editing a record's content and its accompanying
    hash together, within the same bundle file.
    """
    await append_event(
        app_session,
        event_type="ACCOUNT_VIEWED",
        actor_id="user-1",
        resource_type="ACCOUNT",
        resource_id="acct-1",
        payload={"note": "view"},
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await app_session.commit()

    bundle = await export_bundle(app_session, resource_id="acct-1")
    bundle_json = bundle.model_dump(mode="json", by_alias=True)

    records = bundle_json["records"]
    assert isinstance(records, list)
    tampered_record = dict(records[0])
    tampered_record["payload"] = {"note": "TAMPERED"}
    tampered_record["contentHash"] = "f" * 64
    bundle_json["records"] = [tampered_record]

    assert _independently_verify(bundle_json) is False


async def test_export_requires_resource_id_or_actor_id(app_session: AsyncSession) -> None:
    with pytest.raises(NoFilterProvidedError):
        await export_bundle(app_session)

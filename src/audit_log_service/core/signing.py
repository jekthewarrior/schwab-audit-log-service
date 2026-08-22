from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from audit_log_service.core.config import settings

_seed = bytes.fromhex(settings.export_signing_key_seed_hex)
_private_key = Ed25519PrivateKey.from_private_bytes(_seed)
_public_key: Ed25519PublicKey = _private_key.public_key()

SIGNING_KEY_ID = settings.export_signing_key_id


def sign(data: bytes) -> bytes:
    """Signs export bundles (REQUIREMENTS.md Scenario B 5c). Asymmetric, not
    HMAC/symmetric — the recipient is a genuine external third party (regulator,
    auditor), so a shared secret would let anyone able to verify also forge new
    bundles.
    """
    return _private_key.sign(data)


def public_key_hex() -> str:
    """Exposed via an endpoint so a recipient can fetch the current public key to
    verify a bundle against, without needing separate out-of-band distribution.
    """
    return _public_key.public_bytes_raw().hex()

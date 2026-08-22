import hashlib
import json
import secrets
from datetime import UTC, datetime

GENESIS_HASH = "0" * 64


def canonical_json(value: object) -> bytes:
    """RFC 8785-inspired canonical serialization, per REQUIREMENTS.md 6c.

    Operates on the parsed Python value, not raw bytes — Postgres JSONB doesn't
    guarantee byte-for-byte round-tripping of submitted JSON text, so hashes must be
    regenerated from the logical value both at write time and at verify time.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_timestamp(dt: datetime) -> str:
    """Fixed-precision ISO-8601 UTC, per REQUIREMENTS.md 6c."""
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_salt() -> str:
    return secrets.token_hex(16)


def field_hash(salt: str, key: str, value: object) -> str:
    """Per-field salted commitment, per REQUIREMENTS.md Scenario B 3a.

    Hashes [salt, key, value] as one canonical JSON array rather than concatenating
    the three pieces as separate strings — avoids any ambiguity from
    variable-length-string concatenation (e.g. "ab"+"c" colliding with "a"+"bc").
    """
    return sha256_hex(canonical_json([salt, key, value]))


def compute_payload_commitments(payload: dict[str, object]) -> dict[str, dict[str, str]]:
    """Fresh salted commitment for every top-level payload field.

    Called once at write time. Redaction (Scenario B 3a) later overwrites a field's
    raw value but retains its (hash, salt) entry here unchanged forever.
    """
    return {key: {"hash": field_hash(salt := new_salt(), key, value), "salt": salt}
            for key, value in payload.items()}


def payload_commitment_from_hashes(hashes_by_field: dict[str, str]) -> str:
    """Commits to a *set* of per-field hashes — substitutes for the raw payload
    value as content_hash's eighth input (REQUIREMENTS.md 6c amendment / Scenario B
    3a). Takes a plain {field: hash} mapping so both the append service (all fresh
    hashes) and verify (a mix of freshly recomputed and retained-for-redacted-fields
    hashes) can share this same function.
    """
    return sha256_hex(canonical_json(hashes_by_field))


def payload_commitment(field_commitments: dict[str, dict[str, str]]) -> str:
    """Write-time convenience: extracts the hash from each {hash, salt} entry and
    delegates to payload_commitment_from_hashes.
    """
    hashes_by_field = {key: entry["hash"] for key, entry in field_commitments.items()}
    return payload_commitment_from_hashes(hashes_by_field)


def compute_content_hash(
    *,
    sequence_number: int,
    recorded_at: datetime,
    event_type: str,
    actor_id: str,
    resource_type: str,
    resource_id: str,
    timestamp: datetime,
    payload_commitment_value: str,
) -> str:
    """content_hash per REQUIREMENTS.md 6b/6c — covers every persisted field except
    prev_hash, which is deliberately excluded and kept as a separate field (6b).
    Takes the already-computed payload commitment rather than the raw commitments
    dict, so callers control how that value was derived (fresh at write time,
    mixed fresh/retained at verify time).
    """
    content = {
        "sequence_number": sequence_number,
        "recorded_at": canonical_timestamp(recorded_at),
        "event_type": event_type,
        "actor_id": actor_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "timestamp": canonical_timestamp(timestamp),
        "payload_commitment": payload_commitment_value,
    }
    return sha256_hex(canonical_json(content))


def record_hash(content_hash: str, prev_hash: str) -> str:
    """The combined hash the *next* record's prev_hash points to (REQUIREMENTS.md
    6d). Not stored — always derived. Plain string concatenation is safe here
    (unlike field_hash) since both inputs are fixed-length 64-char hex strings, so
    there's no concatenation ambiguity to worry about.
    """
    return sha256_hex((content_hash + prev_hash).encode("utf-8"))

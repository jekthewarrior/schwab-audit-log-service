from datetime import UTC, datetime

from audit_log_service.core.hashing import (
    canonical_json,
    compute_content_hash,
    compute_payload_commitments,
    field_hash,
    payload_commitment,
    payload_commitment_from_hashes,
    record_hash,
)


def test_canonical_json_is_key_order_independent() -> None:
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b


def test_canonical_json_is_deterministic_across_calls() -> None:
    value = {"nested": {"z": 1, "a": [3, 2, 1]}, "x": "y"}
    assert canonical_json(value) == canonical_json(value)


def test_field_hash_differs_for_different_salts() -> None:
    # Regression test for the brute-force risk identified in REQUIREMENTS.md 3a:
    # two records with the same sensitive value must not produce the same hash.
    h1 = field_hash("salt-a", "accountNumber", "123-45-6789")
    h2 = field_hash("salt-b", "accountNumber", "123-45-6789")
    assert h1 != h2


def test_field_hash_is_deterministic_for_same_inputs() -> None:
    assert field_hash("salt", "key", "value") == field_hash("salt", "key", "value")


def test_payload_commitment_only_depends_on_hashes_not_salts() -> None:
    commitments = compute_payload_commitments({"a": 1, "b": "two"})
    # Same hashes, different (irrelevant) salts stored alongside them.
    reconstructed = {k: {"hash": v["hash"], "salt": "different"} for k, v in commitments.items()}
    assert payload_commitment(commitments) == payload_commitment(reconstructed)


def test_payload_commitment_agrees_with_from_hashes() -> None:
    # payload_commitment (write-time convenience) and payload_commitment_from_hashes
    # (shared by verify's mixed fresh/retained reconstruction) must produce the same
    # value for the same underlying hashes.
    commitments = compute_payload_commitments({"a": 1, "b": "two"})
    hashes_by_field = {k: v["hash"] for k, v in commitments.items()}
    assert payload_commitment(commitments) == payload_commitment_from_hashes(hashes_by_field)


def test_content_hash_changes_if_any_covered_field_changes() -> None:
    commitment_value = payload_commitment(compute_payload_commitments({"ip": "127.0.0.1"}))
    recorded_at = datetime(2026, 1, 1, tzinfo=UTC)
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)

    original = compute_content_hash(
        sequence_number=1,
        recorded_at=recorded_at,
        event_type="USER_LOGIN",
        actor_id="user-1",
        resource_type="SESSION",
        resource_id="sess-1",
        timestamp=timestamp,
        payload_commitment_value=commitment_value,
    )

    # sequence_number is covered by content_hash (REQUIREMENTS.md 6b) precisely so
    # that direct-datastore resequencing is detectable.
    tampered = compute_content_hash(
        sequence_number=2,
        recorded_at=recorded_at,
        event_type="USER_LOGIN",
        actor_id="user-1",
        resource_type="SESSION",
        resource_id="sess-1",
        timestamp=timestamp,
        payload_commitment_value=commitment_value,
    )
    assert original != tampered


def test_record_hash_cascades_from_prev_hash() -> None:
    # The core property from REQUIREMENTS.md 6d: prev_hash is an input to
    # record_hash, so changing a record's link changes its own record_hash too.
    content = "a" * 64
    h1 = record_hash(content, "b" * 64)
    h2 = record_hash(content, "c" * 64)
    assert h1 != h2

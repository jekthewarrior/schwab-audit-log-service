def is_redaction_marker(value: object) -> bool:
    """REQUIREMENTS.md Scenario B 3e's marker shape:
    {"__redacted__": true, "redactedAt": ..., "redactionEventSeq": ...}.

    Shared between services/redact.py (writes the marker) and services/verify.py
    (must recognize it during content-hash reconstruction, per 3a/3f) — kept as one
    definition rather than duplicated so the two can't drift out of sync.
    """
    return isinstance(value, dict) and value.get("__redacted__") is True

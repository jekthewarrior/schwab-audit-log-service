from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from audit_log_service.schemas.event import AuditEventOut, CanonicalDatetime


class ChainTailSnapshot(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    sequence_number: int
    record_hash: str


class ExportBundle(BaseModel):
    """REQUIREMENTS.md Scenario B 5c. `signature` is computed over the canonical
    serialization of every other field — see services/export.py.

    `filter` values are pre-converted to their canonical string form by the export
    service before reaching this model (rather than typed as `str | datetime` here)
    — keeps every value in the signed structure going through the same
    canonical_timestamp path with no risk of a Union type picking a different
    serialization route for one branch.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    exported_at: CanonicalDatetime
    filter: dict[str, str]
    chain_tail_snapshot: ChainTailSnapshot | None
    records: list[AuditEventOut]
    signing_key_id: str
    signature: str

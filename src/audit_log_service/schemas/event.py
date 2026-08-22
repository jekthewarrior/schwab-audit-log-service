import json
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

MAX_PAYLOAD_BYTES = 32 * 1024
MAX_PAYLOAD_DEPTH = 10


def _payload_depth(value: object, current: int = 1) -> int:
    if isinstance(value, dict) and value:
        return max(_payload_depth(v, current + 1) for v in value.values())
    if isinstance(value, list) and value:
        return max(_payload_depth(v, current + 1) for v in value)
    return current


class AuditEventCreate(BaseModel):
    """Write API request body. REQUIREMENTS.md 1a-1d: all six fields mandatory,
    eventType format-constrained (not a hard enum), payload is a JSON object with
    size/depth caps.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    event_type: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$", min_length=1)]
    actor_id: Annotated[str, Field(min_length=1)]
    resource_type: Annotated[str, Field(min_length=1)]
    resource_id: Annotated[str, Field(min_length=1)]
    payload: dict[str, object]
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @field_validator("payload")
    @classmethod
    def payload_must_be_within_limits(cls, value: dict[str, object]) -> dict[str, object]:
        size = len(json.dumps(value).encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES}-byte limit")
        depth = _payload_depth(value)
        if depth > MAX_PAYLOAD_DEPTH:
            raise ValueError(f"payload exceeds max nesting depth of {MAX_PAYLOAD_DEPTH}")
        return value


class AuditEventOut(BaseModel):
    """Write/query API response representation of a stored record."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    sequence_number: int
    event_type: str | None
    actor_id: str | None
    resource_type: str | None
    resource_id: str | None
    payload: dict[str, object] | None
    timestamp: datetime | None
    recorded_at: datetime | None
    content_hash: str
    prev_hash: str
    archived: bool
    archived_at: datetime | None

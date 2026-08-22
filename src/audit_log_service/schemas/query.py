from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from audit_log_service.schemas.event import AuditEventOut


class AuditEventPage(BaseModel):
    """Cursor-paginated response for GET /audit/events. REQUIREMENTS.md 5a/5d/5b:
    keyset pagination anchored to sequence_number, descending (newest first).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    records: list[AuditEventOut]
    next_cursor: int | None

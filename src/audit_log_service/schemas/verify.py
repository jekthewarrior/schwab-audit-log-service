from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ViolationType(StrEnum):
    """REQUIREMENTS.md 8a — three categories, not four. A `SEQUENCE_GAP` was
    explicitly considered and rejected as an independent check: in the realistic
    tampering case a gap already surfaces as a LINK_MISMATCH once "previous" is
    resolved against currently-existing records, so gap detail is folded into
    LINK_MISMATCH's message instead of being a fourth peer-level category.
    """

    GENESIS_MISMATCH = "GENESIS_MISMATCH"
    CONTENT_MISMATCH = "CONTENT_MISMATCH"
    LINK_MISMATCH = "LINK_MISMATCH"


class VerifyResult(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    intact: bool
    sequence_number: int | None = None
    violation_type: ViolationType | None = None
    detail: str | None = None

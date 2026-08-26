from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class RedactRequest(BaseModel):
    """REQUIREMENTS.md Scenario B 3b: operator-facing, not policy-driven — names a
    single top-level payload field to redact (3d's scope: top-level fields, single
    record). Who performed the redaction is derived from the authenticated
    principal (C10), not a caller-supplied field — a caller-asserted identity on a
    self-auditing action would be spoofable now that there's an auth layer to
    check it against.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    field: Annotated[str, Field(min_length=1)]

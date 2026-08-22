from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class RedactRequest(BaseModel):
    """REQUIREMENTS.md Scenario B 3b: operator-facing, not policy-driven — names a
    single top-level payload field to redact (3d's scope: top-level fields, single
    record). actor_id identifies who is performing the redaction, since there's no
    auth/authz layer (out of scope) to derive it from a session.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    field: Annotated[str, Field(min_length=1)]
    actor_id: Annotated[str, Field(min_length=1)]

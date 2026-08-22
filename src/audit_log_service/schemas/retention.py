from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class RetentionSweepRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    actor_id: Annotated[str, Field(min_length=1)]


class RetentionSweepResult(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    archived_sequence_numbers: list[int]

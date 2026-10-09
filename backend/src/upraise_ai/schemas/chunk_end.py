"""B2's caller-supplied cognitive-load measurement and playback decision."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChunkEndInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    lesson_id: str = Field(min_length=1, max_length=120)
    chunk_id: UUID
    cognitive_load_score: float = Field(ge=0, le=1, strict=True, allow_inf_nan=False)


class ChunkEndResponse(BaseModel):
    decision: Literal["PROCEED", "HIGH_COGNITIVE_LOAD"]

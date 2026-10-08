"""B4 has one response shape; semantic learner goals remain unrestricted."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from upraise_ai.schemas.lessons import LessonSource, Viseme


class InterruptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    request_id: UUID
    chunk_id: UUID
    text: str | None = Field(None, min_length=1, max_length=6000)
    playback_offset_ms: StrictInt | None = Field(None, ge=0, le=86_400_000)


class PlaybackAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["REPLAY", "SET_SPEED", "PAUSE", "RESUME"]
    rate: float | None = Field(None, ge=0.5, le=2, allow_inf_nan=False)

    @model_validator(mode="after")
    def rate_only_for_speed(self):
        if (self.action == "SET_SPEED") != (self.rate is not None):
            raise ValueError("SET_SPEED requires a rate; other actions must omit it")
        return self


class LearnerIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["respond", "playback", "clarify", "blocked"]
    student_goal: str = Field(min_length=1, max_length=1000)
    is_course_question: StrictBool
    use_course_retrieval: StrictBool
    retrieval_query: str = Field(max_length=2000)
    playback_action: PlaybackAction | None = None

    @model_validator(mode="after")
    def valid_route(self):
        if self.operation == "playback" and self.playback_action is None:
            raise ValueError("playback needs an action")
        if self.operation in {"clarify", "blocked"} and self.playback_action is not None:
            raise ValueError("clarification/refusal cannot control the player")
        return self


class OutputCheck(BaseModel):
    """Internal safety/language verdict; factual grounding is deferred."""

    model_config = ConfigDict(extra="forbid")
    safe: StrictBool
    in_language: StrictBool


class InlineAudio(BaseModel):
    media_type: Literal["audio/wav"] = "audio/wav"
    encoding: Literal["base64"] = "base64"
    data: str


class InterruptResponse(BaseModel):
    request_id: UUID
    chunk_id: UUID
    answer_id: UUID | None = None
    text: str | None = None
    ssml: str | None = None
    audio: InlineAudio | None = None
    visemes: list[Viseme] = Field(default_factory=list)
    sources: list[LessonSource] = Field(default_factory=list)
    grounded: bool | None = None
    playback_action: PlaybackAction | None = None
    warnings: list[str] = Field(default_factory=list)

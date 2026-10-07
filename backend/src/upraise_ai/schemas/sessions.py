"""B1 session initialization; content and playback remain A5 responsibilities."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

Consent = Literal["questions", "telemetry"]


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    course_id: str = Field(min_length=1, max_length=120)
    activity_id: str = Field(min_length=1, max_length=120)
    lesson_id: str = Field(min_length=1, max_length=120)
    lesson_job_id: UUID
    document_ids: list[UUID] | None = Field(None, min_length=1, max_length=50)
    language: str | None = Field(None, min_length=2, max_length=35,
                                 pattern=r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$")
    # Only development local testing may supply these in the body.
    session_id: UUID | None = None
    consent_scope: list[Consent] | None = Field(None, max_length=2)

    @field_validator("document_ids", "consent_scope")
    @classmethod
    def unique_scope(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("scope entries must be unique")
        return value


class SessionClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    sub: str = Field(min_length=1, max_length=256)
    session_id: UUID
    client_id: str = Field(min_length=1, max_length=120)
    course_id: str = Field(min_length=1, max_length=120)
    activity_id: str = Field(min_length=1, max_length=120)
    lesson_id: str = Field(min_length=1, max_length=120)
    lesson_job_id: UUID
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    consent_scope: list[Consent] = Field(max_length=2)
    lesson_approved: StrictBool
    exp: StrictInt
    iat: StrictInt

    @field_validator("consent_scope")
    @classmethod
    def unique_consent(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("consent entries must be unique")
        return value


class StartedSession(BaseModel):
    session_id: UUID
    lesson_job_id: UUID
    lesson_id: str
    course_id: str
    activity_id: str
    document_ids: list[UUID]
    language: str
    learner_level: Literal["beginner", "intermediate", "advanced"]
    voice: str
    result_sha256: str
    preview: bool
    ai_notice: str
    expires_at: datetime

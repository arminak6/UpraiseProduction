"""A7 author feedback and immutable lesson revision provenance."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChunkRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    lesson_job_id: UUID
    chunk_id: UUID
    teacher_review: str = Field(min_length=1, max_length=8000)

    @field_validator("teacher_review")
    @classmethod
    def valid_text(cls, value):
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("teacher_review contains invalid control characters")
        return value


class LessonRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_job_id: UUID
    chunk_id: UUID
    number: int = Field(ge=1)


class RevisionRequest(LessonRevision):
    """Internal job metadata; never accepted by A3 or returned with feedback."""
    teacher_review: str = Field(min_length=1, max_length=8000)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_attempts: dict[str, str]
    aligned_unit: dict | None = None

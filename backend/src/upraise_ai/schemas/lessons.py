"""A3/A5 public contract; generated lessons always require human approval."""

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_serializer,
    model_validator,
)

from upraise_ai.schemas.revisions import LessonRevision, RevisionRequest


class LessonOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_window_pages: int = Field(5, ge=1, le=50)
    target_chunk_words: int = Field(200, ge=30, le=1000)
    learner_level: Literal["beginner", "intermediate", "advanced"] = "intermediate"


class LessonInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    lesson_id: str = Field(min_length=1, max_length=120)
    course_id: str = Field(min_length=1, max_length=120)
    activity_id: str = Field(min_length=1, max_length=120)
    document_ids: list[UUID] = Field(min_length=1, max_length=50)
    language: str = Field(min_length=2, max_length=35, pattern=r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$")
    voice: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z]+$")
    teaching_version: Literal["v1", "v2", "v2.9", "v3"] = "v1"
    options: LessonOptions = Field(default_factory=LessonOptions)
    callback_url: str | None = Field(None, max_length=2048)

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("document_ids must be unique and ordered")
        return value

    @field_validator("callback_url")
    @classmethod
    def empty_callback_is_pull_only(cls, value):
        return value or None


class AcceptedLesson(BaseModel):
    job_id: str


class StoredLessonOptions(LessonOptions):
    learner_level: str = Field("intermediate", min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    max_chunk_words: int = Field(500, ge=50, le=2000)


class StoredLessonInput(LessonInput):
    """Worker-only metadata extension; A3's public request remains unchanged."""
    revision_request: RevisionRequest | None = None
    generation_version: Literal["a3-v2"] | None = None
    level_profiles: dict[str, str] | None = None
    plan_level: str | None = None
    levels: list[str] | None = None

    # Internal aligned revisions may use operator-configured level names.
    # The legacy A3 public schema retains its original three choices.
    options: StoredLessonOptions = Field(default_factory=StoredLessonOptions)


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["accuracy", "citations", "clarity", "bias", "coverage", "source", "speech"]
    message: str = Field(min_length=1, max_length=4000)
    pages: list[StrictInt] = Field(default_factory=list)
    row_ranges: list[dict[str, int]] = Field(default_factory=list)
    source_blocks: list[int] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_issue(self, handler):
        data = handler(self)
        if not self.row_ranges:
            data.pop("row_ranges", None)
        if not self.source_blocks:
            data.pop("source_blocks", None)
        return data


class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[ReviewIssue] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if self.approved and (self.issues or self.revision_instructions):
            raise ValueError("approved reviews cannot contain unresolved issues")
        if not self.approved and not self.issues:
            raise ValueError("rejected reviews must explain the issues")
        return self


class DraftParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=30000)
    pages: list[StrictInt] = Field(min_length=1, max_length=5000)


class SectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paragraphs: list[DraftParagraph] = Field(min_length=1, max_length=200)


class LessonSource(BaseModel):
    document_id: str
    document_version_id: str
    pages: list[int]
    source_chunk_ids: list[str]
    quality_status: str
    source_format: str = "pdf"
    source_blocks: list[int] = Field(default_factory=list)
    row_ranges: list[dict[str, int]] = Field(default_factory=list)
    word_references: list[dict] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_source(self, handler):
        data = handler(self)
        if self.source_format == "pdf":
            for field in ("source_format", "source_blocks", "row_ranges"):
                data.pop(field, None)
        if self.source_format not in {"doc", "docx"}:
            data.pop("word_references", None)
        return data

    @property
    def citation_numbers(self):
        return self.pages if self.source_format == "pdf" else self.source_blocks


class Viseme(BaseModel):
    time_ms: float = Field(ge=0, allow_inf_nan=False)
    value: str


class LessonChunk(BaseModel):
    chunk_id: str
    content_unit_id: UUID | None = None
    index: int = Field(ge=0)
    text: str
    ssml: str
    # Each entry is one exact Polly request. The outer SSML represents the chunk.
    ssml_parts: list[str]
    audio_url: str
    audio_media_type: Literal["audio/wav"] = "audio/wav"
    audio_sha256: str
    visemes: list[Viseme]
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    sources: list[LessonSource]
    reviewer_report: ReviewReport
    section_index: int
    revisions: int = Field(ge=0, le=1)


class LessonResult(BaseModel):
    job_id: str
    lesson_id: str
    course_id: str
    activity_id: str
    status: Literal["DRAFT"] = "DRAFT"
    label: Literal["AI-generated"] = "AI-generated"
    language: str
    voice: str
    learner_level: str
    learner_level_applied: bool = False
    chunks: list[LessonChunk]
    reviewer_report: ReviewReport
    versions: dict[str, str]
    expires_at: str | None = None
    revision: LessonRevision | None = None
    generation_version: Literal["a3-v2"] | None = None
    chunk_plan_id: UUID | None = None
    source_attempts: dict[str, str] = Field(default_factory=dict)

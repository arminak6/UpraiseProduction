"""A3 v2: one job, one teaching plan, aligned difficulty variants."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from upraise_ai.schemas.lessons import LessonInput, LessonResult, LessonSource, ReviewReport


class AlignedOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_window_pages: int = Field(5, ge=1, le=50)
    target_chunk_words: int = Field(200, ge=30, le=1000)
    max_chunk_words: int = Field(500, ge=50, le=2000)

    @model_validator(mode="after")
    def bounded_target(self):
        if self.target_chunk_words > self.max_chunk_words:
            raise ValueError("target_chunk_words must not exceed max_chunk_words")
        return self


class AlignedLessonInput(LessonInput):
    options: AlignedOptions = Field(default_factory=AlignedOptions)
    levels: list[Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,39}$")]] | None = Field(
        default=None, min_length=1, max_length=8)

    @field_validator("levels")
    @classmethod
    def unique_levels(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("levels must be unique")
        return value


class ContentUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_unit_id: UUID
    index: int = Field(ge=0)
    section_index: int = Field(ge=0)
    source_language: str
    material_text: str = Field(min_length=1, max_length=30000)
    source: LessonSource
    canonical_review: ReviewReport | None = None


class ChunkPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_plan_id: UUID
    version: Literal["aligned-plan-v1"] = "aligned-plan-v1"
    source_attempts: dict[str, str]
    units: list[ContentUnit] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def ordered_unique_units(self):
        if ([unit.index for unit in self.units] != list(range(len(self.units)))
                or len({unit.content_unit_id for unit in self.units}) != len(self.units)):
            raise ValueError("plan units must have unique IDs and contiguous indexes")
        return self


class AlignedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_unit_id: UUID
    text: str = Field(min_length=1, max_length=16000)


class AlignedTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    units: list[AlignedText] = Field(min_length=1, max_length=8)


class UnitReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_unit_id: UUID
    material_preserved: StrictBool
    level_suitable: StrictBool
    in_language: StrictBool
    issues: list[str] = Field(default_factory=list, max_length=20)
    revision_instructions: list[str] = Field(default_factory=list, max_length=20)


class AlignedReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    units: list[UnitReview] = Field(min_length=1, max_length=8)


class AlignedLessonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_version: Literal["a3-v2"] = "a3-v2"
    job_id: str
    lesson_id: str
    course_id: str
    activity_id: str
    language: str
    voice: str
    status: Literal["DRAFT"] = "DRAFT"
    label: Literal["AI-generated"] = "AI-generated"
    chunk_plan_id: UUID
    content_unit_ids: list[UUID]
    chunk_count: int = Field(ge=1)
    variants: dict[str, LessonResult]
    reviewer_report: ReviewReport
    versions: dict[str, str]
    source_attempts: dict[str, str]
    expires_at: str | None = None

    @model_validator(mode="after")
    def matching_variants(self):
        if (not self.variants or len(self.content_unit_ids) != self.chunk_count
                or len(set(self.content_unit_ids)) != self.chunk_count):
            raise ValueError("aligned variants require a complete shared plan")
        ids = []
        for level, variant in self.variants.items():
            if (variant.job_id != self.job_id or variant.lesson_id != self.lesson_id
                    or variant.course_id != self.course_id or variant.activity_id != self.activity_id
                    or variant.language != self.language or variant.voice != self.voice
                    or variant.learner_level != level or variant.chunk_plan_id != self.chunk_plan_id
                    or [chunk.index for chunk in variant.chunks] != list(range(self.chunk_count))
                    or [chunk.content_unit_id for chunk in variant.chunks] != self.content_unit_ids):
                raise ValueError("every variant must follow the same ordered teaching units and lesson context")
            ids.extend(chunk.chunk_id for chunk in variant.chunks)
        if len(set(ids)) != len(ids):
            raise ValueError("audio/text chunk IDs must be unique across variants")
        return self

    @property
    def chunks(self):
        return [chunk for variant in self.variants.values() for chunk in variant.chunks]

    @property
    def revision(self):
        return None

"""Retained teaching text for author review, independent of temporary audio."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from upraise_ai.schemas.lessons import LessonSource, ReviewReport


class ReviewLessonSummary(BaseModel):
    job_id: str
    lesson_id: str
    course_id: str
    activity_id: str
    language: str
    voice: str
    status: Literal["DRAFT"] = "DRAFT"
    job_status: str
    levels: list[str]
    chunk_counts: dict[str, int]
    created_at: datetime
    audio_expires_at: datetime | None
    revision_available: bool
    revision_unavailable_reason: str | None = None


class ReviewLessonChunk(BaseModel):
    chunk_id: str
    index: int = Field(ge=0)
    text: str
    sources: list[LessonSource]
    reviewer_report: ReviewReport


class ReviewLessonLevel(BaseModel):
    learner_level: str
    chunks: list[ReviewLessonChunk]


class ReviewLessonText(ReviewLessonSummary):
    variants: dict[str, ReviewLessonLevel]

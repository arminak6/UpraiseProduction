"""Content-free metadata for A8's ready-lesson catalog."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ReadyLesson(BaseModel):
    job_id: str
    lesson_id: str
    course_id: str
    activity_id: str
    language: str
    voice: str
    learner_level: str
    chunk_count: int
    status: Literal["DRAFT"] = "DRAFT"
    label: Literal["AI-generated"] = "AI-generated"
    requires_review: bool
    review_issue_count: int
    created_at: datetime
    expires_at: datetime | None

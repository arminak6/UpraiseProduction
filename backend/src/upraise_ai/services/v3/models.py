from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "learning-v1"
PAGE_WINDOW_SIZE = 5
EASY_LECTURE_PROMPT_VERSION = "learning-v1-easy-6"


class LecturePageWindow(BaseModel):
    """One exact Learning V1 page window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)

    @field_validator("page_end")
    @classmethod
    def validate_page_end(cls, value: int, info) -> int:
        page_start = info.data.get("page_start")
        if page_start is not None and value < page_start:
            raise ValueError("page_end cannot be before page_start.")
        if page_start is not None and value - page_start >= PAGE_WINDOW_SIZE:
            raise ValueError(
                f"A Learning V1 window cannot exceed {PAGE_WINDOW_SIZE} pages."
            )
        return value

    @field_validator("page_start")
    @classmethod
    def require_v1_boundary(cls, value: int) -> int:
        if (value - 1) % PAGE_WINDOW_SIZE:
            raise ValueError(
                "A Learning V1 page window must start at 1, 6, 11, and so on."
            )
        return value


def five_page_windows(total_pages: int) -> list[LecturePageWindow]:
    """Return the complete, deterministic five-page plan for a document."""

    if total_pages < 1:
        raise ValueError("A learning document must have at least one page.")
    return [
        LecturePageWindow(
            page_start=page_start,
            page_end=min(
                page_start + PAGE_WINDOW_SIZE - 1,
                total_pages,
            ),
        )
        for page_start in range(1, total_pages + 1, PAGE_WINDOW_SIZE)
    ]


class PreparedLecture(BaseModel):
    """A reusable easy explanation generated once for one document window."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    document_version_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    lecture_text: str
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    generation_model_id: str
    prompt_version: str = EASY_LECTURE_PROMPT_VERSION

    @field_validator(
        "document_id",
        "document_version_id",
        "lecture_text",
        "generation_model_id",
        "prompt_version",
    )
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Prepared lecture text fields cannot be blank.")
        return cleaned

    @field_validator("page_end")
    @classmethod
    def validate_page_window_end(cls, value: int, info) -> int:
        page_start = info.data.get("page_start")
        if page_start is not None:
            LecturePageWindow(
                page_start=page_start,
                page_end=value,
            )
        return value

    @field_validator("page_start")
    @classmethod
    def validate_page_window_start(cls, value: int) -> int:
        LecturePageWindow(page_start=value, page_end=value)
        return value

    @field_validator("source_chunk_ids")
    @classmethod
    def normalize_chunk_ids(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(cleaned for value in values if (cleaned := value.strip()))
        )

    @field_validator("source_pages")
    @classmethod
    def normalize_source_pages(cls, values: list[int]) -> list[int]:
        if any(page < 1 for page in values):
            raise ValueError("Source page numbers must be positive.")
        return sorted(set(values))

    @property
    def window(self) -> LecturePageWindow:
        return LecturePageWindow(
            page_start=self.page_start,
            page_end=self.page_end,
        )


class PreparedLectureReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    document_version_id: str
    expected_windows: list[LecturePageWindow] = Field(default_factory=list)
    prepared_windows: list[LecturePageWindow] = Field(default_factory=list)
    missing_windows: list[LecturePageWindow] = Field(default_factory=list)
    is_ready: bool


class PreparedLectureGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    document_version_id: str
    generated_windows: list[LecturePageWindow] = Field(default_factory=list)
    skipped_windows: list[LecturePageWindow] = Field(default_factory=list)
    readiness: PreparedLectureReadiness


class WorkflowAction(str, Enum):
    """Semantic workflow outcomes; these are not student phrase matches."""

    RESUME_TEACHING = "resume_teaching"
    RESPOND_AND_WAIT = "respond_and_wait"
    PAUSE_SESSION = "pause_session"
    CLARIFY_INTENT = "clarify_intent"


class LearningStatus(str, Enum):
    AWAITING_STUDENT = "awaiting_student"
    QUESTION_LOOP = "question_loop"
    PAUSED = "paused"
    COMPLETED = "completed"
    DOCUMENT_CHANGED = "document_changed"
    ERROR = "error"


class LearningAction(str, Enum):
    NEW_EXPLANATION = "new_explanation"
    RESPONSE = "response"
    CLARIFICATION = "clarification"
    RESUME_CURRENT_EXPLANATION = "resume_current_explanation"
    SESSION_PAUSED = "session_paused"
    COURSE_COMPLETED = "course_completed"
    DOCUMENT_CHANGED = "document_changed"
    ERROR = "error"


class StudentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_action: WorkflowAction
    student_goal: str = ""
    retrieval_query: str = ""
    use_course_retrieval: bool = True

    @field_validator("student_goal", "retrieval_query")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class LearningChunk(BaseModel):
    chunk_id: str
    chunk_index: int
    document_id: str
    document_version_id: str
    text: str
    page_numbers: list[int] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)


class LearningDocument(BaseModel):
    document_id: str
    document_version_id: str
    document_title: str
    course_id: str = ""
    lesson_id: str = ""
    lesson_title: str = ""
    total_pages: int


class TeacherTurn(BaseModel):
    text: str
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A teacher turn cannot be blank.")
        return cleaned


class LectureReview(BaseModel):
    """Structured factual and teaching-quality feedback for one draft."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    summary: str
    contradictions: list[str]
    unsupported_claims: list[str]
    missing_source_points: list[str]
    citation_issues: list[str]
    clarity_issues: list[str]
    teaching_quality_issues: list[str]
    revision_instructions: list[str]

    @field_validator("summary")
    @classmethod
    def require_summary(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A lecture review summary cannot be blank.")
        return cleaned

    @field_validator(
        "contradictions",
        "unsupported_claims",
        "missing_source_points",
        "citation_issues",
        "clarity_issues",
        "teaching_quality_issues",
        "revision_instructions",
    )
    @classmethod
    def normalize_feedback(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(cleaned for value in values if (cleaned := value.strip()))
        )

    @model_validator(mode="after")
    def require_consistent_approval(self) -> LectureReview:
        diagnostic_groups = (
            self.contradictions,
            self.unsupported_claims,
            self.missing_source_points,
            self.citation_issues,
            self.clarity_issues,
            self.teaching_quality_issues,
        )
        has_diagnostics = any(diagnostic_groups)
        has_instructions = bool(self.revision_instructions)
        if self.approved and (has_diagnostics or has_instructions):
            raise ValueError(
                "An approved lecture review cannot contain revision issues."
            )
        if not self.approved and not has_diagnostics:
            raise ValueError(
                "A rejected lecture review must diagnose at least one issue."
            )
        if not self.approved and not has_instructions:
            raise ValueError(
                "A rejected lecture review must give revision instructions."
            )
        return self


class LearningMessage(BaseModel):
    message_id: str
    role: Literal["student", "teacher"]
    kind: Literal[
        "explanation",
        "student",
        "response",
        "clarification",
        "status",
    ]
    text: str
    page_start: int | None = None
    page_end: int | None = None
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)


class LearningSnapshot(BaseModel):
    schema_version: str = SCHEMA_VERSION
    session_id: str
    document_id: str
    document_version_id: str
    document_title: str
    total_pages: int
    page_start: int
    page_end: int
    status: LearningStatus
    last_action: LearningAction
    messages: list[LearningMessage] = Field(default_factory=list)
    current_explanation_id: str = ""
    current_explanation: str = ""
    presentation_start_offset: int = 0
    presentation_offset: int = 0
    presentation_complete: bool = False
    can_accept_student_text: bool = False
    is_course_complete: bool = False


class ResumePayload(TypedDict):
    text: str
    event_id: str
    presentation_offset: int
    was_interrupted: bool


class LearningState(TypedDict, total=False):
    schema_version: str
    session_id: str
    document_id: str
    document_version_id: str
    document_title: str
    course_id: str
    lesson_id: str
    lesson_title: str
    total_pages: int
    page_start: int
    page_end: int
    page_size: int
    status: str
    last_action: str
    messages: list[dict[str, Any]]
    current_explanation_id: str
    current_explanation: str
    presentation_start_offset: int
    presentation_offset: int
    presentation_complete: bool
    current_chunk_ids: list[str]
    covered_chunk_ids: list[str]
    last_student_message: str
    last_student_event_id: str
    was_interrupted: bool
    intent: dict[str, Any]
    routing_error: str
    processed_event_ids: list[str]

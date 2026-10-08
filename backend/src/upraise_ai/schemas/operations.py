"""Content-free operational state, independent of learner consent and job progress."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OperationalState = Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]


class DependencyStatus(BaseModel):
    state: OperationalState
    reason_code: str
    observed_at: datetime | None = None


class ReadinessStatus(BaseModel):
    status: Literal["READY", "DEGRADED", "NOT_READY"]
    checked_at: datetime | None
    dependencies: dict[str, DependencyStatus]


class FeatureStatus(BaseModel):
    enabled: bool
    available: bool | None
    state: OperationalState
    reason_codes: list[str] = Field(default_factory=list)


class ServiceStatus(BaseModel):
    course_id: str | None
    checked_at: datetime | None
    answers_available: bool | None
    stt_available: bool | None
    tts_available: bool | None
    generation_available: bool | None
    kill_switch_active: bool
    features: dict[str, FeatureStatus]

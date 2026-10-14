"""Content-free operational state, independent of learner consent and job progress."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

OperationalState = Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
ControlFeature = Literal["answers", "stt", "tts", "generation", "adaptive_levels", "all"]
CONTROL_FEATURES = ("answers", "stt", "tts", "generation", "adaptive_levels")


class KillSwitchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["global", "course_id"]
    course_id: str | None = Field(None, min_length=1, max_length=120)
    feature: ControlFeature
    enabled: StrictBool = Field(description="True activates this kill switch; false releases it.")

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope == "global" and self.course_id is not None:
            raise ValueError("global scope must omit course_id")
        if self.scope == "course_id" and (self.course_id is None or self.course_id != self.course_id.strip()):
            raise ValueError("course_id scope requires a nonempty, unpadded course_id")
        return self


class KillSwitchState(BaseModel):
    scope: Literal["global", "course_id"]
    course_id: str | None
    feature: ControlFeature
    enabled: bool
    kill_switch_active: bool
    features_enabled: dict[str, bool]
    updated_at: datetime
    audit_event_id: UUID
    audit_pending: bool


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

"""Bounded planning and continuity outputs; references are resolved by the host."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from upraise_ai.core.errors import IngestionError


class Concept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_index: StrictInt
    symbol: str = Field(default="", max_length=100)


class SectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    concepts: list[Concept] = Field(max_length=8)
    join_next: StrictBool


class ContinuityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consistent: StrictBool
    connected: StrictBool
    complete: StrictBool
    issues: list[str] = Field(max_length=12)

    @property
    def passed(self):
        return self.consistent and self.connected and self.complete and not self.issues


class CoverageCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    omitted_available: list[str] = Field(max_length=30)
    language_errors: list[str] = Field(max_length=10)
    warnings: list[str] = Field(max_length=10)


class ConflictResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["confirmed_conflict", "source_ambiguity_or_damage", "different_contexts", "not_found"]
    reason: str = Field(min_length=1, max_length=2000)
    evidence_indices: list[StrictInt] = Field(default_factory=list, max_length=2)
    entity: str = Field(default="", max_length=160)


class CompletionFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[str] = Field(max_length=12)
    source_warnings: list[str] = Field(max_length=12)


class CompletionResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["actionable", "source_gap_or_damage", "covered_elsewhere", "intentional_question", "unfounded"]
    reason: str = Field(min_length=1, max_length=1500)


class LanguageCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    in_language: StrictBool
    reason: str = Field(min_length=1, max_length=1500)


class FindingVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocking: StrictBool
    reason: str = Field(min_length=1, max_length=1500)


class CoverageResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["missing_available_meaning", "source_gap_or_damage", "source_ambiguity", "equivalent_meaning"]
    reason: str = Field(min_length=1, max_length=1500)


class ExcerptGroups(BaseModel):
    model_config = ConfigDict(extra="forbid")
    end_indices: list[StrictInt] = Field(min_length=1, max_length=200)


class CourseError(IngestionError):
    def __init__(self, problem):
        super().__init__("TEACHING_V3_" + problem)


def fingerprint(value):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

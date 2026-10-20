"""Strict, bounded V2 provider outputs and formatting gates."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from upraise_ai.core.errors import IngestionError
from upraise_ai.services.aligned_models import provider_schema
from upraise_ai.services.v3.polly_speech import _MATH_PATTERN


class Narration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=24000)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correct: StrictBool
    complete: StrictBool
    in_language: StrictBool
    natural: StrictBool
    issues: list[str] = Field(max_length=30)

    @property
    def passed(self):
        return self.correct and self.complete and self.in_language and self.natural and not self.issues


class MaterialWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["source_gap", "style"]
    message: str = Field(min_length=1, max_length=2000)


class MaterialDecision(Decision):
    """Written DRAFTs can retain warnings; factual checks cannot be downgraded."""

    warnings: list[MaterialWarning] = Field(default_factory=list, max_length=20)

    @property
    def passed(self):
        return self.correct and self.complete and self.in_language and not self.issues


class MaterialFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["changed_fact", "unsupported_claim", "omitted_available", "wrong_language", "invalid_citation"]
    candidate_index: StrictInt
    candidate_quote: str = Field(max_length=2000)
    required_index: StrictInt
    required_quote: str = Field(max_length=2000)
    message: str = Field(min_length=1, max_length=2000)


class GroundedMaterialReview(BaseModel):
    """Verdict flags are derived locally from grounded candidate errors."""
    model_config = ConfigDict(extra="forbid")
    findings: list[MaterialFinding] = Field(max_length=30)
    warnings: list[MaterialWarning] = Field(max_length=20)
    natural: StrictBool


class SourceSelectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_index: StrictInt
    disposition: Literal["teach", "incidental", "unreadable"]
    teaching_quotes: list[str] = Field(max_length=50)
    note: str = Field(max_length=1000)


class SourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SourceSelectionItem] = Field(min_length=1, max_length=1000)


MAX_MATERIAL_CORRECTIONS = 2


class TeachingReviewEvidenceInvalid(IngestionError):
    """Content-free stage details, protected by the job's normal output lifetime."""

    def __init__(self, stage, problem):
        super().__init__("TEACHING_V2_REVIEW_EVIDENCE_INVALID")
        self.details = {"stage": f"teaching_v2_{stage}", "problem": problem,
                        "reason": "The automated review returned invalid evidence references after bounded repair. "
                                  "No teaching text or audio was published."}


class TeachingMaterialRejected(IngestionError):
    """Bounded author-facing findings, never provider output in logs/audit."""

    def __init__(self, decision, *, section, learner_level, correction_rounds):
        super().__init__("TEACHING_V2_MATERIAL_REJECTED")
        self.review = decision
        review = decision.model_dump()
        review["issues"] = [" ".join(issue.split())[:600] for issue in decision.issues[:5]]
        if "warnings" in review:
            review["warnings"] = [{"category": item.category, "message": " ".join(item.message.split())[:600]}
                                  for item in decision.warnings[:5]]
        fallback = [message for key, message in (
            ("correct", "The teaching text changes or invents source facts or mathematical meaning."),
            ("complete", "Required teaching material is missing."),
            ("in_language", "The teaching text does not match the requested language."),
            ("natural", "The explanation needs clearer teaching or formatting.")) if not getattr(decision, key)]
        self.details = {"stage": "teaching_v2_material_review", "learner_level": learner_level,
                        "source_window": section.get("window", []),
                        "correction_rounds": correction_rounds,
                        "max_correction_rounds": MAX_MATERIAL_CORRECTIONS,
                        "reason": "; ".join(review["issues"] or fallback or ["The reviewer requested changes."])[:1200],
                        "review": review}


def structured(models, system, payload, schema, name, *, reviewer=False, max_tokens=None):
    """Reuse the existing EU model transport, with bounded JSON/schema recovery."""
    teacher = models.v2_teacher
    contract = provider_schema(schema.model_json_schema())
    for strict in (True, False):
        try:
            raw = teacher._converse_text(
                system_prompt=system,
                user_prompt=json.dumps(payload, ensure_ascii=False) + "\nReturn one JSON object.\n"
                            + json.dumps(contract),
                max_tokens=max_tokens or (8000 if not reviewer else 3000), temperature=0.0,
                model_id=models.settings.lesson_review_model_id if reviewer else models.settings.lesson_generation_model_id,
                output_config=teacher._structured_output_config(schema=contract, name=name,
                    description="Source-based teaching and natural mathematical narration.") if strict else None,
            )
            return schema.model_validate_json(raw)
        except Exception:
            if not strict:
                raise IngestionError("TEACHING_V2_MODEL_OUTPUT_INVALID", retryable=True) from None


def validate_written(text):
    # Match literal escapes, never valid commands such as \nu or \underbrace.
    # Literal syntax is legitimate inside explicitly marked programming examples.
    notation = re.sub(r"```[\s\S]*?```|`[^`\n]+`", " ", text)
    prose = _MATH_PATTERN.sub(" ", notation)
    problem = next((code for code, invalid in (
        ("EMPTY_TEXT", not text.strip()),
        ("LITERAL_NEWLINE_ESCAPE", r"\n" in prose or re.search(r"\\n(?![A-Za-z])", notation)),
        ("LITERAL_UNICODE_ESCAPE", re.search(r"\\u[0-9a-fA-F]{4}", notation)),
        ("DOUBLE_ESCAPED_MATH_DELIMITER", re.search(r"\\\\[()[\]]", notation)),
        ("CONTROL_CHARACTER", any(ord(char) < 32 and char not in "\n\r\t" for char in text)),
    ) if invalid), None)
    if problem:
        error = IngestionError("TEACHING_V2_TEXT_INVALID", retryable=True)
        error.details = {"stage": "teaching_v2_text_format", "problem": problem}
        raise error


def validate_spoken(text):
    validate_written(text)
    if (re.search(r"\\[A-Za-z]+|\\[()[\]]|<[^>]+>|\$", text)
            or re.search(r"\b(?:open paren|close paren|open bracket|close bracket|backslash n|double struck)\b",
                         text, re.IGNORECASE)):
        raise IngestionError("TEACHING_V2_NARRATION_INVALID", retryable=True)

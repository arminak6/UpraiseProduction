"""Checkpoint the judge before final review; unresolved checks never trigger edits."""

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import ReviewReport, SectionDraft
from upraise_ai.services.aligned_models import validate_ids
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.teaching_v2.contracts import Decision, structured, validate_spoken, validate_written
from upraise_ai.services.teaching_v2.first_draft import retention_error, storable_text
from upraise_ai.services.teaching_v2.review_recovery import FLAG_SUMMARY, gap_text, review_flag

from .prompts import JUDGE_POLICY, POLICY, REVIEW_POLICY, VERSION


class Assessment(Decision):
    candidate_index: StrictInt
    level_suitable: StrictBool


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessments: list[Assessment] = Field(min_length=1, max_length=200)


def items(candidate):
    if isinstance(candidate, SectionDraft):
        return candidate.paragraphs
    return candidate.units if hasattr(candidate, "units") else [candidate]


def checkpoint(models, stage, payload):
    key = hashlib.sha256(json.dumps([VERSION, POLICY, stage, payload, JUDGE_POLICY, REVIEW_POLICY,
        models.settings.lesson_generation_model_id, models.settings.lesson_review_model_id],
        sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return models.diagnostic_dir.parent / "teaching-v29" / (stage + "-" + key + ".json") if models.diagnostic_dir else None


def first_candidate(models, stage, payload, schema, generate):
    path = checkpoint(models, "writer-" + stage, payload)
    if path and path.is_file():
        return schema.model_validate(json.loads(path.read_text(encoding="utf-8"))["candidate"])
    candidate = generate()
    if path:
        write_json(path, {"candidate": candidate.model_dump(mode="json")})
    return candidate


def prepare(candidate, context, language, *, units=None, max_words=None):
    """Keep usable prose; bound metadata and record local format findings."""
    candidate = candidate.model_copy(deep=True)
    if units is not None:
        validate_ids(units, candidate.units)
    reports = [[] for _ in items(candidate)]
    for index, item in enumerate(items(candidate)):
        pages = units[index].source.citation_numbers if units is not None else context.get("citation_numbers", [])
        if hasattr(item, "pages"):
            valid = [page for page in item.pages if page in pages]
            if valid != item.pages:
                reports[index].append(review_flag("judge source references", IngestionError("LESSON_CITATIONS_INVALID"),
                                                 pages=pages, category="citations"))
            item.pages = valid or list(pages)
        safe = storable_text(item.text)
        if safe != item.text or not safe.strip():
            reports[index].append(review_flag("judge text encoding", IngestionError("TEACHING_V2_TEXT_INVALID"),
                                             pages=pages, category="clarity"))
        item.text = safe if safe.strip() else gap_text(language)
        try:
            (validate_spoken if context.get("kind") == "spoken" else validate_written)(item.text)
        except IngestionError as error:
            reports[index].append(review_flag("judge text formatting", error, pages=pages, category="clarity"))
        for match in re.finditer(r"\[(?:p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]", item.text):
            start, end = int(match[1]), int(match[2] or match[1])
            if end < start or end - start > 5000 or not set(range(start, end + 1)).issubset(pages):
                reports[index].append(review_flag("judge source references", IngestionError("LESSON_CITATIONS_INVALID"),
                                                 pages=pages, category="citations"))
        if (max_words and len(item.text.split()) > max_words) or len(item.text) > 16000:
            reports[index].append(review_flag("judge length", IngestionError("LESSON_UNIT_TOO_LARGE"),
                                             pages=pages, category="clarity"))
    return candidate, reports


def review(models, candidate, context, language, *, stage, local_issues):
    # A fresh, stateless request: no initial findings, verdict or judge rationale.
    payload = {**context, "language": language, "candidates": [item.text for item in items(candidate)]}
    count = len(payload["candidates"])
    try:
        verdict = structured(models, REVIEW_POLICY, payload, Review, "teaching_v29_" + stage + "_review",
                             reviewer=True, max_tokens=6000)
        if [item.candidate_index for item in verdict.assessments] != list(range(count)):
            raise IngestionError("TEACHING_V2_REVIEW_UNAVAILABLE")
        reports = []
        for index, assessment in enumerate(verdict.assessments):
            findings = list(local_issues[index])
            if not assessment.passed or not assessment.level_suitable:
                reasons = assessment.issues or [name.replace("_", " ") + " check failed." for name in
                    ("correct", "complete", "in_language", "natural", "level_suitable") if not getattr(assessment, name)]
                findings.append(review_flag("judge final review" if stage == "final" else "writer review",
                    IngestionError("TEACHING_V2_NARRATION_REJECTED" if context.get("kind") == "spoken"
                                   else "TEACHING_V2_MATERIAL_REJECTED"),
                    pages=context.get("citation_numbers", []),
                    category="speech" if context.get("kind") == "spoken" else "accuracy", issues=reasons))
            reports.append(ReviewReport(approved=not findings,
                summary=FLAG_SUMMARY if findings else "V2.9 automated final checks passed; human approval remains required.",
                issues=findings))
        return reports
    except Exception as error:
        failure = retention_error(error)
        if failure is None:
            raise
        return [ReviewReport(approved=False, summary=FLAG_SUMMARY, issues=[*local_issues[index],
            review_flag("judge final review" if stage == "final" else "writer review", failure,
                        pages=context.get("citation_numbers", []),
                        category="speech" if context.get("kind") == "spoken" else "accuracy")]) for index in range(count)]


def adjudicate(models, candidate, context, language, *, stage, units=None, max_words=None):
    """A -> B -> C -> B exactly once, preserving C on failed final checks."""
    path = checkpoint(models, stage, [context, candidate.model_dump(mode="json"), language, max_words])
    saved = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}
    schema = type(candidate)
    if saved.get("final_candidate") is not None and saved.get("reports") is not None:
        final = schema.model_validate(saved["final_candidate"])
        if units is not None:
            validate_ids(units, final.units)
        return final, [ReviewReport.model_validate(item) for item in saved["reports"]], saved["judge_generated"]

    def persist():
        if path:
            write_json(path, saved)

    if "judge_candidate" not in saved:
        original, local = prepare(candidate, context, language, units=units, max_words=max_words)
        if "initial_reports" not in saved:
            initial = review(models, original, context, language, stage="initial", local_issues=local)
            saved["initial_reports"] = [report.model_dump(mode="json") for report in initial]
            persist()
        payload = {**context, "language": language, "original_candidate": candidate.model_dump(mode="json"),
                   "reviewer_comments": saved["initial_reports"], "max_words_per_unit": max_words}
        try:
            final = structured(models, JUDGE_POLICY, payload, schema, "teaching_v29_judge", max_tokens=10000)
            if units is not None:
                validate_ids(units, final.units)
            if context.get("preserve_paragraph_count") and len(items(final)) != len(items(candidate)):
                raise IngestionError("LESSON_TRANSLATION_INCOMPLETE")
            saved["judge_generated"] = True
        except Exception as error:
            failure = retention_error(error)
            if failure is None:
                raise
            final = original
            saved["judge_generated"] = False
            saved["judge_failure"] = review_flag("judge unavailable; writer text retained", failure,
                pages=context.get("citation_numbers", []), category="source").model_dump(mode="json")
        # Persist C before B's final review. An interrupted review cannot rerun C.
        saved["judge_candidate"] = final.model_dump(mode="json")
        persist()
    final = schema.model_validate(saved["judge_candidate"])
    final, local = prepare(final, context, language, units=units, max_words=max_words)
    if "judge_failure" in saved:
        from upraise_ai.schemas.lessons import ReviewIssue
        for findings in local:
            findings.append(ReviewIssue.model_validate(saved["judge_failure"]))
    reports = review(models, final, context, language, stage="final", local_issues=local)
    saved.update(final_candidate=final.model_dump(mode="json"), reports=[r.model_dump(mode="json") for r in reports])
    persist()
    return final, reports, saved["judge_generated"]

"""Retain V2's first written candidate; failed checks remain teacher findings."""

import hashlib
import json
import re

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.schemas.lessons import SectionDraft
from upraise_ai.services.aligned_models import validate_ids

from .contracts import validate_written
from .review_recovery import review_failure, review_flag

POLICY = "generator-text-with-review-tags-v2"


def retention_error(error):
    """Normalize model/check failures without hiding storage or ownership loss."""
    if isinstance(error, (LeaseLost, OSError, psycopg.Error, MemoryError)):
        return None
    if isinstance(error, IngestionError):
        if (review_failure(error) or error.code.startswith("LESSON_ALIGNMENT_") or error.code in {
                "TEACHING_V2_TEXT_INVALID", "TEACHING_V2_NARRATION_INVALID", "TEACHING_V2_REVIEW_UNAVAILABLE",
                "TEACHING_V2_SOURCE_SELECTION_INVALID", "LESSON_CITATIONS_INVALID", "LESSON_UNIT_TOO_LARGE",
                "LESSON_TRANSLATION_INVALID", "LESSON_TRANSLATION_INCOMPLETE", "LESSON_TRANSLATION_REJECTED"}):
            return error
        return None
    normalized = IngestionError("TEACHING_V2_REVIEW_UNAVAILABLE")
    normalized.details = {"problem": "PROVIDER_ERROR" if isinstance(error, (BotoCoreError, ClientError)) else "RESPONSE_ERROR"}
    return normalized


def first_candidate(models, stage, payload, schema, generate):
    """Persist each first usable generator output before any reviewer sees it."""
    if not models.preserve_first_draft or models.diagnostic_dir is None:
        return generate()
    key = hashlib.sha256(json.dumps([POLICY, stage, payload,
        models.settings.lesson_generation_model_id, models.settings.lesson_review_model_id],
        sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    path = models.diagnostic_dir.parent / "teaching-v2-first-drafts" / f"{stage}-{key}.json"
    if path.is_file():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            return schema.model_validate(saved["candidate"])
    candidate = generate()
    from upraise_ai.services.lesson_pipeline import atomic_write
    atomic_write(path, json.dumps({"key": key, "candidate": candidate.model_dump(mode="json")}, ensure_ascii=True).encode())
    return candidate


def storable_text(text):
    # Literal escapes/notation remain visible for editing. Only characters that
    # cannot safely travel through UTF-8/PostgreSQL/XML are replaced with spaces.
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", " ", text)


def format_error(problem):
    error = IngestionError("TEACHING_V2_TEXT_INVALID")
    error.details = {"problem": problem}
    return error


def retained_material(models, section, candidate, language, learner_level, *, units=None, max_words=None):
    retained = candidate.model_copy(deep=True)
    warnings = []
    if isinstance(retained, SectionDraft):
        items = retained.paragraphs
        bounds = [section["pages"]] * len(items)
        for item in items:
            valid = [p for p in item.pages if p in section["pages"]]
            if valid != item.pages:
                item.pages = valid or list(section["pages"])
                warnings.append(review_flag("source references", IngestionError("LESSON_CITATIONS_INVALID"),
                    pages=section["pages"], category="citations"))
    else:
        validate_ids(units, retained.units)
        items = retained.units
        bounds = [u.source.citation_numbers for u in units]
    for item, pages in zip(items, bounds, strict=True):
        try:
            validate_written(item.text)
        except IngestionError as error:
            warnings.append(review_flag("first-draft text formatting", error, pages=pages, category="clarity"))
        safe = storable_text(item.text)
        if safe != item.text:
            warnings.append(review_flag("first-draft text encoding", format_error("UNSTORABLE_CHARACTER"),
                                        pages=pages, category="clarity"))
        if not safe.strip():
            # There is no usable first text to retain; the caller supplies an
            # explicit review notice rather than publishing empty content.
            from .review_recovery import gap_text
            safe = gap_text(language)
        def citation(match):
            start, end = int(match[1]), int(match[2] or match[1])
            if end < start or end - start > 5000 or not set(range(start, end + 1)).issubset(pages):
                warnings.append(review_flag("first-draft source references", IngestionError("LESSON_CITATIONS_INVALID"),
                                            pages=pages, category="citations"))
                return match[0]
            return match[0]
        item.text = re.sub(r"\[(?:p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]", citation, safe)
        if len(item.text) > 16000:
            warnings.append(review_flag("first-draft length; original text split into display parts",
                IngestionError("TEACHING_V2_TEXT_TOO_LONG"), pages=pages, category="clarity"))
        if units is not None and max_words and len(item.text.split()) > max_words:
            warnings.append(review_flag("first-draft length", IngestionError("LESSON_UNIT_TOO_LARGE"),
                                        pages=pages, category="clarity"))
    if any("TEACHING_V2_TEXT_INVALID" in w.message for w in warnings):
        return retained, 0, warnings
    try:
        warnings.extend(models.check_written(section, [i.text for i in items], language, learner_level,
            [u.material_text for u in units] if units is not None else None))
    except Exception as error:
        failure = retention_error(error)
        if failure is None:
            raise
        warnings.append(review_flag("first-draft material review", failure, pages=section["pages"],
                                    category="accuracy"))
    return retained, 0, warnings

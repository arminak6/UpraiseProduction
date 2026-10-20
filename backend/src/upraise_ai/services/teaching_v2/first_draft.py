"""Retain V2's first written candidate; failed checks remain teacher findings."""

import re

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import SectionDraft
from upraise_ai.services.aligned_models import validate_ids

from .contracts import validate_written
from .review_recovery import review_failure, review_flag

POLICY = "keep-first-draft-for-human-review-v1"


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
    for index, (item, pages) in enumerate(zip(items, bounds, strict=True)):
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
                return ""
            return match[0]
        item.text = re.sub(r"\[(?:p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]", citation, safe)
        if units is not None and max_words and len(item.text.split()) > max_words:
            warnings.append(review_flag("first-draft length", IngestionError("LESSON_UNIT_TOO_LARGE"),
                                        pages=pages, category="clarity"))
    if any("TEACHING_V2_TEXT_INVALID" in w.message for w in warnings):
        return retained, 0, warnings
    try:
        warnings.extend(models.check_written(section, [i.text for i in items], language, learner_level,
            [u.material_text for u in units] if units is not None else None))
    except IngestionError as error:
        if not review_failure(error) and error.code != "TEACHING_V2_TEXT_INVALID":
            raise
        warnings.append(review_flag("first-draft material review", error, pages=section["pages"],
                                    category="accuracy"))
    return retained, 0, warnings

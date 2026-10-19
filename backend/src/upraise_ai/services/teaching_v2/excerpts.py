"""Deterministic, teacher-flagged recovery; rejected prose is never retained."""

import re

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import DraftParagraph, ReviewIssue, SectionDraft
from upraise_ai.services.aligned_models import UnitSizeError
from upraise_ai.services.lesson_translation import needs_translation

from .contracts import validate_written

EXCERPT_WARNING = (
    "Teacher editing required: after two unsuccessful factual corrections, this section uses exact "
    "source excerpts instead of the rejected explanation. Extraction errors and unexplained notation "
    "may remain. Review the source and edit this DRAFT before teaching."
)
EXCERPT_SUMMARY = "Source-excerpt DRAFT; teacher editing is required."
REVIEWED_TEXT_WARNING = (
    "Teacher editing required: after two unsuccessful factual corrections, this part keeps its "
    "previously reviewed teaching text. The attempted difficulty adaptation was discarded. "
    "Review this DRAFT for the requested level before teaching."
)
RECOVERY_WARNINGS = {EXCERPT_WARNING, REVIEWED_TEXT_WARNING}


def validate_excerpt(text, pages):
    validate_written(text)
    allowed = set(pages)
    if not allowed or any(isinstance(page, bool) or page < 1 for page in allowed):
        raise IngestionError("LESSON_CITATIONS_INVALID")
    for match in re.finditer(r"\[(?:p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]", text):
        start, end = int(match[1]), int(match[2] or match[1])
        if end < start or end - start > 5000 or not set(range(start, end + 1)).issubset(allowed):
            raise IngestionError("LESSON_CITATIONS_INVALID")


def source_excerpt(section, language, *, units=None, max_words=None):
    """Return None when a faithful, language-correct source copy is unavailable."""
    if units is None:
        message = EXCERPT_WARNING
        source_language = section.get("source_language")
        chunks = section["chunks"]
        if not source_language or needs_translation(source_language, language) or not 1 <= len(chunks) <= 200:
            return None
        paragraphs = []
        for chunk in chunks:
            pages = chunk.get("page_numbers", [])
            if not set(pages).issubset(section["pages"]) or len(chunk["text"]) > 30000:
                return None
            validate_excerpt(chunk["text"], pages)
            paragraphs.append(DraftParagraph(text=chunk["text"], pages=pages))
        result = SectionDraft(paragraphs=paragraphs)
        pages = sorted({page for part in paragraphs for page in part.pages})
    else:
        message = REVIEWED_TEXT_WARNING
        if any(unit.canonical_review is None or needs_translation(unit.source_language, language) for unit in units):
            return None
        for unit in units:
            validate_excerpt(unit.material_text, unit.source.citation_numbers)
            if len(unit.material_text) > 16000 or (max_words and len(unit.material_text.split()) > max_words):
                # Let the common-plan refinement split the source; never truncate it.
                raise UnitSizeError(unit.content_unit_id)
        result = AlignedTexts(units=[AlignedText(content_unit_id=unit.content_unit_id, text=unit.material_text)
                                    for unit in units])
        pages = sorted({page for unit in units for page in unit.source.citation_numbers})
    return result, [ReviewIssue(category="source", message=message, pages=pages)]

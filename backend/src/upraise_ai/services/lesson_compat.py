"""A3 output conversion around the unchanged V3 teacher contracts."""
import re

from upraise_ai.schemas.lessons import ReviewIssue, ReviewReport, SectionDraft
from upraise_ai.services.v3.models import LectureReview
from upraise_ai.services.v3.polly_speech import _MATH_PATTERN


def turn_to_draft(turn):
    """V3 attaches whole-window provenance; preserve it on A3 paragraphs."""
    text = turn.text.replace("\r\n", "\n")
    math = list(_MATH_PATTERN.finditer(text))
    paragraphs, start = [], 0
    for separator in re.finditer(r"\n[ \t]*\n", text):
        if any(span.start() <= separator.start() < span.end() for span in math):
            continue
        if text[start:separator.start()].strip():
            paragraphs.append(text[start:separator.start()].strip())
        start = separator.end()
    if text[start:].strip():
        paragraphs.append(text[start:].strip())
    return SectionDraft(paragraphs=[{"text": text, "pages": turn.source_pages} for text in paragraphs])


def legacy_review_report(review: LectureReview, pages):
    groups = {"contradictions": "accuracy", "unsupported_claims": "accuracy",
              "missing_source_points": "coverage", "citation_issues": "citations",
              "clarity_issues": "clarity", "teaching_quality_issues": "clarity"}
    issues = [ReviewIssue(category=category, message=message, pages=sorted(set(pages)))
              for field, category in groups.items() for message in getattr(review, field)]
    return ReviewReport(approved=review.approved, summary=review.summary, issues=issues,
                        revision_instructions=review.revision_instructions)

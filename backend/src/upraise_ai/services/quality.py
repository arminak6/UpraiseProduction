"""Conservative quality aggregation shared by extraction, chunks and publication."""

from collections.abc import Iterable

from upraise_ai.schemas.documents import ContentQuality, DocumentElement, QualityStatus


def aggregate_quality(elements: Iterable[DocumentElement]) -> ContentQuality:
    items = list(elements)
    if not items:
        return ContentQuality()
    statuses = {item.quality.status for item in items}
    if QualityStatus.NEEDS_REVIEW in statuses:
        status = QualityStatus.NEEDS_REVIEW
    elif QualityStatus.UNVERIFIED in statuses:
        status = QualityStatus.UNVERIFIED
    else:
        status = QualityStatus.AUTO_CHECKED
    model_ids = {item.quality.verification_model_id for item in items}
    prompt_versions = {item.quality.verification_prompt_version for item in items}
    return ContentQuality(
        status=status,
        checks=list(dict.fromkeys(check for item in items for check in item.quality.checks)),
        issues=[f"{item.element_id}: {issue}" for item in items for issue in item.quality.issues],
        evidence_complete=all(item.quality.evidence_complete for item in items),
        verification_model_id=next(iter(model_ids)) if len(model_ids) == 1 else None,
        verification_prompt_version=next(iter(prompt_versions)) if len(prompt_versions) == 1 else None,
    )


def quality_summary(elements: Iterable[DocumentElement]) -> dict[str, int]:
    """Count element states; AUTO_CHECKED is a recorded check, never human approval."""
    summary = {"total": 0, "auto_checked": 0, "unverified": 0, "needs_review": 0}
    for element in elements:
        summary["total"] += 1
        summary[element.quality.status.value.lower()] += 1
    return summary

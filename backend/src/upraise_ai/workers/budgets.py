"""Workload-based execution budgets, independent of progress frequency."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from upraise_ai.core.config import Settings
from upraise_ai.schemas.documents import DocumentElement, ElementType

BudgetPhase = Literal["startup", "init", "parse", "enrich", "chunk", "embed", "store"]


def phase_timeout_seconds(settings: Settings, phase: BudgetPhase, units: int) -> float:
    """Return a phase allowance bounded by the absolute attempt cap.

    The supervisor must measure this from phase entry, without restarting it
    when progress updates arrive, and separately enforce the total job cap.
    """
    if isinstance(units, bool) or not isinstance(units, int) or units < 0:
        raise ValueError("Workload units must be a nonnegative integer")
    if phase == "startup":
        base, per_unit = settings.worker_startup_timeout_seconds, 0.0
    elif phase == "init":
        base, per_unit = settings.worker_phase_timeout_seconds, 0.0
    elif phase == "parse":
        base = settings.parse_timeout_base_seconds
        per_unit = (
            settings.parse_timeout_per_ocr_page_seconds
            if settings.enable_ocr
            else settings.parse_timeout_per_page_seconds
        )
    elif phase == "enrich":
        base = settings.enrich_timeout_base_seconds
        per_unit = settings.enrich_timeout_per_region_seconds
    elif phase == "embed":
        base = settings.embed_timeout_base_seconds
        per_unit = settings.embed_timeout_per_chunk_seconds
    elif phase == "chunk":
        base = settings.worker_phase_timeout_seconds
        per_unit = settings.parse_timeout_per_page_seconds
    elif phase == "store":
        base, per_unit = settings.worker_phase_timeout_seconds, 5.0
    else:
        raise ValueError(f"Unsupported budget phase: {phase}")
    return min(settings.job_timeout_seconds, max(1.0, base + (per_unit * units)))


def enrichment_workload(elements: Iterable[DocumentElement], *, describe_images: bool) -> int:
    """Count source crops eligible for the current enrichment pipeline."""
    count = 0
    for element in elements:
        if not element.asset_path:
            continue
        if element.element_type is ElementType.FORMULA and not element.original_text.strip():
            count += 1
        elif element.metadata.get("inline_math_candidate") and element.original_text.strip():
            count += 1
        elif describe_images and element.element_type is ElementType.FIGURE:
            count += 1
    return count


def notify_workload(progress, phase: BudgetPhase, units: int) -> None:
    """Use the optional supervisor hook while retaining legacy callbacks."""
    callback = getattr(progress, "set_workload", None)
    if callable(callback):
        callback(phase, units)

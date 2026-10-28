"""Keep immutable evidence; bind model-selected IDs to host-owned references."""

import re

from upraise_ai.core.errors import IngestionError
from upraise_ai.services.teaching_v2.contracts import validate_written
from upraise_ai.services.v3.polly_speech import _MATH_PATTERN

TAG = re.compile(r"\[(?:p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]")
EXTRACTION_LABEL = re.compile(r"(?m)^\s*\[(?:Word blocks?|Slides?) \d+(?:-\d+)?\]\s*\n?")


def records(section):
    return [{"chunk_id": str(c["chunk_id"]), "text": c["text"],
             "document_id": str(section["document_id"]),
             "document_version_id": str(section["document_version_id"]),
             "heading_path": c.get("heading_path", []), "source_numbers": c.get("page_numbers", []),
             "quality_status": c.get("quality", {}).get("status", "UNVERIFIED"),
             "element_types": c.get("element_types", [])}
            for c in section["chunks"]]


def restore_selection(original, selected):
    originals = {str(c["chunk_id"]): c for c in original["chunks"]}
    chunks = []
    for chunk in selected["chunks"]:
        # The selector's honest no-content sentinel must not resurrect an image.
        if chunk["text"].startswith("[No readable substantive teaching content"):
            continue
        full = originals[str(chunk["chunk_id"])]
        chunks.append({**full, "selected_quotes": chunk["text"]})
    return {**selected, "chunks": chunks,
            "pages": sorted({n for c in chunks for n in c.get("page_numbers", [])})}


def without_citations(text):
    return TAG.sub("", text).strip()


def presentation_text(text):
    """Remove host extraction labels only; keep source prose/math immutable."""
    return EXTRACTION_LABEL.sub("", text).strip()


def substantive(record):
    """Subject headings remain context; a title alone is not required prose."""
    if _MATH_PATTERN.search(record["text"]) or any(
            kind.upper() in {"TABLE", "FORMULA", "EQUATION"} for kind in record.get("element_types", [])):
        return True
    headings = record.get("heading_path", [])
    if isinstance(headings, str):
        headings = [headings]
    labels = {" ".join(label.split()).casefold() for label in headings}
    lines = [" ".join(line.split()).casefold() for line in presentation_text(record["text"]).splitlines()
             if line.strip()]
    return bool(lines) and not all(line in labels for line in lines)


class MaterialFindings(dict):
    """Internal repair evidence stays structured instead of entering UI strings."""

    def __init__(self):
        super().__init__()
        self.evidence = {}

    def add(self, index, message, evidence=()):
        self.setdefault(index, []).append(message)
        existing = self.evidence.setdefault(index, {})
        existing.update({record["chunk_id"]: record for record in evidence})


def reference_label(record):
    numbers = ", ".join(str(n) for n in record["source_numbers"])
    return f"Source reference {numbers}" if numbers else "Required source passage"


def cited_records(section, text):
    numbers = set()
    for match in TAG.finditer(text):
        start, end = int(match[1]), int(match[2] or match[1])
        if end < start or end - start > 5000:
            raise IngestionError("LESSON_CITATIONS_INVALID")
        numbers.update(range(start, end + 1))
    actual = records(section)
    available = {n for record in actual for n in record["source_numbers"]}
    if not numbers or not numbers.issubset(available):
        raise IngestionError("LESSON_CITATIONS_INVALID")
    return [r for r in actual if numbers.intersection(r["source_numbers"])]


def bind(section, item, *, allowed=None):
    by_id = {r["chunk_id"]: r for r in records(section)}
    ids = list(dict.fromkeys(item.evidence_ids))
    if not ids or any(identity not in by_id for identity in ids):
        raise IngestionError("TEACHING_V31_EVIDENCE_INVALID")
    chosen = [by_id[identity] for identity in ids]
    numbers = sorted({n for r in chosen for n in r["source_numbers"]})
    if not numbers or (allowed is not None and not set(numbers).issubset(allowed)):
        raise IngestionError("LESSON_CITATIONS_INVALID")
    # Do not silently convert model-invented numeric references into valid ones.
    prose = _MATH_PATTERN.sub(" ", item.text)
    if TAG.search(prose) or re.search(r"\[(?:Word blocks|CSV rows|Slides?)\b|\[\d+\]", prose):
        raise IngestionError("TEACHING_V31_EVIDENCE_INVALID")
    validate_written(item.text)
    text = item.text.strip() + " " + " ".join(f"[p. {n}]" for n in numbers)
    return text, numbers, chosen

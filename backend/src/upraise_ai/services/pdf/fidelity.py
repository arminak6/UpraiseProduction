"""Conservative structural checks; these never establish semantic correctness."""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

from upraise_ai.core.utils import resolve_project_path
from upraise_ai.schemas.documents import DocumentElement, SourceRegion

from .inline_math import looks_like_inline_math


def source_evidence(element: DocumentElement, root: Path) -> tuple[list[Path], list[str]]:
    """Require every recorded region, including multiple regions on one page."""
    regions = element.source_regions
    issues = []
    paths = []
    if not regions:
        return [], ["No complete provenance-backed source crops are available."]
    provenance = element.metadata.get("provenance", [])
    if provenance and (
        len(provenance) != len(regions)
        or any(p.get("page") != r.page_number for p, r in zip(provenance, regions))
    ):
        issues.append("Source crops do not cover every recorded provenance entry.")
    if provenance and len(provenance) == len(regions):
        for recorded, region in zip(provenance, regions):
            bbox = region.bounding_box
            expected_bbox = {
                "l": bbox.left, "t": bbox.top, "r": bbox.right, "b": bbox.bottom,
                "coord_origin": bbox.coordinate_origin,
            } if bbox else None
            if recorded.get("charspan", []) != region.charspan or recorded.get("bbox") != expected_bbox:
                issues.append("Source crop character spans/bounding boxes disagree with recorded provenance.")
                break
    expected_pages = set(element.page_numbers)
    if element.page_number is not None:
        expected_pages.add(element.page_number)
    if expected_pages and expected_pages != {r.page_number for r in regions}:
        issues.append("Source crop pages do not match the element's source pages.")
    for position, region in enumerate(regions):
        if region.page_number < 1 or region.bounding_box is None:
            issues.append(f"Source region {position + 1} has no usable page/bounding box.")
        if not region.asset_path:
            issues.append(f"Source region {position + 1} has no crop.")
            continue
        path = resolve_project_path(region.asset_path, root)
        if not path.is_file():
            issues.append(f"Source region {position + 1} crop is unavailable.")
        else:
            resolved = path.resolve()
            if resolved in paths:
                issues.append("Distinct source regions share one crop path; coverage is ambiguous.")
            paths.append(resolved)
    return paths, issues


def inline_fragments(
    element: DocumentElement,
) -> tuple[list[tuple[SourceRegion, int, int]], list[str]]:
    """Map crops to immutable text spans, rejecting gaps and ambiguous overlap."""
    source = element.source_text if element.source_text is not None else element.original_text
    fragments = []
    for region in element.source_regions:
        span = region.charspan
        if (
            len(span) != 2
            or any(isinstance(n, bool) or not isinstance(n, int) for n in span)
            or not 0 <= span[0] < span[1] <= len(source)
        ):
            return [], ["Inline source crop has no valid character-span mapping."]
        fragments.append((region, span[0], span[1]))
    fragments.sort(key=lambda entry: entry[1])
    cursor = 0
    for _, start, end in fragments:
        if start < cursor:
            return [], ["Inline source character spans overlap; replacement would be ambiguous."]
        if source[cursor:start].strip():
            return [], ["Inline source character spans leave text without crop evidence."]
        cursor = end
    if source[cursor:].strip() or not fragments:
        return [], ["Inline source crops do not cover all source text."]
    return fragments, []


def latex_structure_issues(text: str) -> list[str]:
    depth = 0
    for brace in re.finditer(r"(?<!\\)[{}]", text):
        depth += 1 if brace.group() == "{" else -1
        if depth < 0:
            return ["LaTeX braces are unbalanced."]
    return ["LaTeX braces are unbalanced."] if depth else []


def merge_inline_math(source: str, candidate: str) -> tuple[str | None, list[str]]:
    """Replace only source-aligned math spans; retain surrounding prose verbatim."""
    spans = list(re.finditer(r"\\\((.*?)\\\)", candidate, flags=re.DOTALL))
    if not spans or candidate.count(r"\(") != len(spans) or candidate.count(r"\)") != len(spans):
        return None, ["Inline LaTeX delimiters are missing or unbalanced."]
    issues = latex_structure_issues(candidate)
    if issues:
        return None, issues
    pattern_parts = [r"\A\s*"]
    cursor = 0
    for index, span in enumerate(spans):
        prose = candidate[cursor:span.start()]
        pattern_parts.append(_prose_pattern(prose))
        pattern_parts.append(f"(?P<math{index}>.*?)")
        cursor = span.end()
    pattern_parts.extend([_prose_pattern(candidate[cursor:]), r"\s*\Z"])
    alignment = re.fullmatch("".join(pattern_parts), source, flags=re.DOTALL)
    if alignment is None:
        return None, ["AI transcription changes or omits source prose outside mathematical spans."]
    replacements = []
    for index, span in enumerate(spans):
        start, end = alignment.span(f"math{index}")
        original = source[start:end]
        if not original.strip() or not span.group(1).strip():
            return None, ["Inline mathematical span has no source-aligned content."]
        # An ordinary word/sentence must not become a whole mathematical expression.
        words = re.findall(r"[^\W\d_]+", original, flags=re.UNICODE)
        if any(len(word) > 3 for word in words) and not looks_like_inline_math(original):
            return None, ["AI transcription wraps ordinary source prose as mathematics."]
        candidate_words = re.findall(r"[^\W\d_]+", span.group(1), flags=re.UNICODE)
        source_words = [word for word in words if len(word) >= 2]
        if [word for word in candidate_words if word in source_words] != source_words:
            return None, ["AI transcription drops or changes words within a proposed mathematical span."]
        replacements.append((start, end, span.group()))
    # A changed number is reviewable, even when a parser error could explain it.
    def numbers(text):
        return re.findall(r"\d+", unicodedata.normalize("NFKC", text))
    if numbers(source) != numbers(candidate):
        return None, ["AI transcription changes the sequence of source numbers."]
    output = source
    for start, end, replacement in reversed(replacements):
        output = output[:start] + replacement + output[end:]
    return output, []


def _prose_pattern(text: str) -> str:
    return "".join(r"\s+" if part.isspace() else re.escape(part) for part in re.split(r"(\s+)", text))


class _TableStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = self.rows = self.cells = 0
        self.open_table = self.open_row = self.open_cell = 0
        self.issues = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables += 1
            self.open_table += 1
        elif tag == "tr":
            if not self.open_table or self.open_row:
                self.issues.append("Table row boundaries are malformed.")
            self.rows += 1
            self.open_row += 1
        elif tag in {"td", "th"}:
            if not self.open_row or self.open_cell:
                self.issues.append("Table cell boundaries are malformed.")
            self.cells += 1
            self.open_cell += 1
            for key, value in attrs:
                if key in {"rowspan", "colspan"} and (not value or not value.isdigit() or int(value) < 1):
                    self.issues.append("Table contains an invalid merged-cell span.")

    def handle_endtag(self, tag):
        if tag == "table":
            self.open_table -= 1
        elif tag == "tr":
            self.open_row -= 1
        elif tag in {"td", "th"}:
            self.open_cell -= 1
        if min(self.open_table, self.open_row, self.open_cell) < 0:
            self.issues.append("Table boundaries are unbalanced.")


def table_structure_issues(html: str | None) -> list[str]:
    if not html:
        return ["No structured table cells are available for validation."]
    parser = _TableStructure()
    parser.feed(html)
    if not parser.tables or not parser.rows or not parser.cells:
        parser.issues.append("Structured table has no complete table/rows/cells.")
    if parser.open_table or parser.open_row or parser.open_cell:
        parser.issues.append("Structured table boundaries are unbalanced.")
    return list(dict.fromkeys(parser.issues))

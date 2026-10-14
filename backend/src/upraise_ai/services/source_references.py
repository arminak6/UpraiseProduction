"""Adapt CSV blocks to the legacy teacher's numeric citation slots, never PDF pages."""

import re

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import LessonSource

CSV_CITATION_POLICY = """
This source is CSV. Its numeric page fields are internal CSV SOURCE BLOCK identifiers,
not physical PDF pages. Source text identifies the exact CSV record ranges and headers.
Use the required [p. N]/[pp. N-M] syntax only for those supplied block identifiers;
the publication adapter converts it into CSV row citations. Do not describe these
identifiers as pages in your prose or invent missing values/units/relationships.
"""

WORD_CITATION_POLICY = """
This source is a Word document. Numeric page fields are internal WORD SOURCE BLOCK
identifiers, not physical pages. Blocks retain native headings, text, tables and
figure evidence. Use [p. N]/[pp. N-M] only for supplied block identifiers; publication
converts them to Word block citations. Never describe these numbers as PDF pages.
Do not invent content missing from the source or treat figure descriptions as verified facts.
"""


def citation_policy(section):
    return (WORD_CITATION_POLICY if section.get('source_format') in {'doc', 'docx'} else
            CSV_CITATION_POLICY if section.get('source_format') == 'csv' else '')


def citation_chunks(chunks):
    return [{**chunk, "page_numbers": chunk["source_blocks"]} if chunk.get("source_format") in {"csv", "doc", "docx"}
            else chunk for chunk in chunks]


def lesson_source(section, citations):
    citations = sorted(set(citations))
    selected = [chunk for chunk in section["chunks"] if set(chunk["page_numbers"]) & set(citations)]
    csv = section.get("source_format") == "csv"
    word = section.get('source_format') in {'doc', 'docx'}
    return LessonSource(document_id=section["document_id"], document_version_id=section["document_version_id"],
        pages=[] if csv or word else citations, source_chunk_ids=[chunk["chunk_id"] for chunk in selected],
        quality_status="NEEDS_REVIEW" if any(chunk.get("quality", {}).get("status", "UNVERIFIED")
            != "AUTO_CHECKED" for chunk in section["chunks"]) else "AUTO_CHECKED",
        source_format=section.get('source_format', 'pdf'), source_blocks=citations if csv or word else [],
        row_ranges=[span for chunk in selected for span in chunk.get("row_ranges", [])] if csv else [],
        word_references=[ref for chunk in selected for ref in chunk.get('word_references', [])] if word else [])


def display_review(report, source):
    if source.source_format in {'doc', 'docx'}:
        report = report.model_copy(deep=True)
        for issue in report.issues:
            issue.source_blocks = [n for n in issue.pages if n in source.source_blocks] or source.source_blocks
            issue.pages = []
            issue.message = display_citations(issue.message, source, strict=False)
        report.revision_instructions = [display_citations(t, source, strict=False) for t in report.revision_instructions]
        return report
    if source.source_format != "csv":
        return report
    report = report.model_copy(deep=True)
    mapping = dict(zip(source.source_blocks, source.row_ranges, strict=True))
    for issue in report.issues:
        issue.row_ranges = [mapping[block] for block in issue.pages if block in mapping] or source.row_ranges
        issue.pages = []
    return report


def display_citations(text, source, *, strict=True):
    if source.source_format in {'doc', 'docx'}:
        def word_replace(match):
            start, end = int(match[2]), int(match[3] or match[2])
            if end < start or end - start > 5000 or not set(range(start, end + 1)).issubset(source.source_blocks):
                if not strict:
                    return '[Word source reference unavailable]'
                raise IngestionError('LESSON_CITATIONS_INVALID', retryable=True)
            return f'[Word blocks {start}-{end}]'
        return re.sub(r'\[(p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]', word_replace, text)
    if source.source_format != "csv":
        return text
    mapping = dict(zip(source.source_blocks, source.row_ranges, strict=True))

    def replace(match):
        start, end = int(match[2]), int(match[3] or match[2])
        if end < start or end - start > 5000 or any(block not in mapping for block in range(start, end + 1)):
            raise IngestionError("LESSON_CITATIONS_INVALID", retryable=True)
        return f"[CSV rows {mapping[start]['start']}-{mapping[end]['end']}]"

    return re.sub(r"\[(p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]", replace, text)


def model_citations(text, source):
    if source.source_format in {'doc', 'docx'}:
        def word_replace(match):
            start, end = int(match[1]), int(match[2])
            if end < start or end - start > 5000 or not set(range(start, end + 1)).issubset(source.source_blocks):
                raise IngestionError('LESSON_CITATIONS_INVALID', retryable=True)
            return f'[p. {start}]' if start == end else f'[pp. {start}-{end}]'
        return re.sub(r'\[Word blocks (\d+)-(\d+)\]', word_replace, text)
    if source.source_format != "csv":
        return text
    mapping = dict(zip(source.source_blocks, source.row_ranges, strict=True))

    def replace(match):
        start, end = int(match[1]), int(match[2])
        first = next((block for block, span in mapping.items() if span["start"] == start), None)
        last = next((block for block, span in mapping.items() if span["end"] == end), None)
        if first is None or last is None or last < first:
            raise IngestionError("LESSON_CITATIONS_INVALID", retryable=True)
        return f"[p. {first}]" if first == last else f"[pp. {first}-{last}]"

    return re.sub(r"\[CSV rows (\d+)-(\d+)\]", replace, text)

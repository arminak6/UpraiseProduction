from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from upraise_ai.schemas.documents import DocumentElement

ProgressCallback = Callable[[str, float, str], None]


@dataclass
class ParseResult:
    page_count: int
    elements: list[DocumentElement]
    parser_name: str
    parser_version: str
    warnings: list[str] = field(default_factory=list)


class PdfParser(Protocol):
    name: str
    version: str

    def parse(
        self,
        pdf_path: Path,
        *,
        source_hash: str,
        artifact_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> ParseResult: ...


def get_processor(mime_type, settings):
    """Verified format dispatch; future processors share prepare() output."""
    if mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        from upraise_ai.services.powerpoint_processor import PowerPointProcessor

        return PowerPointProcessor(settings)
    if mime_type == "application/pdf":
        from upraise_ai.services.pdf.processor import PdfProcessor

        return PdfProcessor(settings)
    if mime_type == "text/csv":
        from upraise_ai.services.csv_processor import CsvProcessor

        return CsvProcessor(settings)
    if mime_type in {"application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
        from upraise_ai.services.word_processor import WordProcessor

        return WordProcessor(settings)
    from upraise_ai.core.errors import IngestionError

    raise IngestionError("UNSUPPORTED_DOCUMENT_FORMAT", status=415)


FORMAT_MIMES = {"pdf": "application/pdf", "csv": "text/csv", "doc": "application/msword",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def upload_format(filename, mime_type):
    """Choose a supported parser; bytes are validated before job acceptance."""
    extension = Path(filename).suffix.lower().lstrip(".")
    mime_type = (mime_type or "").split(";")[0].lower()
    if extension == 'csv' or mime_type in {'text/csv', 'application/csv'}:
        return 'csv'
    if extension in FORMAT_MIMES:
        return extension
    return next((fmt for fmt, mime in FORMAT_MIMES.items() if mime == mime_type), "pdf")

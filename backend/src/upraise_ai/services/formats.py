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
    if mime_type == "application/pdf":
        from upraise_ai.services.pdf.processor import PdfProcessor

        return PdfProcessor(settings)
    from upraise_ai.core.errors import IngestionError

    raise IngestionError("UNSUPPORTED_DOCUMENT_FORMAT", status=415)

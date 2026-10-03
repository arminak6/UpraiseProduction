from pathlib import Path

from pypdf import PdfReader

from upraise_ai.core.errors import IngestionError


def validate_pdf_input(path: Path, max_bytes: int, max_pages: int) -> int:
    """Check parser support and resource limits, without a document guard."""
    if path.stat().st_size == 0:
        raise IngestionError("EMPTY_PDF")
    if path.stat().st_size > max_bytes:
        raise IngestionError("PDF_TOO_LARGE", status=413)
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise IngestionError("ENCRYPTED_PDF_UNSUPPORTED")
        page_count = len(reader.pages)
        if not 0 < page_count <= max_pages:
            raise IngestionError("PDF_PAGE_LIMIT")
        return page_count
    except IngestionError:
        raise
    except Exception:
        raise IngestionError("MALFORMED_PDF") from None

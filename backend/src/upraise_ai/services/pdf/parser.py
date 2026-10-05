from __future__ import annotations

import json
import math
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from PIL import Image

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import deterministic_uuid, project_relative
from upraise_ai.schemas.documents import BoundingBox, DocumentElement, ElementType, SourceRegion
from upraise_ai.services.formats import ParseResult, ProgressCallback
from upraise_ai.workers.budgets import phase_timeout_seconds

from .inline_math import looks_like_inline_math
from .input import validate_pdf_input
from .languages import ocr_languages

LABEL_MAP: dict[str, ElementType] = {
    "title": ElementType.TITLE,
    "section_header": ElementType.HEADING,
    "paragraph": ElementType.TEXT,
    "text": ElementType.TEXT,
    "list_item": ElementType.LIST_ITEM,
    "table": ElementType.TABLE,
    "picture": ElementType.FIGURE,
    "formula": ElementType.FORMULA,
    "code": ElementType.CODE,
    "caption": ElementType.CAPTION,
    "page_header": ElementType.HEADER,
    "page_footer": ElementType.FOOTER,
    "footnote": ElementType.TEXT,
}
INLINE_MATH_TEXT_TYPES = {
    ElementType.TITLE,
    ElementType.HEADING,
    ElementType.TEXT,
    ElementType.LIST_ITEM,
    ElementType.CAPTION,
}


class DoclingPdfParser:
    name = "docling"

    def __init__(self, settings: Settings, language: str, *, page_count: int | None = None):
        self.language = language
        self.settings = settings
        self.page_count = page_count
        self._page_count_hint = page_count
        try:
            self.version = version("docling")
        except PackageNotFoundError:
            self.version = "not-installed"
        self._converter: Any | None = None

    def _build_converter(self) -> Any:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                EasyOcrOptions,
                PdfPipelineOptions,
                TableFormerMode,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:
            raise RuntimeError(
                "Docling is not installed. Install project requirements before processing PDFs."
            ) from exc

        options = PdfPipelineOptions()
        options.document_timeout = phase_timeout_seconds(
            self.settings, "parse", self.page_count if self.page_count is not None else self.settings.max_pdf_pages
        )
        options.do_ocr = self.settings.enable_ocr
        options.ocr_options = EasyOcrOptions(lang=ocr_languages(self.language), use_gpu=False)
        options.do_table_structure = True
        options.table_structure_options.mode = TableFormerMode.ACCURATE
        options.images_scale = 2.0
        options.generate_page_images = True
        options.generate_picture_images = True
        options.do_formula_enrichment = self.settings.enable_formula_enrichment

        return DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            },
        )

    @property
    def converter(self) -> Any:
        if self._converter is None:
            self._converter = self._build_converter()
        return self._converter

    def parse(
        self,
        pdf_path: Path,
        *,
        source_hash: str,
        artifact_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        page_count = self._page_count_hint
        self._page_count_hint = None
        if page_count is None:
            page_count = validate_pdf_input(
                pdf_path, self.settings.max_pdf_size_mb * 1024**2, self.settings.max_pdf_pages
            )
        if self.page_count != page_count:
            self.page_count = page_count
            self._converter = None
        if progress:
            progress("parse", 0.12, "Docling is analysing document layout.")

        artifact_dir.mkdir(parents=True, exist_ok=True)
        figures_dir = artifact_dir / "figures"
        tables_dir = artifact_dir / "tables"
        formulas_dir = artifact_dir / "formulas"
        inline_math_dir = artifact_dir / "inline_math"
        figures_dir.mkdir(parents=True, exist_ok=True)
        tables_dir.mkdir(parents=True, exist_ok=True)
        formulas_dir.mkdir(parents=True, exist_ok=True)
        inline_math_dir.mkdir(parents=True, exist_ok=True)

        conversion = self.converter.convert(pdf_path)
        has_timeout_errors = getattr(conversion, "has_timeout_errors", None)
        if callable(has_timeout_errors) and has_timeout_errors():
            raise IngestionError("PROCESSING_TIMEOUT", retryable=True)
        if str(getattr(conversion.status, "value", conversion.status)) != "success":
            raise IngestionError("PDF_CONVERSION_INCOMPLETE")
        document = conversion.document
        warnings: list[str] = []
        elements: list[DocumentElement] = []
        heading_stack: list[tuple[int, str]] = []

        items = list(document.iterate_items(traverse_pictures=True))
        item_count = max(len(items), 1)
        for index, (item, level) in enumerate(items):
            if progress and index % max(1, item_count // 20) == 0:
                progress(
                    "parse",
                    0.12 + (0.28 * index / item_count),
                    f"Extracting element {index + 1} of {item_count}.",
                )

            label = self._label_value(getattr(item, "label", None))
            element_type = LABEL_MAP.get(label, ElementType.OTHER)
            original_text = self._text_for_item(item, document, element_type)
            structured_content = None
            caption = self._caption_for_item(item, document)
            crop_destination = None

            if element_type is ElementType.TABLE:
                structured_content = self._table_html(item, document)
                crop_destination = tables_dir / f"table_{index:05d}.png"
            elif element_type is ElementType.FIGURE:
                crop_destination = figures_dir / f"figure_{index:05d}.png"
            elif element_type is ElementType.FORMULA:
                crop_destination = formulas_dir / f"formula_{index:05d}.png"
            elif element_type in INLINE_MATH_TEXT_TYPES and looks_like_inline_math(original_text):
                crop_destination = inline_math_dir / f"inline_math_{index:05d}.png"

            source_regions = self._source_regions(
                item, document, crop_destination, warnings, element_index=index,
            )
            asset_path = next((region.asset_path for region in source_regions if region.asset_path), None)

            if element_type in {ElementType.TITLE, ElementType.HEADING}:
                heading_level = max(int(level or 1), 1)
                while heading_stack and heading_stack[-1][0] >= heading_level:
                    heading_stack.pop()
                if original_text.strip():
                    heading_stack.append((heading_level, original_text.strip()))

            first_region = source_regions[0] if source_regions else None
            page_number = first_region.page_number if first_region and first_region.page_number > 0 else None
            bbox = first_region.bounding_box if first_region else None
            source_reference = self._source_reference(item)
            element_id = deterministic_uuid(f"{source_hash}:element:{index}:{source_reference or label}")
            element_metadata: dict[str, Any] = {
                "hierarchy_level": int(level or 0),
                "provenance": [
                    {
                        "page": region.page_number,
                        "charspan": region.charspan,
                        "bbox": {
                            "l": region.bounding_box.left,
                            "t": region.bounding_box.top,
                            "r": region.bounding_box.right,
                            "b": region.bounding_box.bottom,
                            "coord_origin": region.bounding_box.coordinate_origin,
                        } if region.bounding_box else None,
                    }
                    for region in source_regions
                ],
            }
            if element_type is ElementType.FORMULA:
                element_metadata["formula_extraction_method"] = (
                    "docling" if original_text.strip() else "empty"
                )
            elif element_type in INLINE_MATH_TEXT_TYPES and looks_like_inline_math(original_text):
                element_metadata["inline_math_candidate"] = True
                element_metadata["inline_math_extraction_method"] = "docling_text"

            elements.append(
                DocumentElement(
                    element_id=element_id,
                    element_index=index,
                    element_type=element_type,
                    original_text=original_text,
                    structured_content=structured_content,
                    source_text=original_text,
                    source_structured_content=structured_content,
                    source_regions=source_regions,
                    caption=caption,
                    page_number=page_number,
                    page_numbers=sorted({region.page_number for region in source_regions if region.page_number > 0}),
                    bounding_box=bbox,
                    heading_path=[entry[1] for entry in heading_stack],
                    asset_path=asset_path,
                    source_reference=source_reference,
                    parser_label=label,
                    metadata=element_metadata,
                )
            )

        page_count = len(getattr(document, "pages", {}) or {})
        self._save_native_document(document, artifact_dir, warnings)

        if not any(element.is_retrievable for element in elements):
            warnings.append("Docling did not extract any retrievable content.")

        if progress:
            progress("parse", 0.40, "Structured PDF extraction is complete.")

        return ParseResult(
            page_count=page_count,
            elements=elements,
            parser_name=self.name,
            parser_version=self.version,
            warnings=warnings,
        )

    @staticmethod
    def _label_value(label: Any) -> str:
        if label is None:
            return "other"
        value = getattr(label, "value", label)
        return str(value).lower()

    @staticmethod
    def _source_reference(item: Any) -> str | None:
        reference = getattr(item, "self_ref", None)
        if reference is None:
            return None
        return str(getattr(reference, "cref", reference))

    @staticmethod
    def _caption_for_item(item: Any, document: Any) -> str | None:
        caption_method = getattr(item, "caption_text", None)
        if not callable(caption_method):
            return None
        try:
            caption = caption_method(document)
        except (AttributeError, TypeError, ValueError):
            return None
        caption = str(caption or "").strip()
        return caption or None

    @staticmethod
    def _text_for_item(
        item: Any,
        document: Any,
        element_type: ElementType,
    ) -> str:
        if element_type is ElementType.TABLE:
            export = getattr(item, "export_to_markdown", None)
            if callable(export):
                try:
                    return str(export(doc=document) or "").strip()
                except (AttributeError, TypeError, ValueError):
                    pass
        return str(getattr(item, "text", "") or "").strip()

    @staticmethod
    def _table_html(item: Any, document: Any) -> str | None:
        export = getattr(item, "export_to_html", None)
        if callable(export):
            try:
                value = str(export(doc=document) or "").strip()
                return value or None
            except (AttributeError, TypeError, ValueError):
                return None
        return None

    def _source_regions(
        self,
        item: Any,
        document: Any,
        destination: Path | None,
        warnings: list[str],
        *,
        element_index: int,
    ) -> list[SourceRegion]:
        """Retain every provenance span; crop only its own page and bounding box."""
        provenance = list(getattr(item, "prov", []) or [])
        if not provenance and destination is not None:
            warnings.append(f"Element {element_index} has no source provenance for visual verification.")
        regions: list[SourceRegion] = []
        for region_index, prov in enumerate(provenance):
            context = f"element {element_index}, region {region_index}"
            try:
                page_number = int(getattr(prov, "page_no", None))
                if page_number < 1:
                    raise ValueError("page number is not positive")
            except (TypeError, ValueError, OverflowError):
                page_number = 0
                warnings.append(f"Missing or invalid source page number ({context}).")

            try:
                charspan = [int(value) for value in (getattr(prov, "charspan", []) or [])]
            except (TypeError, ValueError, OverflowError):
                charspan = []
                warnings.append(f"Invalid source character span ({context}).")

            bbox = getattr(prov, "bbox", None)
            bounding_box = None
            if bbox is not None:
                try:
                    coordinates = [float(getattr(bbox, name)) for name in ("l", "t", "r", "b")]
                    if not all(math.isfinite(value) for value in coordinates):
                        raise ValueError("nonfinite coordinates")
                    origin = getattr(bbox, "coord_origin", None)
                    origin = getattr(origin, "value", origin)
                    bounding_box = BoundingBox(
                        left=coordinates[0], top=coordinates[1],
                        right=coordinates[2], bottom=coordinates[3],
                        coordinate_origin=str(origin) if origin is not None else None,
                    )
                except (AttributeError, TypeError, ValueError, OverflowError):
                    warnings.append(f"Invalid source bounding box ({context}).")

            region = SourceRegion(page_number=page_number, charspan=charspan, bounding_box=bounding_box)
            if destination is not None:
                region_destination = destination.with_name(
                    f"{destination.stem}_region_{region_index:03d}_page_{page_number:05d}.png"
                )
                try:
                    self._save_region_image(document, region, region_destination)
                    region.asset_path = project_relative(region_destination, self.settings.project_root)
                except (AttributeError, KeyError, OSError, TypeError, ValueError, OverflowError) as exc:
                    warnings.append(f"Could not save complete source crop ({context}): {exc}")
            regions.append(region)
        return regions

    @staticmethod
    def _save_region_image(document: Any, region: SourceRegion, destination: Path) -> None:
        # FloatingItem.get_image can return its embedded image instead of the
        # requested provenance. Direct page cropping avoids that ambiguity.
        page = (getattr(document, "pages", {}) or {}).get(region.page_number)
        if page is None:
            raise ValueError("source page is unavailable")
        bbox = region.bounding_box
        if bbox is None:
            raise ValueError("source bounding box is unavailable")
        page_size = getattr(page, "size", None)
        width, height = float(page_size.width), float(page_size.height)
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise ValueError("source page dimensions are invalid")
        image_ref = getattr(page, "image", None)
        image = image_ref if isinstance(image_ref, Image.Image) else getattr(image_ref, "pil_image", None)
        if not isinstance(image, Image.Image):
            raise ValueError("source page image is unavailable")
        left, right = bbox.left, bbox.right
        if bbox.coordinate_origin == "TOPLEFT":
            top, bottom = bbox.top, bbox.bottom
        elif bbox.coordinate_origin == "BOTTOMLEFT":
            top, bottom = height - bbox.top, height - bbox.bottom
        else:
            raise ValueError("source bounding box has an unknown coordinate origin")
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("source bounding box is empty, inverted, or outside its page")
        # Round outwards to retain all pixels touched by the source region.
        pixels = (
            math.floor(left * image.width / width),
            math.floor(top * image.height / height),
            math.ceil(right * image.width / width),
            math.ceil(bottom * image.height / height),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.crop(pixels).save(destination, format="PNG")

    @staticmethod
    def _save_native_document(
        document: Any,
        artifact_dir: Path,
        warnings: list[str],
    ) -> None:
        destination = artifact_dir / "docling_document.json"
        save_method = getattr(document, "save_as_json", None)
        try:
            if callable(save_method):
                save_method(destination)
                return
            export_method = getattr(document, "export_to_dict", None)
            if callable(export_method):
                destination.write_text(
                    json.dumps(export_method(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except (OSError, TypeError, ValueError) as exc:
            warnings.append(f"Could not save Docling's native JSON: {exc}")

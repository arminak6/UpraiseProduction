import sys
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.documents import DocumentElement, ElementType, IngestionMetadata
from upraise_ai.services.formats import ParseResult
from upraise_ai.services.pdf.parser import DoclingPdfParser
from upraise_ai.services.pdf.processor import PdfProcessor
from upraise_ai.workers.budgets import enrichment_workload, notify_workload, phase_timeout_seconds


@pytest.mark.parametrize("pages", [1, 200, 500])
def test_parse_budget_scales_with_pages_and_ocr(pages):
    settings = Settings()
    assert phase_timeout_seconds(settings, "parse", pages) == 180 + (30 * pages)
    without_ocr = settings.model_copy(update={"enable_ocr": False})
    assert phase_timeout_seconds(without_ocr, "parse", pages) == 180 + (10 * pages)


def test_runtime_budgets_are_configurable_and_do_not_change_pipeline_identity():
    original = Settings()
    changed = Settings(
        job_timeout_seconds=600,
        parse_timeout_base_seconds=20,
        parse_timeout_per_ocr_page_seconds=5,
        enrich_timeout_base_seconds=30,
        enrich_timeout_per_region_seconds=7,
        embed_timeout_base_seconds=40,
        embed_timeout_per_chunk_seconds=11,
    )
    assert changed.pipeline_signature == original.pipeline_signature
    assert phase_timeout_seconds(changed, "parse", 20) == 120
    assert phase_timeout_seconds(changed, "enrich", 20) == 170
    assert phase_timeout_seconds(changed, "embed", 20) == 260
    assert phase_timeout_seconds(changed, "chunk", 20) == 500
    assert phase_timeout_seconds(changed, "store", 20) == 400
    assert phase_timeout_seconds(changed, "startup", 0) == 300
    assert phase_timeout_seconds(changed, "init", 0) == 300
    for phase in ("parse", "enrich", "chunk", "embed", "store"):
        assert phase_timeout_seconds(changed, phase, 10_000) == 600


@pytest.mark.parametrize("units", [-1, 1.5, True])
def test_budget_rejects_invalid_workloads(units):
    with pytest.raises(ValueError, match="nonnegative integer"):
        phase_timeout_seconds(Settings(), "parse", units)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_absolute_budget_must_be_positive_and_finite(value):
    with pytest.raises(ValidationError):
        Settings(job_timeout_seconds=value)


def element(element_type, *, text="", crop="crop.png", inline=False):
    return DocumentElement(
        element_id=f"{element_type.value}:{text}",
        element_index=0,
        element_type=element_type,
        original_text=text,
        asset_path=crop,
        metadata={"inline_math_candidate": inline},
    )


def test_enrichment_workload_counts_every_eligible_crop():
    elements = [
        element(ElementType.FORMULA),
        element(ElementType.FORMULA, text="x^2"),
        element(ElementType.FORMULA, crop=None),
        element(ElementType.TEXT, text="x = 1", inline=True),
        element(ElementType.TEXT, text="x = 2", crop=None, inline=True),
        element(ElementType.TEXT, text="ordinary prose"),
        element(ElementType.FIGURE),
        element(ElementType.FIGURE, crop=None),
    ]
    assert enrichment_workload(elements, describe_images=True) == 3
    assert enrichment_workload(elements, describe_images=False) == 2
    assert enrichment_workload([element(ElementType.FIGURE) for _ in range(1_000)], describe_images=True) == 1_000


def test_workload_hook_preserves_three_argument_progress_callbacks():
    calls = []

    def legacy(step, fraction, message):
        calls.append((step, fraction, message))

    notify_workload(legacy, "parse", 200)
    assert calls == []
    legacy("parse", 0.1, "Extracting PDF.")
    assert calls == [("parse", 0.1, "Extracting PDF.")]

    class Progress:
        def set_workload(self, phase, units):
            calls.append((phase, units))

    notify_workload(Progress(), "enrich", 350)
    assert calls[-1] == ("enrich", 350)


def test_docling_converter_receives_document_specific_timeout(monkeypatch):
    captured = {}
    base_models = ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = SimpleNamespace(PDF="pdf")
    pipeline_options = ModuleType("docling.datamodel.pipeline_options")
    pipeline_options.EasyOcrOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    pipeline_options.PdfPipelineOptions = lambda: SimpleNamespace(table_structure_options=SimpleNamespace())
    pipeline_options.TableFormerMode = SimpleNamespace(ACCURATE="accurate")
    document_converter = ModuleType("docling.document_converter")
    document_converter.PdfFormatOption = lambda **kwargs: SimpleNamespace(**kwargs)

    def converter(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    document_converter.DocumentConverter = converter
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", base_models)
    monkeypatch.setitem(sys.modules, "docling.datamodel.pipeline_options", pipeline_options)
    monkeypatch.setitem(sys.modules, "docling.document_converter", document_converter)
    parser = DoclingPdfParser(Settings(), "en", page_count=200)
    parser._build_converter()
    options = captured["format_options"]["pdf"].pipeline_options
    assert options.document_timeout == 6_180
    assert options.generate_page_images and options.generate_picture_images


@pytest.mark.parametrize("timed_out", [True, False])
def test_partial_docling_output_is_never_returned(tmp_path, timed_out):
    parser = DoclingPdfParser(Settings(), "en", page_count=1)
    parser._converter = SimpleNamespace(convert=lambda _: SimpleNamespace(
        status="partial_success", has_timeout_errors=lambda: timed_out,
    ))
    expected = "PROCESSING_TIMEOUT" if timed_out else "PDF_CONVERSION_INCOMPLETE"
    with pytest.raises(IngestionError, match=expected) as error:
        parser.parse(tmp_path / "source.pdf", source_hash="hash", artifact_dir=tmp_path / "attempt")
    assert error.value.retryable is timed_out


def test_processor_announces_workload_before_each_expensive_phase(tmp_path, monkeypatch):
    from upraise_ai.services.pdf import processor as processor_module

    events = []
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source bytes")
    monkeypatch.setattr(processor_module, "validate_pdf_input", lambda *_: 200)

    class Progress:
        def __call__(self, step, fraction, message):
            pass

        def set_workload(self, phase, units):
            events.append((phase, units))

    class Parser:
        # Deliberately retain the old parser argument contract.
        def parse(self, path, *, source_hash, artifact_dir, progress):
            assert events[-1] == ("parse", 200)
            return ParseResult(200, [
                element(ElementType.TEXT, text="Source lesson material"),
                element(ElementType.FORMULA),
                element(ElementType.FIGURE),
            ], "fake", "1")

    class Enricher:
        def enrich(self, parsed, progress):
            assert events[-1] == ("enrich", 2)
            parsed.elements[1].original_text = "x^2"
            parsed.elements[2].derived_description = "Source diagram"
            return []

    class Embedder:
        def embed_texts(self, texts):
            assert events[-1] == ("embed", 3)
            return [[1.0] * 1_024 for _ in texts]

    settings = Settings(data_dir=tmp_path)
    processor = PdfProcessor(settings, parser=Parser(), enricher=Enricher(), embedder=Embedder())
    metadata = IngestionMetadata(
        course_id="course", activity_id="activity", language="en", source_ref="source",
        content_hash="declared-hash", source_filename="teaching.pdf",
    )
    processor.prepare(source, metadata, "document", tmp_path, "token", Progress())
    assert events == [("parse", 200), ("enrich", 2), ("chunk", 200), ("embed", 3)]

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from upraise_ai.core.config import Settings
from upraise_ai.schemas.documents import (
    BoundingBox,
    ContentQuality,
    DocumentElement,
    ElementType,
    IngestionMetadata,
    ProcessedDocument,
    QualityStatus,
    SourceRegion,
)
from upraise_ai.services.chunking import StructureAwareChunker
from upraise_ai.services.quality import aggregate_quality, quality_summary
from upraise_ai.storage.vectors import VectorStore
from upraise_ai.workers.ingestion import IngestionWorker


def metadata():
    return IngestionMetadata(
        course_id="course", activity_id="activity", language="en", source_ref="source",
        content_hash="declared-revision", source_filename="general.pdf",
    )


def element(element_id, status=QualityStatus.UNVERIFIED, page=1):
    return DocumentElement(
        element_id=element_id, element_index=page - 1, element_type=ElementType.TEXT,
        original_text=f"Accepted content from {element_id}.", source_text=f"Original content from {element_id}.",
        page_number=page, page_numbers=[page],
        quality=ContentQuality(
            status=status, checks=["independent_source_comparison"] if status is QualityStatus.AUTO_CHECKED else [],
            issues=["formula_symbol_disagreement"] if status is QualityStatus.NEEDS_REVIEW else [],
            evidence_complete=True,
        ),
        source_regions=[SourceRegion(
            page_number=page, asset_path=f"crop-{element_id}.png", charspan=[0, 20],
            bounding_box=BoundingBox(left=10, top=20, right=100, bottom=80),
        )],
    )


def processed(elements):
    meta = metadata()
    chunks = StructureAwareChunker().chunk(
        elements, metadata=meta, document_id="document", document_version_id="revision", source_hash="actual-hash",
    )
    return ProcessedDocument(
        document_id="document", document_version_id="revision", source_hash="actual-hash", metadata=meta,
        parser_name="test", parser_version="1", pipeline_version="test-pipeline",
        embedding_model_id="test-embedding", embedding_dimension=2,
        page_count=max((item.page_number or 1 for item in elements), default=1),
        elements=elements, chunks=chunks, warnings=[],
        quality=aggregate_quality(elements), quality_summary=quality_summary(elements),
    )


@pytest.mark.parametrize("other,expected", [
    (QualityStatus.AUTO_CHECKED, QualityStatus.AUTO_CHECKED),
    (QualityStatus.UNVERIFIED, QualityStatus.UNVERIFIED),
    (QualityStatus.NEEDS_REVIEW, QualityStatus.NEEDS_REVIEW),
])
def test_mixed_chunk_cannot_upgrade_unverified_or_flagged_content(other, expected):
    document = processed([element("checked", QualityStatus.AUTO_CHECKED), element("other", other, 2)])
    assert len(document.chunks) == 1
    chunk = document.chunks[0]
    assert chunk.quality.status is expected
    assert chunk.element_quality["other"].status is other
    assert chunk.element_quality["checked"].status is QualityStatus.AUTO_CHECKED
    assert chunk.page_numbers == [1, 2]
    assert chunk.element_source_regions["other"][0].page_number == 2
    assert chunk.element_source_regions["checked"][0].asset_path == "crop-checked.png"
    document.elements[0].quality.status = QualityStatus.NEEDS_REVIEW
    assert chunk.element_quality["checked"].status is QualityStatus.AUTO_CHECKED


def test_review_flag_survives_unverified_elements_and_empty_quality_is_unverified():
    elements = [element("unknown"), element("flagged", QualityStatus.NEEDS_REVIEW, 2)]
    quality = aggregate_quality(elements)
    assert quality.status is QualityStatus.NEEDS_REVIEW
    assert quality.issues == ["flagged: formula_symbol_disagreement"]
    assert quality_summary(elements) == {"total": 2, "auto_checked": 0, "unverified": 1, "needs_review": 1}
    assert aggregate_quality([]).status is QualityStatus.UNVERIFIED
    assert not aggregate_quality([]).evidence_complete


def test_rejected_candidates_never_enter_chunk_text():
    item = element("text", QualityStatus.NEEDS_REVIEW)
    item.candidate_text = "Invented answer candidate"
    figure = DocumentElement(
        element_id="figure", element_index=1, element_type=ElementType.FIGURE,
        caption="Source caption", candidate_description="Invented transition candidate",
        quality=ContentQuality(status=QualityStatus.NEEDS_REVIEW),
    )
    document = processed([item, figure])
    assert "Accepted content from text." in document.chunks[0].text
    assert all("Invented" not in chunk.text and "Invented" not in chunk.embedding_text for chunk in document.chunks)
    assert "Source caption" in document.chunks[1].text


@pytest.mark.parametrize("element_type", [ElementType.FORMULA, ElementType.FIGURE])
def test_unresolved_visual_region_keeps_source_coverage_without_candidate(element_type):
    item = DocumentElement(
        element_id="unresolved", element_index=0, element_type=element_type, page_number=7, page_numbers=[7],
        asset_path="source-region.png", candidate_text="Invented formula", candidate_description="Invented arrow",
        source_regions=[SourceRegion(page_number=7, asset_path="source-region.png")],
        quality=ContentQuality(status=QualityStatus.NEEDS_REVIEW, issues=["source_comparison_failed"]),
    )
    document = processed([item])
    assert len(document.chunks) == 1
    chunk = document.chunks[0]
    assert "Source region requires review" in chunk.text
    assert "Invented" not in chunk.text
    assert chunk.page_numbers == [7] and chunk.quality.status is QualityStatus.NEEDS_REVIEW
    assert chunk.element_source_regions["unresolved"][0].asset_path == "source-region.png"


def test_legacy_checkpoint_defaults_are_never_auto_checked():
    legacy = processed([element("old", QualityStatus.AUTO_CHECKED)]).model_dump(mode="json")
    for name in ("quality", "quality_summary"):
        legacy.pop(name)
    for item in legacy["elements"]:
        for name in ("quality", "source_regions", "source_text", "source_structured_content", "candidate_text",
                     "candidate_description"):
            item.pop(name)
    for chunk in legacy["chunks"]:
        for name in ("quality", "element_quality", "source_regions", "element_source_regions"):
            chunk.pop(name)
    loaded = ProcessedDocument.model_validate(legacy)
    assert loaded.quality.status is QualityStatus.UNVERIFIED
    assert loaded.elements[0].quality.status is QualityStatus.UNVERIFIED
    assert loaded.chunks[0].quality.status is QualityStatus.UNVERIFIED
    assert loaded.chunks[0].element_quality == {}


def test_vector_payload_keeps_quality_and_element_scoped_provenance():
    document = processed([element("checked", QualityStatus.AUTO_CHECKED), element("flagged", QualityStatus.NEEDS_REVIEW, 2)])
    captured = []
    store = object.__new__(VectorStore)
    store.collection = "quality-test"
    store.dimension = 2
    store.client = SimpleNamespace(
        upsert=lambda collection, points, **kwargs: captured.extend(points),
        count=lambda *args, **kwargs: SimpleNamespace(count=len(captured)),
    )
    store.ensure_collection = lambda: None
    store.remove_attempt = lambda *args: None
    assert store.write(document, [[1.0, 0.0]], "attempt") == 1
    payload = captured[0].payload
    assert payload["ingestion_status"] == "INDEXED"
    assert payload["quality_status"] == "NEEDS_REVIEW" and payload["requires_review"] is True
    assert payload["document_quality_status"] == "NEEDS_REVIEW"
    assert payload["element_quality"]["checked"]["status"] == "AUTO_CHECKED"
    assert payload["element_quality"]["flagged"]["issues"] == ["formula_symbol_disagreement"]
    assert payload["element_source_regions"]["flagged"][0] == {
        "page_number": 2, "asset_path": "crop-flagged.png", "charspan": [0, 20],
        "bounding_box": {"left": 10.0, "top": 20.0, "right": 100.0, "bottom": 80.0, "coordinate_origin": None},
    }
    assert [region["page_number"] for region in payload["source_regions"]] == [1, 2]


@pytest.mark.parametrize("status,requires_review", [
    (QualityStatus.UNVERIFIED, False), (QualityStatus.NEEDS_REVIEW, True), (QualityStatus.AUTO_CHECKED, False),
])
def test_done_reports_quality_separately_from_processing_success(tmp_path, status, requires_review):
    document = processed([element("content", status)])
    settings = Settings(data_dir=tmp_path)
    results = []

    @contextmanager
    def owned_transaction(*args):
        yield object()

    jobs = SimpleNamespace(
        progress=lambda *args: None, owned_transaction=owned_transaction,
        done=lambda conn, job_id, token, result: results.append(result),
    )
    vectors = SimpleNamespace(write=lambda *args: len(document.chunks), cleanup_old_attempts=lambda *args: None)
    worker = IngestionWorker(
        settings, jobs=jobs, audit=SimpleNamespace(record=lambda *args, **kwargs: None), vectors=vectors,
        processor=SimpleNamespace(prepare=lambda *args: (document, [[1.0, 0.0]])), notify=lambda *args: None,
    )
    job = {
        "job_id": str(uuid4()), "lease_token": str(uuid4()), "document_id": "document", "attempts": 1,
        "pipeline_version": settings.pipeline_signature, "metadata": document.metadata.model_dump(mode="json"),
    }
    worker._process(job)
    assert len(results) == 1
    assert results[0]["quality_status"] == status.value
    assert results[0]["requires_review"] is requires_review
    assert results[0]["quality_summary"] == document.quality_summary
    assert results[0]["warning_count"] == 0

"""CSV A1/A3/A7/lifecycle with isolated PG/Qdrant and fake providers."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_a1 import FakeEmbedder, metadata, pdf_bytes
from test_a3 import request_data
from test_a3_integration import api, process
from test_csv_processor import csv_metadata

from upraise_ai.guards.ingress import ingress
from upraise_ai.main import app
from upraise_ai.schemas.aligned_lessons import AlignedReview, AlignedTexts
from upraise_ai.schemas.lessons import ReviewReport, SectionDraft
from upraise_ai.services.csv_processor import CsvProcessor
from upraise_ai.storage.files import StagingStore
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.submission import SubmissionService

pytest_plugins = ["test_a1_integration", "test_a3_integration"]

RAW = b"Concept,Definition\nStates,Finite set of control states\nTape,Unbounded read write memory\n"


@contextmanager
def upload_api(integration, lesson):
    settings, jobs, audit, vectors, worker, _ = integration
    app.dependency_overrides[ingress] = lambda: lesson[4]
    try:
        with TestClient(app) as client:
            app.state.submission = SubmissionService(settings, jobs, StagingStore(settings.data_dir))
            app.state.jobs = worker.job_control
            app.state.lessons = lesson[3]
            app.state.documents = DocumentService(jobs, vectors, audit, lessons=lesson[3])
            yield client
    finally:
        app.dependency_overrides.clear()


def upload(integration, lesson, raw=RAW, key="csv-one"):
    with upload_api(integration, lesson) as client:
        response = client.post("/v1/documents", data=csv_metadata().model_dump(
            exclude={"source_filename", "source_format"}, exclude_none=True),
            files={"file": ("lesson.csv", raw, "text/csv")}, headers={"Idempotency-Key": key})
        assert response.status_code == 202, response.text
        assert set(response.json()) == {"document_id", "job_id"}
        return response.json()


def ingest(integration, monkeypatch):
    settings, jobs, _, _, worker, _ = integration
    worker.processor = None
    monkeypatch.setattr("upraise_ai.workers.ingestion.get_processor", lambda mime, cfg:
        CsvProcessor(cfg, embedder=FakeEmbedder(cfg.embedding_dimension)))
    row = jobs.claim(settings.lease_seconds, settings.pipeline_signature)
    worker.process(row)
    assert jobs.get(row["job_id"])["status"] == "DONE"


def test_csv_upload_retry_worker_vector_provenance_and_a6_a2(integration, lesson, monkeypatch):
    accepted = upload(integration, lesson)
    assert upload(integration, lesson) == accepted
    with upload_api(integration, lesson) as client:
        bad = client.post("/v1/documents", data=csv_metadata().model_dump(
            exclude={"source_filename", "source_format"}, exclude_none=True),
            files={"file": ("lesson.csv", b"Name,Value\nmissing\n", "text/csv")})
        assert bad.status_code == 422 and bad.json()["detail"] == "CSV_COLUMN_COUNT_MISMATCH"
    ingest(integration, monkeypatch)
    settings, jobs, _, vectors, worker, _ = integration
    row = jobs.get(accepted["job_id"])
    chunks = vectors.document_sections(accepted["document_id"], "course-1", row["active_attempt"])
    assert chunks[0]["page"] is None and chunks[0]["page_numbers"] == []
    assert chunks[0]["source_format"] == "csv" and chunks[0]["row_ranges"] == [{"start": 2, "end": 3}]
    with upload_api(integration, lesson) as client:
        status = client.get(f"/v1/jobs/{accepted['job_id']}").json()
        assert status["result"]["row_count"] == 2 and status["result"]["page_count"] == 0
        assert status["result"]["source_block_count"] == 1
        assert client.delete(f"/v1/jobs/{accepted['job_id']}", params={"reason": "RECEIVED"}).status_code == 204
        assert worker.staging.source_path(accepted["job_id"]).is_file()
        assert vectors.document_count(accepted["document_id"], "course-1") == 1
        assert client.delete(f"/v1/documents/{accepted['document_id']}").status_code == 204
    assert worker.staging.source_path(accepted["job_id"]).is_file()
    assert jobs.get(accepted["job_id"])["deleted_at"] is not None
    assert vectors.document_count(accepted["document_id"], "course-1") == 0


def test_csv_cancellation_preserves_source_and_prevents_publication(integration, lesson):
    accepted = upload(integration, lesson)
    with upload_api(integration, lesson) as client:
        response = client.delete(f"/v1/jobs/{accepted['job_id']}", params={"reason": "CANCEL"})
        assert response.status_code == 204
        assert client.get(f"/v1/jobs/{accepted['job_id']}").json()["status"] == "CANCELLED"
    assert integration[4].staging.source_path(accepted["job_id"]).is_file()
    assert integration[1].claim(integration[0].lease_seconds) is None


@pytest.mark.parametrize("aligned", [False, True])
def test_csv_generation_has_row_citations_all_levels_and_revision_preserves_them(integration, lesson, monkeypatch, aligned):
    doc = upload(integration, lesson)
    ingest(integration, monkeypatch)
    def generate(section, language, *args, **kwargs):
        assert section["source_format"] == "csv"
        text = "The CSV defines control states and read write tape memory. " * 3 + "[p. 1]"
        return SectionDraft(paragraphs=[{"text": text, "pages": [1]}]), ReviewReport(approved=True, summary="Checked."), 0
    monkeypatch.setattr(lesson[5], "generate_section", generate)
    monkeypatch.setattr(lesson[5], "adapt_units", lambda section, units, language, **kwargs:
        (AlignedTexts(units=[{"content_unit_id": unit.content_unit_id, "text": unit.material_text} for unit in units]),
         {str(unit.content_unit_id): ReviewReport(approved=True, summary="Checked.") for unit in units}), raising=False)
    with api(integration, lesson) as client:
        accepted = client.post("/v2/lesson-jobs" if aligned else "/v1/lesson-jobs",
            json=request_data(document_ids=[doc["document_id"]]))
        assert accepted.status_code == 202, accepted.text
    settings, jobs, worker, service, _, models = lesson
    worker.process(jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature]))
    root = jobs.get(accepted.json()["job_id"])
    assert root["status"] == "DONE", root["error_code"]
    result = service.result(root)
    variants = result.variants.values() if aligned else [result]
    for variant in variants:
        assert variant.chunks[0].sources[0].pages == []
        assert variant.chunks[0].sources[0].row_ranges == [{"start": 2, "end": 3}]
        assert "[CSV rows 2-3]" in variant.chunks[0].text and "[p." not in variant.chunks[0].text
        assert variant.chunks[0].reviewer_report.issues[0].pages == []
    def revise(section, original, feedback, neighbours, **kwargs):
        assert section["source_format"] == "csv" and "[p. 1]" in original
        return "A clearer CSV explanation. [p. 1]", ReviewReport(approved=True, summary="Checked."), 0
    monkeypatch.setattr(models, "revise_chunk", revise, raising=False)
    if aligned:
        models.transport = SimpleNamespace(language="en", teaching_prompt=None)
        monkeypatch.setattr("upraise_ai.services.revision_pipeline.reviewed", lambda models, section, units, *args:
            AlignedReview(units=[{"content_unit_id": unit.content_unit_id, "material_preserved": True,
                "level_suitable": True, "in_language": True, "issues": [], "revision_instructions": []} for unit in units]))
        with api(integration, lesson) as client:
            revision = client.post("/v1/audio:regenerate", json={"lesson_job_id": str(root["job_id"]),
                "chunk_id": result.variants["intermediate"].chunks[0].chunk_id,
                "teacher_review": "Simplify the wording.", "apply_to_all_levels": True})
            assert revision.status_code == 202, revision.text
        updated = process(lesson)
        assert updated["status"] == "DONE", updated["error_code"]
        current = service.result(jobs.get(root["job_id"]))
        assert all(variant.chunks[0].text == "A clearer CSV explanation. [CSV rows 2-3]"
                   and variant.chunks[0].sources[0].pages == [] for variant in current.variants.values())
        with upload_api(integration, lesson) as client:
            assert client.delete(f"/v1/lesson-jobs/{root['job_id']}", params={"learner_level": "intermediate"}).status_code == 204
            assert integration[3].document_count(doc["document_id"], "course-1") == 1
        return
    with api(integration, lesson) as client:
        revised = client.post("/v1/audio:regenerate", json={"lesson_job_id": str(root["job_id"]),
            "chunk_id": result.chunks[0].chunk_id, "teacher_review": "Simplify the wording."})
        assert revised.status_code == 202, revised.text
    updated = process(lesson)
    assert updated["status"] == "DONE", updated["error_code"]
    current = service.result(jobs.get(root["job_id"]))
    assert current.chunks[0].text == "A clearer CSV explanation. [CSV rows 2-3]"
    assert current.chunks[0].sources[0].pages == []


def test_pdf_old_bytes_fingerprint_still_retries(integration, lesson):
    import hashlib
    import io
    import json
    from uuid import uuid4

    raw = pdf_bytes()
    meta = metadata(raw)
    old = meta.model_dump(exclude={"source_filename", "source_format"})
    fingerprint = "bytes-v1:" + hashlib.sha256(json.dumps(
        {"metadata": old, "source_hash": hashlib.sha256(raw).hexdigest()}, sort_keys=True).encode()).hexdigest()
    jobs = integration[1]
    row, _ = jobs.accept(str(uuid4()), str(uuid4()), lesson[4].client_id,
        hashlib.sha256(b"old-pdf").hexdigest(), fingerprint, old, "old-pipeline")
    accepted = SubmissionService(integration[0], jobs, integration[4].staging).submit(
        meta, io.BytesIO(raw), lesson[4], "old-pdf")
    assert accepted["job_id"] == str(row["job_id"])

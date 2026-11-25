"""A6 cancellation/receipt with real isolated PostgreSQL, Qdrant and artifacts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from multiprocessing import get_context
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from qdrant_client.http.exceptions import ResponseHandlingException
from test_a1 import pdf_bytes
from test_a2_integration import events, submit

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.schemas.documents import IngestionMetadata
from upraise_ai.storage.files import StagingStore
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.jobs import JobService
from upraise_ai.workflows.submission import SubmissionService

pytest_plugins = ["test_a1_integration"]


@contextmanager
def api(integration):
    settings, jobs, audit, vectors, worker, original = integration
    principal = Principal(original.client_id, ("course-1",))
    app.dependency_overrides[ingress] = lambda: principal
    try:
        with TestClient(app) as client:
            app.state.submission = SubmissionService(settings, jobs, worker.staging)
            app.state.documents = DocumentService(jobs, vectors, audit)
            app.state.jobs = worker.job_control
            yield client, principal
    finally:
        app.dependency_overrides.clear()


def output(worker, job_id):
    folder = worker.staging.job_dir(job_id)
    (folder / "prepared.json").write_text("temporary output")
    (folder / "prepared.partial.part").write_text("partial output")
    crops = folder / "attempts" / "synthetic"
    crops.mkdir(parents=True)
    (crops / "crop.png").write_bytes(b"crop")


def assert_purged(worker, job_id, source):
    assert worker.staging.source_path(job_id).read_bytes() == source
    assert {p.name for p in worker.staging.job_dir(job_id).iterdir()} == {"source.pdf"}


def test_queued_cancel_purges_inactive_vectors_and_preserves_source_history(integration):
    settings, jobs, audit, vectors, worker, original = integration
    raw = pdf_bytes()
    target = submit(integration, key="cancelled-key", raw=raw)
    row = jobs.get(target["job_id"])
    processed, embeddings = worker.processor.prepare(
        worker.staging.source_path(target["job_id"]),
        IngestionMetadata.model_validate(row["metadata"]), target["document_id"], worker.staging.job_dir(target["job_id"]),
        str(uuid4()), lambda *args: None,
    )
    vectors.write(processed, embeddings, str(uuid4()))
    output(worker, target["job_id"])
    with api(integration) as (client, _):
        path = "/v1/jobs/" + target["job_id"]
        response = client.delete(path, params={"reason": "CANCEL"})
        assert response.status_code == 204 and response.content == b""
        assert client.delete(path, params={"reason": "CANCEL"}).status_code == 204
        status = client.get(path).json()
        assert status["status"] == "CANCELLED" and status["step"] == "CANCELLED"
        assert not status["cleanup_pending"] and not status["acknowledged"]
        assert client.get("/v1/documents", params={"course_id": "course-1"}).json()[0]["chunk_count"] == 0
    assert jobs.claim(settings.lease_seconds) is None
    assert vectors.document_count(target["document_id"], "course-1") == 0
    assert_purged(worker, target["job_id"], raw)
    assert events(integration, target["job_id"], "JOB_CANCELLED") == 1
    with pytest.raises(IngestionError, match="JOB_CANCELLED"):
        submit(integration, key="cancelled-key", raw=raw)


def test_cancel_after_claim_fences_all_worker_mutations(integration):
    settings, jobs, audit, vectors, worker, _ = integration
    raw = pdf_bytes()
    target = submit(integration, raw=raw)
    claim = jobs.claim(settings.lease_seconds)
    with api(integration) as (client, _):
        assert client.delete("/v1/jobs/" + target["job_id"], params={"reason": "CANCEL"}).status_code == 204
    assert not jobs.renew(claim["job_id"], claim["lease_token"], settings.lease_seconds)
    with pytest.raises(LeaseLost):
        jobs.progress(claim["job_id"], claim["lease_token"], "store", 0.9)
    with pytest.raises(LeaseLost):
        with jobs.owned_transaction(claim["job_id"], claim["lease_token"]):
            pytest.fail("Cancelled worker acquired publication ownership")
    worker.process(claim)
    assert jobs.claim(settings.lease_seconds) is None
    assert vectors.document_count(target["document_id"], "course-1") == 0
    assert_purged(worker, target["job_id"], raw)


def test_cancel_running_preparation_waits_for_writer_then_worker_recovers(integration, monkeypatch):
    settings, jobs, audit, vectors, worker, _ = integration
    raw = pdf_bytes()
    target = submit(integration, raw=raw)
    entered, resume = Event(), Event()
    original = worker.processor.prepare

    def paused(*args):
        output(worker, target["job_id"])
        entered.set()
        assert resume.wait(15)
        # Simulate a parser writing before reaching its next ownership check.
        (worker.staging.job_dir(target["job_id"]) / "late-output.json").write_text("late")
        return original(*args)

    monkeypatch.setattr(worker.processor, "prepare", paused)
    claim = jobs.claim(settings.lease_seconds)
    with api(integration) as (client, _), ThreadPoolExecutor(1) as executor:
        future = executor.submit(worker.process, claim)
        try:
            assert entered.wait(10)
            path = "/v1/jobs/" + target["job_id"]
            response = client.delete(path, params={"reason": "CANCEL"})
            assert response.status_code == 503 and response.json()["detail"] == "JOB_CLEANUP_PENDING"
            assert response.headers["Retry-After"] == "2"
            status = client.get(path).json()
            assert status["status"] == "CANCELLED" and status["cleanup_pending"]
            assert events(integration, target["job_id"], "JOB_CANCELLED") == 0
        finally:
            resume.set()
        future.result(timeout=15)
        assert client.delete(path, params={"reason": "CANCEL"}).status_code == 204
        assert not client.get(path).json()["cleanup_pending"]
    assert_purged(worker, target["job_id"], raw)
    assert vectors.document_count(target["document_id"], "course-1") == 0
    assert events(integration, target["job_id"], "JOB_CANCELLED") == 1


@pytest.mark.parametrize("status", ["DONE", "FAILED"])
def test_received_keeps_terminal_result_vectors_and_source(integration, status):
    settings, jobs, audit, vectors, worker, _ = integration
    raw = pdf_bytes()
    target = submit(integration, raw=raw)
    if status == "DONE":
        worker.process(jobs.claim(settings.lease_seconds))
    else:
        with jobs.connect() as conn:
            conn.execute("UPDATE ingestion_jobs SET status='FAILED', error_code='PROCESSING_FAILED' WHERE job_id=%s",
                         (target["job_id"],))
    before = jobs.get(target["job_id"])
    processed, embeddings = worker.processor.prepare(
        worker.staging.source_path(target["job_id"]), IngestionMetadata.model_validate(before["metadata"]),
        target["document_id"], worker.staging.job_dir(target["job_id"]), str(uuid4()), lambda *args: None,
    )
    vectors.write(processed, embeddings, str(uuid4()))
    assert vectors.document_count(target["document_id"], "course-1") == (6 if status == "DONE" else 3)
    output(worker, target["job_id"])
    with api(integration) as (client, _):
        path = "/v1/jobs/" + target["job_id"]
        response = client.delete(path, params={"reason": "RECEIVED"})
        assert response.status_code == 204 and response.content == b""
        assert client.delete(path, params={"reason": "RECEIVED"}).status_code == 204
        body = client.get(path).json()
        assert body["status"] == status and body["acknowledged"] and not body["cleanup_pending"]
        assert body["result"] == before["result"]
        assert body["error"] == ({"code": before["error_code"]} if before["error_code"] else None)
        assert client.get("/v1/documents", params={"course_id": "course-1"}).json()[0]["chunk_count"] == (
            3 if status == "DONE" else 0
        )
    assert jobs.get(target["job_id"])["active_attempt"] == before["active_attempt"]
    assert vectors.document_count(target["document_id"], "course-1") == (3 if status == "DONE" else 0)
    assert_purged(worker, target["job_id"], raw)
    assert events(integration, target["job_id"], "JOB_RECEIVED") == 1


@pytest.mark.parametrize("reason", ["CANCEL", "RECEIVED"])
@pytest.mark.parametrize("failure", ["files", "audit", "commit"])
def test_interrupted_cleanup_recovers_without_duplicate_audit(integration, monkeypatch, reason, failure):
    settings, jobs, audit, vectors, worker, _ = integration
    raw = pdf_bytes()
    target = submit(integration, raw=raw)
    if reason == "RECEIVED":
        worker.process(jobs.claim(settings.lease_seconds))
    output(worker, target["job_id"])
    with api(integration) as (client, _):
        with monkeypatch.context() as patch:
            if failure == "files":
                def unavailable(*args):
                    raise OSError("private filesystem details")
                patch.setattr(worker.staging, "purge_output", unavailable)
            else:
                def unavailable(*args):
                    raise psycopg.OperationalError("private database details")
                patch.setattr(audit if failure == "audit" else jobs,
                              "record" if failure == "audit" else "finish_cleanup", unavailable)
            response = client.delete("/v1/jobs/" + target["job_id"], params={"reason": reason})
            assert response.status_code == 503 and "private" not in response.text
        assert jobs.get(target["job_id"])["cleanup_completed_at"] is None
        # Fresh orchestrator after a restart resumes the committed request.
        JobService(jobs, vectors, audit, StagingStore(settings.data_dir)).recover_cleanup()
        assert client.delete("/v1/jobs/" + target["job_id"], params={"reason": reason}).status_code == 204
    assert_purged(worker, target["job_id"], raw)
    assert jobs.get(target["job_id"])["cleanup_completed_at"] is not None
    assert events(integration, target["job_id"], "JOB_CANCELLED" if reason == "CANCEL" else "JOB_RECEIVED") == 1


def test_cancel_vector_failure_is_fenced_and_recovered(integration, monkeypatch):
    settings, jobs, audit, vectors, worker, _ = integration
    target = submit(integration)
    with api(integration) as (client, _):
        with monkeypatch.context() as patch:
            def unavailable(*args):
                raise ResponseHandlingException(RuntimeError("private vector details"))
            patch.setattr(vectors, "remove_document", unavailable)
            response = client.delete("/v1/jobs/" + target["job_id"], params={"reason": "CANCEL"})
            assert response.status_code == 503 and "private" not in response.text
        assert jobs.get(target["job_id"])["status"] == "CANCELLED"
        assert jobs.claim(settings.lease_seconds) is None
        worker.job_control.recover_cleanup()
        assert client.delete("/v1/jobs/" + target["job_id"], params={"reason": "CANCEL"}).status_code == 204


def test_publication_winning_race_rejects_cancel_and_received_preserves_vectors(integration, monkeypatch):
    settings, jobs, audit, vectors, worker, original = integration
    raw = pdf_bytes()
    target = submit(integration, raw=raw)
    entered, resume = Event(), Event()
    write = vectors.write

    def paused(*args):
        entered.set()
        assert resume.wait(15)
        return write(*args)

    monkeypatch.setattr(vectors, "write", paused)
    with ThreadPoolExecutor(2) as executor:
        processing = executor.submit(worker.process, jobs.claim(settings.lease_seconds))
        try:
            assert entered.wait(10)
            cancellation = executor.submit(worker.job_control.dispose, target["job_id"], "CANCEL", original)
        finally:
            resume.set()
        processing.result(timeout=15)
        with pytest.raises(IngestionError, match="JOB_ACTION_NOT_ALLOWED"):
            cancellation.result(timeout=15)
    assert jobs.get(target["job_id"])["status"] == "DONE"
    worker.job_control.dispose(target["job_id"], "RECEIVED", original)
    assert vectors.document_count(target["document_id"], "course-1") == 3
    assert_purged(worker, target["job_id"], raw)


def test_received_cannot_interrupt_running_job(integration):
    settings, jobs, audit, vectors, worker, original = integration
    target = submit(integration)
    claim = jobs.claim(settings.lease_seconds)
    with api(integration) as (client, _):
        assert client.delete("/v1/jobs/" + target["job_id"], params={"reason": "RECEIVED"}).status_code == 409
    assert jobs.renew(claim["job_id"], claim["lease_token"], settings.lease_seconds)
    assert jobs.get(target["job_id"])["cleanup_requested_at"] is None
    worker.process(claim)
    assert jobs.get(target["job_id"])["status"] == "DONE"


def test_authorization_validation_state_conflicts_and_a2_independence(integration):
    settings, jobs, audit, vectors, worker, _ = integration
    queued = submit(integration)
    done = submit(integration)
    # Complete the second job explicitly, leaving the first queued.
    with jobs.connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET available_at=clock_timestamp()+interval '1 hour' WHERE job_id=%s",
                     (queued["job_id"],))
    worker.process(jobs.claim(settings.lease_seconds))
    foreign = submit(integration, client_id="other-client")
    another_course = submit(integration, course="course-2")
    with api(integration) as (client, _):
        path = "/v1/jobs/" + queued["job_id"]
        assert client.delete(path).status_code == 422
        assert client.delete(path, params={"reason": "invalid"}).status_code == 422
        assert client.delete("/v1/jobs/invalid", params={"reason": "CANCEL"}).status_code == 422
        for job_id in (str(uuid4()), foreign["job_id"], another_course["job_id"]):
            assert client.delete("/v1/jobs/" + job_id, params={"reason": "CANCEL"}).status_code == 404
        assert client.delete(path, params={"reason": "RECEIVED"}).status_code == 409
        assert client.delete("/v1/jobs/" + done["job_id"], params={"reason": "CANCEL"}).status_code == 409
        assert jobs.get(queued["job_id"])["cleanup_requested_at"] is None
        assert client.delete(path, params={"reason": "CANCEL"}).status_code == 204
        assert client.delete(path, params={"reason": "RECEIVED"}).status_code == 409
        assert client.delete("/v1/jobs/" + done["job_id"], params={"reason": "RECEIVED"}).status_code == 204
        assert client.delete("/v1/documents/" + done["document_id"]).status_code == 204
        assert client.delete("/v1/jobs/" + done["job_id"], params={"reason": "RECEIVED"}).status_code == 404


def test_concurrent_cancel_audits_once_other_job_untouched(integration):
    settings, jobs, audit, vectors, worker, original = integration
    target, other = submit(integration), submit(integration)
    principal = Principal(original.client_id, original.courses)

    def cancel(_):
        try:
            worker.job_control.dispose(target["job_id"], "CANCEL", principal)
        except IngestionError as error:
            assert error.code == "JOB_CLEANUP_PENDING"

    with ThreadPoolExecutor(2) as executor:
        list(executor.map(cancel, range(2)))
    worker.job_control.dispose(target["job_id"], "CANCEL", principal)
    assert events(integration, target["job_id"], "JOB_CANCELLED") == 1
    assert jobs.get(other["job_id"])["status"] == "QUEUED"
    assert worker.staging.source_path(other["job_id"]).exists()


def test_migration_upgrades_previous_constraint_without_losing_job(integration):
    settings, jobs, audit, vectors, worker, original = integration
    target = submit(integration)
    with jobs.connect() as conn:
        conn.execute("ALTER TABLE ingestion_jobs DROP CONSTRAINT ingestion_jobs_status_check")
        conn.execute("ALTER TABLE ingestion_jobs ADD CONSTRAINT ingestion_jobs_status_check "
                     "CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED'))")
        for column in ("cleanup_reason", "cleanup_requested_at", "cleanup_completed_at"):
            conn.execute("ALTER TABLE ingestion_jobs DROP COLUMN " + column)
    jobs.migrate()
    jobs.migrate()
    worker.job_control.dispose(target["job_id"], "CANCEL", original)
    assert jobs.get(target["job_id"])["status"] == "CANCELLED"
    assert worker.staging.source_path(target["job_id"]).exists()


def test_a6_uses_production_ingress(integration):
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        assert client.delete("/v1/jobs/" + str(uuid4()), params={"reason": "CANCEL"}).status_code == 503


def hold_artifact_lock(root, job_id, entered, release):
    staging = StagingStore(root)
    with staging.artifact_lock(job_id):
        entered.set()
        release.wait(20)


def test_artifact_lock_blocks_other_process_and_releases_after_crash(tmp_path):
    staging = StagingStore(tmp_path)
    context = get_context("spawn")
    entered, release = context.Event(), context.Event()
    job_id = str(uuid4())
    child = context.Process(target=hold_artifact_lock, args=(tmp_path, job_id, entered, release))
    child.start()
    try:
        assert entered.wait(10)
        with pytest.raises(IngestionError, match="JOB_CLEANUP_PENDING"):
            with staging.artifact_lock(job_id):
                pytest.fail("Concurrent process acquired the worker's artifact lock")
        child.terminate()
        child.join(timeout=10)
        assert not child.is_alive()
        with staging.artifact_lock(job_id):
            pass
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=10)

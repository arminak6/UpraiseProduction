"""A1/A2 lifecycle with real, isolated PostgreSQL and Qdrant storage."""

import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from qdrant_client.http.exceptions import ResponseHandlingException
from test_a1 import metadata, pdf_bytes

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.storage.files import StagingStore
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.submission import SubmissionService

pytest_plugins = ["test_a1_integration"]


@contextmanager
def api(integration):
    settings, jobs, audit, vectors, worker, original = integration
    principal = Principal(original.client_id, ("course-1", "course-2"),
                          frozenset({"documents:read", "documents:write", "jobs:read"}))
    app.dependency_overrides[ingress] = lambda: principal
    try:
        with TestClient(app) as client:
            app.state.submission = SubmissionService(settings, jobs, StagingStore(settings.data_dir))
            app.state.documents = DocumentService(jobs, vectors, audit)
            yield client, principal
    finally:
        app.dependency_overrides.clear()


def submit(integration, *, course="course-1", client_id=None, key=None, version="1", raw=None):
    settings, jobs, audit, vectors, worker, original = integration
    raw = raw or pdf_bytes()
    principal = Principal(client_id or original.client_id, (course,), original.scopes)
    service = SubmissionService(settings, jobs, StagingStore(settings.data_dir))
    return service.submit(metadata(raw).model_copy(update={"course_id": course, "version": version}),
                          io.BytesIO(raw), principal, key)


def events(integration, job_id, event_type):
    settings = integration[0]
    with psycopg.connect(settings.audit_dsn.get_secret_value()) as conn:
        return conn.execute(
            "SELECT count(*) FROM ingestion_audit WHERE job_id=%s AND event_type=%s",
            (job_id, event_type),
        ).fetchone()[0]


def test_list_status_version_scope_and_empty_course(integration):
    settings, jobs, audit, vectors, worker, original = integration
    done = submit(integration, version="revision-2")
    worker.process(jobs.claim(settings.lease_seconds))
    queued = submit(integration, version=None)
    failed = submit(integration)
    with jobs.connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET status='FAILED' WHERE job_id=%s", (failed["job_id"],))
    submit(integration, course="course-2")
    submit(integration, client_id="another-client")
    with api(integration) as (client, principal):
        response = client.get("/v1/documents", params={"course_id": "course-1"})
        assert response.status_code == 200, response.text
        rows = {row["document_id"]: row for row in response.json()}
        assert set(rows) == {done["document_id"], queued["document_id"], failed["document_id"]}
        assert rows[done["document_id"]]["status"] == "DONE"
        assert rows[done["document_id"]]["version"] == "revision-2"
        assert rows[done["document_id"]]["chunk_count"] == 3
        assert rows[done["document_id"]]["progress"] == 1
        assert rows[queued["document_id"]]["status"] == "QUEUED"
        assert rows[queued["document_id"]]["version"] is None
        assert rows[queued["document_id"]]["chunk_count"] == 0
        assert rows[failed["document_id"]]["status"] == "FAILED"
        assert "job_id" not in rows[done["document_id"]]
        all_courses = client.get("/v1/documents")
        assert all_courses.status_code == 200
        assert {row["course_id"] for row in all_courses.json()} == {"course-1", "course-2"}
        assert len(all_courses.json()) == 4
        assert client.get("/v1/documents", params={"course_id": ""}).status_code == 422
        assert client.get("/v1/documents", params={"course_id": "course-3"}).status_code == 403
        app.dependency_overrides[ingress] = lambda: Principal(principal.client_id, ("empty",), principal.scopes)
        assert client.get("/v1/documents", params={"course_id": "empty"}).json() == []
        assert client.get("/v1/documents").json() == []
    assert events(integration, done["job_id"], "DOCUMENT_DELETED") == 0


def test_delete_all_attempts_204_audit_and_idempotent_retry(integration):
    settings, jobs, audit, vectors, worker, original = integration
    raw = pdf_bytes()
    target = submit(integration, key="deleted-key", raw=raw)
    worker.process(jobs.claim(settings.lease_seconds))
    untouched = submit(integration, course="course-2")
    worker.process(jobs.claim(settings.lease_seconds))
    row = jobs.get(target["job_id"])
    # An inactive attempt must also disappear, not just the activated chunks.
    processed, embeddings = worker.processor.prepare(
        worker.staging.source_path(target["job_id"]), metadata(pdf_bytes()), target["document_id"],
        worker.staging.job_dir(target["job_id"]), str(uuid4()), lambda *args: None,
    )
    vectors.write(processed, embeddings, str(uuid4()))
    assert vectors.document_count(target["document_id"], "course-1") == 6
    with api(integration) as (client, principal):
        path = "/v1/documents/" + target["document_id"]
        response = client.delete(path)
        assert response.status_code == 204 and response.content == b""
        assert client.delete(path).status_code == 204
        assert client.get("/v1/documents", params={"course_id": "course-1"}).json() == []
        assert client.get("/v1/jobs/" + target["job_id"]).status_code == 404
        assert client.delete("/v1/documents/" + str(uuid4())).status_code == 404
        assert client.delete("/v1/documents/not-a-uuid").status_code == 422
    assert vectors.document_count(target["document_id"], "course-1") == 0
    assert vectors.document_count(untouched["document_id"], "course-2") == 3
    assert jobs.get(target["job_id"])["deleted_at"] is not None
    assert events(integration, target["job_id"], "DOCUMENT_DELETED") == 1
    with pytest.raises(IngestionError, match="DOCUMENT_DELETED"):
        submit(integration, key="deleted-key", raw=raw)
    assert jobs.get(target["job_id"])["status"] == row["status"]


def test_all_courses_list_keeps_client_course_isolation_and_progress(integration):
    settings, jobs, audit, vectors, worker, original = integration
    first = submit(integration)
    worker.process(jobs.claim(settings.lease_seconds))
    second = submit(integration, course="course-2")
    worker.process(jobs.claim(settings.lease_seconds))
    hidden = submit(integration, course="hidden-course")
    foreign = submit(integration, client_id="another-client")
    running = submit(integration)
    with jobs.connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET status='RUNNING',progress=0.123456 WHERE job_id=%s",
                     (running["job_id"],))
    with api(integration) as (client, principal):
        response = client.get("/v1/documents")
        assert response.status_code == 200, response.text
        rows = {row["document_id"]: row for row in response.json()}
        assert set(rows) == {first["document_id"], second["document_id"], running["document_id"]}
        assert hidden["document_id"] not in rows and foreign["document_id"] not in rows
        assert rows[first["document_id"]]["chunk_count"] == rows[second["document_id"]]["chunk_count"] == 3
        assert rows[running["document_id"]]["progress"] == 0.123456
        app.dependency_overrides[ingress] = lambda: Principal(principal.client_id, (), principal.scopes, local_test=True)
        local = client.get("/v1/documents").json()
        assert {row["document_id"] for row in local} == {*rows, hidden["document_id"]}


def test_delete_permissions_do_not_touch_other_clients_or_courses(integration):
    target = submit(integration)
    with api(integration) as (client, principal):
        path = "/v1/documents/" + target["document_id"]
        for denied in (Principal("other", principal.courses, principal.scopes),
                       Principal(principal.client_id, ("course-2",), principal.scopes)):
            app.dependency_overrides[ingress] = lambda denied=denied: denied
            assert client.delete(path).status_code == 404
        app.dependency_overrides[ingress] = lambda: Principal(principal.client_id, principal.courses, frozenset())
        assert client.delete(path).status_code == 403
        assert client.get("/v1/documents", params={"course_id": "course-1"}).status_code == 403
        assert client.get("/v1/documents").status_code == 403
    assert integration[1].get(target["job_id"])["deletion_requested_at"] is None


def test_queued_delete_fences_claims_and_stale_lease(integration):
    settings, jobs, audit, vectors, worker, principal = integration
    queued = submit(integration)
    with api(integration) as (client, _):
        assert client.delete("/v1/documents/" + queued["document_id"]).status_code == 204
    assert jobs.claim(settings.lease_seconds) is None
    running = submit(integration)
    claim = jobs.claim(settings.lease_seconds)
    with api(integration) as (client, _):
        assert client.delete("/v1/documents/" + running["document_id"]).status_code == 204
    assert not jobs.renew(claim["job_id"], claim["lease_token"], settings.lease_seconds)
    with pytest.raises(LeaseLost):
        jobs.progress(claim["job_id"], claim["lease_token"], "store", 0.9)
    with pytest.raises(LeaseLost):
        with jobs.owned_transaction(claim["job_id"], claim["lease_token"]):
            pytest.fail("Deleted document could publish")
    assert jobs.claim(settings.lease_seconds) is None


def test_delete_during_actual_worker_preparation_cannot_republish(integration, monkeypatch):
    settings, jobs, audit, vectors, worker, principal = integration
    target = submit(integration)
    claim = jobs.claim(settings.lease_seconds)
    entered, resume = Event(), Event()
    prepare = worker.processor.prepare

    def paused(*args):
        entered.set()
        assert resume.wait(15)
        return prepare(*args)

    monkeypatch.setattr(worker.processor, "prepare", paused)
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(worker.process, claim)
        try:
            assert entered.wait(10)
            with api(integration) as (client, _):
                assert client.delete("/v1/documents/" + target["document_id"]).status_code == 204
        finally:
            resume.set()
        future.result(timeout=15)
    assert jobs.get(target["job_id"])["active_attempt"] is None
    assert vectors.document_count(target["document_id"], "course-1") == 0


@pytest.mark.parametrize("failure", ["vectors", "audit", "commit"])
def test_interrupted_deletion_is_hidden_and_recovered_without_duplicate_audit(integration, monkeypatch, failure):
    settings, jobs, audit, vectors, worker, principal = integration
    target = submit(integration)
    worker.process(jobs.claim(settings.lease_seconds))
    with api(integration) as (client, _):
        with monkeypatch.context() as patch:
            if failure == "vectors":
                def unavailable(*args):
                    raise ResponseHandlingException(RuntimeError("private connection details"))
                patch.setattr(vectors, "remove_document", unavailable)
            elif failure == "audit":
                def unavailable(*args):
                    raise psycopg.OperationalError("private database details")
                patch.setattr(audit, "record", unavailable)
            else:
                def unavailable(*args):
                    raise psycopg.OperationalError("uncertain deletion commit")
                patch.setattr(jobs, "finish_deletion", unavailable)
            response = client.delete("/v1/documents/" + target["document_id"])
            assert response.status_code == 503
            assert "private" not in response.text
        assert client.get("/v1/documents", params={"course_id": "course-1"}).json() == []
        assert client.get("/v1/jobs/" + target["job_id"]).status_code == 404
        assert jobs.get(target["job_id"])["deleted_at"] is None
        # The worker resumes persisted deletion without a client retry.
        worker.documents.recover_deletion()
        assert client.delete("/v1/documents/" + target["document_id"]).status_code == 204
    assert vectors.document_count(target["document_id"], "course-1") == 0
    assert jobs.get(target["job_id"])["deleted_at"] is not None
    assert events(integration, target["job_id"], "DOCUMENT_DELETED") == 1


def test_concurrent_delete_audits_once_and_other_document_survives(integration):
    settings, jobs, audit, vectors, worker, principal = integration
    target, other = submit(integration), submit(integration)
    worker.process(jobs.claim(settings.lease_seconds))
    worker.process(jobs.claim(settings.lease_seconds))
    service = DocumentService(jobs, vectors, audit)
    with ThreadPoolExecutor(2) as executor:
        list(executor.map(lambda _: service.delete(target["document_id"], principal), range(2)))
    assert vectors.document_count(target["document_id"], "course-1") == 0
    assert vectors.document_count(other["document_id"], "course-1") == 3
    assert events(integration, target["job_id"], "DOCUMENT_DELETED") == 1


def test_a2_uses_default_ingress(integration):
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        assert client.get("/v1/documents", params={"course_id": "course-1"}).status_code == 503
        assert client.delete("/v1/documents/" + str(uuid4())).status_code == 503


def test_existing_retention_can_clean_deleted_nonterminal_artifacts(integration):
    settings, jobs, audit, vectors, worker, principal = integration
    deleted = submit(integration)
    retained = submit(integration)
    DocumentService(jobs, vectors, audit).delete(deleted["document_id"], principal)
    with jobs.connect() as conn:
        conn.execute(
            "UPDATE ingestion_jobs SET deleted_at=clock_timestamp()-interval '2 days' WHERE job_id=%s",
            (deleted["job_id"],),
        )
    assert {str(row["job_id"]) for row in jobs.completed_before(1)} == {deleted["job_id"]}
    assert jobs.get(retained["job_id"])["deletion_requested_at"] is None

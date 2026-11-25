"""Course/document cascades on disposable PG/Qdrant; invented content only."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from qdrant_client.http.exceptions import ResponseHandlingException
from test_a2_integration import submit
from test_a3 import request_data
from test_a3_v2_integration import accept_bundle
from test_b1 import runtime

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.storage.sessions import SessionStore
from upraise_ai.workflows.documents import DocumentService

pytest_plugins = ["test_a3_integration"]


def source(integration, *, course="course-1", client_id=None):
    data = submit(integration, course=course, client_id=client_id)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    return data


def seed(lesson, documents, *, course="course-1", client_id=None, language="en", revision=False, status="DONE"):
    settings, jobs, worker, service, principal, models = lesson
    metadata = request_data(document_ids=[d["document_id"] for d in documents], course_id=course, language=language)
    if revision:
        metadata["revision_request"] = {"teacher_review": "Private teacher text", "aligned_unit": {"material_text": "Canonical text"}}
    row = jobs.accept(client_id or principal.client_id, None, str(uuid4()), metadata, settings.lesson_signature)
    job_id = str(row["job_id"])
    folder = service.staging.job_dir(job_id)
    folder.mkdir()
    (folder / "result.json").write_text("temporary lesson text")
    (folder / "voice.wav").write_bytes(b"temporary voice")
    with jobs.connect() as conn:
        conn.execute("UPDATE lesson_jobs SET status=%s,lease_token=%s,lease_until=clock_timestamp()+interval '1 hour' WHERE job_id=%s",
                     (status, uuid4(), job_id))
        for level in ("beginner", "intermediate", "advanced"):
            conn.execute("""INSERT INTO prepared_lesson_chunks(job_id,chunk_id,chunk_index,section_index,text,sources,
                reviewer_report,revisions,versions,ssml,ssml_parts,level_key)
                VALUES(%s,%s,0,0,'Saved generated text',%s,'{}',0,'{}','<speak>Saved text</speak>','[]',%s)""",
                (job_id, uuid4(), Jsonb([{"document_id": d["document_id"]} for d in documents]), level))
        conn.execute("INSERT INTO lesson_chunk_plans(job_id,plan) VALUES(%s,%s)", (job_id, Jsonb({"material_text": "Saved canonical material"})))
    return jobs.get(job_id)


def documents(integration, lesson, sessions=None):
    return DocumentService(integration[1], integration[3], integration[2], lessons=lesson[3], sessions=sessions)


@contextmanager
def api(integration, lesson, sessions=None):
    principal = Principal(lesson[4].client_id, ("course-1", "course-2"))
    app.dependency_overrides[ingress] = lambda: principal
    try:
        with TestClient(app) as client:
            app.state.documents = documents(integration, lesson, sessions)
            app.state.lessons = lesson[3]
            yield client, principal
    finally:
        app.dependency_overrides.clear()


def erased(lesson, row):
    job_id = str(row["job_id"])
    assert lesson[1].prepared_chunks(job_id) == []
    assert lesson[1].get_plan(job_id) is None
    current = lesson[1].get(job_id)
    assert current["content_deleted_at"] and current["cleanup_completed_at"]
    assert current["result"] is None and "revision_request" not in current["metadata"]
    assert not lesson[3].staging.job_dir(job_id).exists()
    with pytest.raises(LeaseLost):
        with lesson[1].owned_transaction(job_id, row["lease_token"]):
            pytest.fail("Deleted job could save content again")


def test_course_deletion_all_levels_languages_revisions_expired_running_and_owner_scope(integration, lesson):
    first, second = source(integration), source(integration)
    other = source(integration, course="course-2")
    foreign = source(integration, client_id="foreign-client")
    targets = [seed(lesson, [first], language="en"), seed(lesson, [second], language="it", revision=True),
               seed(lesson, [first, second], status="RUNNING"), seed(lesson, [second], status="QUEUED")]
    protected = [seed(lesson, [other], course="course-2"), seed(lesson, [foreign], client_id="foreign-client")]
    with lesson[1].connect() as conn:
        conn.execute("""UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 day',cleanup_reason='TTL',
            cleanup_requested_at=clock_timestamp(),cleanup_completed_at=clock_timestamp() WHERE job_id=%s""",
            (targets[0]["job_id"],))
    with api(integration, lesson) as (client, principal):
        assert client.delete("/v1/courses/course-1").status_code == 204
        assert client.delete("/v1/courses/course-1").status_code == 204
        for row in targets:
            erased(lesson, row)
            assert client.get(f"/v1/jobs/{row['job_id']}").status_code == 404
        assert client.get("/v1/documents", params={"course_id": "course-1"}).json() == []
    for row in protected:
        assert len(lesson[1].prepared_chunks(row["job_id"])) == 3
        assert lesson[3].staging.job_dir(str(row["job_id"])).exists()
    for doc in (first, second):
        assert integration[3].document_count(doc["document_id"], "course-1") == 0
    assert integration[3].document_count(foreign["document_id"], "course-1") == 3
    assert integration[3].document_count(other["document_id"], "course-2") == 3
    with psycopg.connect(integration[0].audit_dsn.get_secret_value()) as conn:
        for row in targets:
            assert conn.execute("SELECT count(*) FROM lesson_audit WHERE job_id=%s AND event_type='CONTENT_DELETED'",
                                (row["job_id"],)).fetchone()[0] == 1


def test_document_deletion_cascades_complete_dependent_generations_only(integration, lesson):
    first, second = source(integration), source(integration)
    targets = [seed(lesson, [first]), seed(lesson, [first, second], language="it", revision=True)]
    retained = seed(lesson, [second])
    with api(integration, lesson) as (client, _):
        path = "/v1/documents/" + first["document_id"]
        assert client.delete(path).status_code == 204
        assert client.delete(path).status_code == 204
    for row in targets:
        erased(lesson, row)
    assert len(lesson[1].prepared_chunks(retained["job_id"])) == 3
    assert integration[3].document_count(second["document_id"], "course-1") == 3


def test_indefinite_qdrant_text_retention_survives_ttl_then_document_delete_erases_bundle(integration, lesson, monkeypatch):
    row, data, adapted = accept_bundle(integration, lesson, monkeypatch)
    document_id = data.document_ids[0]
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '10 years' WHERE job_id=%s", (row["job_id"],))
    for expired in lesson[1].maintenance():
        lesson[3].complete_cleanup(str(expired["job_id"]))
    assert len(lesson[1].prepared_chunks(row["job_id"])) > 0
    assert lesson[1].get_plan(row["job_id"])
    assert integration[3].document_count(str(document_id), "course-1") == 3
    with api(integration, lesson) as (client, _):
        assert client.delete("/v1/documents/" + str(document_id)).status_code == 204
    erased(lesson, row)


@pytest.mark.parametrize("scope", ["document", "course"])
def test_busy_writer_fenced_text_erased_then_retry_completes(integration, lesson, scope):
    doc = source(integration)
    row = seed(lesson, [doc], status="RUNNING")
    with api(integration, lesson) as (client, _):
        path = "/v1/courses/course-1" if scope == "course" else "/v1/documents/" + doc["document_id"]
        with lesson[3].staging.artifact_lock(str(row["job_id"])):
            response = client.delete(path)
            assert response.status_code == 503 and response.json()["detail"] == "JOB_CLEANUP_PENDING"
            assert lesson[1].prepared_chunks(row["job_id"]) == []
            assert lesson[1].get(row["job_id"])["cleanup_requested_at"]
            assert not lesson[1].renew(row["job_id"], row["lease_token"], 120)
        documents(integration, lesson).recover_deletion()
        assert client.delete(path).status_code == 204
    erased(lesson, row)


def test_qdrant_outage_blocks_new_ingestion_and_recovery_allows_fresh_course(integration, lesson, monkeypatch):
    doc = source(integration)
    row = seed(lesson, [doc])
    control = documents(integration, lesson)
    principal = lesson[4]
    with monkeypatch.context() as patch:
        patch.setattr(integration[3], "remove_document", lambda *args: (_ for _ in ()).throw(ResponseHandlingException(RuntimeError("private"))))
        with pytest.raises(IngestionError, match="VECTOR_STORAGE_UNAVAILABLE"):
            control.delete_course("course-1", principal)
    with pytest.raises(IngestionError, match="COURSE_DELETION_PENDING"):
        submit(integration)
    assert lesson[1].prepared_chunks(row["job_id"]) == []
    control.recover_deletion()
    erased(lesson, row)
    fresh = source(integration)
    new = seed(lesson, [fresh])
    # Stale recovery for the completed operation must not touch the new course.
    with control.courses.connect() as conn:
        old = control.courses.get(conn, principal.client_id, "course-1")
    control.complete_course(old)
    assert len(lesson[1].prepared_chunks(new["job_id"])) == 3
    assert integration[3].document_count(fresh["document_id"], "course-1") == 3


def test_course_delete_permissions_unknown_and_malformed(integration, lesson):
    doc = source(integration)
    row = seed(lesson, [doc])
    with api(integration, lesson) as (client, principal):
        for denied, code in ((Principal("other", principal.courses, principal.scopes), 404),
                             (Principal(principal.client_id, ("course-2",), principal.scopes), 403)):
            app.dependency_overrides[ingress] = lambda denied=denied: denied
            assert client.delete("/v1/courses/course-1").status_code == code
        app.dependency_overrides[ingress] = lambda: principal
        assert client.delete("/v1/courses/" + "x" * 121).status_code == 422
        assert client.delete("/v1/courses/course-2").status_code == 404
    assert len(lesson[1].prepared_chunks(row["job_id"])) == 3


def test_delete_clears_related_ram_sessions_and_fences_inflight_reply(integration, lesson):
    doc = source(integration)
    row = seed(lesson, [doc])
    store = SessionStore(monotonic=lambda: 0)
    active = runtime(datetime.now(UTC))
    active.binding = replace(active.binding, lesson_job_id=str(row["job_id"]), client_id=lesson[4].client_id,
                             document_ids=(doc["document_id"],))
    active.chat_cache.extend([{"text": "Private question and answer"}])
    store.start(active, lambda _: None)
    unrelated = runtime(datetime.now(UTC), course_id="other")
    store.start(unrelated, lambda _: None)
    documents(integration, lesson, store).delete(doc["document_id"], lesson[4])
    assert not active.chat_cache and not active.chunk_ids and not active.prompt_guard_counters
    with pytest.raises(HTTPException) as denied:
        store.ensure_current(active)
    assert denied.value.status_code == 410
    assert store.get(unrelated.response.session_id, unrelated.binding.client_id) is unrelated


def test_a3_acceptance_race_cannot_create_orphan_generation(integration, lesson, monkeypatch):
    doc = source(integration)
    reached, proceed = Event(), Event()
    original = lesson[1].accept
    def paused(*args, **kwargs):
        reached.set()
        assert proceed.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(lesson[1], "accept", paused)
    data = LessonInput.model_validate(request_data(document_ids=[doc["document_id"]]))
    with ThreadPoolExecutor(1) as executor:
        pending = executor.submit(lesson[3].submit, data, lesson[4])
        assert reached.wait(10)
        documents(integration, lesson).delete(doc["document_id"], lesson[4])
        proceed.set()
        with pytest.raises(IngestionError, match="DOCUMENT_NOT_FOUND"):
            pending.result(timeout=10)
    with lesson[1].connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM lesson_jobs").fetchone()["n"] == 0

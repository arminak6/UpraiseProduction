"""A3 with real isolated PostgreSQL/Qdrant/artifacts and fake model/speech providers."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_a2_integration import submit
from test_a3 import MathModels, Polly, request_data

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.schemas.lessons import LessonInput, ReviewReport, SectionDraft
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.lesson_speech import LessonSpeech
from upraise_ai.storage.lessons import LessonRepository
from upraise_ai.workers.lessons import LessonWorker, read_lesson_state
from upraise_ai.workers.supervision import JobSupervisor, prepare_child
from upraise_ai.workflows.lessons import LessonService

pytest_plugins = ["test_a1_integration"]


class Models(MathModels):
    def __init__(self, reject=False):
        self.reject = reject
        self.drafts = self.reviews = self.revisions = 0

    def draft(self, section, language):
        self.drafts += 1
        return SectionDraft(paragraphs=[{"text": "A supported teaching sentence. " * 25, "pages": section["pages"]}])

    def review(self, section, draft, language, source_path):
        self.reviews += 1
        if self.reject and self.reviews == 1:
            return ReviewReport(approved=False, summary="Needs clearer explanation.",
                                issues=[{"category": "clarity", "message": "Explain the notation."}],
                                revision_instructions=["Explain the notation."])
        return ReviewReport(approved=True, summary="Delivered text checked.")

    def revise(self, section, draft, review, language):
        self.revisions += 1
        return draft

    def generate_section(self, section, language, source_path=None, *, notify=None):
        draft = self.draft(section, language)
        if notify:
            notify("REVIEW")
        report = self.review(section, draft, language, source_path)
        if notify:
            notify("FINALIZE")
        final = self.revise(section, draft, report, language) if not report.approved else draft
        return final, report, int(not report.approved)


@pytest.fixture
def lesson(integration):
    settings, documents, audit, vectors, ingestion, original = integration
    jobs = LessonRepository(settings.jobs_dsn.get_secret_value())
    models = Models()
    speech = LessonSpeech(settings, models, client=Polly(), converter=lambda *a: "x plus one")
    worker = LessonWorker(settings, jobs=jobs, documents=documents, audit=audit, vectors=vectors,
                          models=models, speech=speech)
    principal = Principal(original.client_id, ("course-1",),
                          frozenset({"lessons:write", "jobs:read", "jobs:write", "documents:read", "documents:write"}))
    service = LessonService(settings, jobs, documents, ingestion.staging, audit)
    try:
        yield settings, jobs, worker, service, principal, models
    finally:
        with jobs.connect() as conn:
            ids = [r["job_id"] for r in conn.execute("SELECT job_id FROM lesson_jobs").fetchall()]
        if ids:
            with psycopg.connect(settings.audit_dsn.get_secret_value()) as conn:
                conn.execute("DELETE FROM lesson_audit WHERE job_id=ANY(%s)", (ids,))


@contextmanager
def api(integration, lesson):
    _, _, ingestion, service, principal, _ = lesson
    app.dependency_overrides[ingress] = lambda: principal
    try:
        with TestClient(app) as client:
            app.state.lessons = service
            app.state.submission = type("Submission", (), {"jobs": integration[1]})()
            app.state.jobs = integration[4].job_control
            yield client
    finally:
        app.dependency_overrides.clear()


def accept(integration, lesson, *, ready=True, key=None, **updates):
    doc = submit(integration)
    if ready:
        integration[4].process(integration[1].claim(integration[0].lease_seconds))
    data = LessonInput.model_validate(request_data(document_ids=[doc["document_id"]], **updates))
    result = lesson[3].submit(data, lesson[4], key)
    return result, doc, data


def process(lesson):
    settings, jobs, worker, _, _, _ = lesson
    row = jobs.claim(settings.lease_seconds, settings.lesson_signature)
    worker.process(row)
    return jobs.get(row["job_id"])


def test_full_a3_a4_a5_audio_a6_and_new_generation(integration, lesson):
    target, doc, data = accept(integration, lesson, key="retry-one",
                                options={"target_chunk_words": 30, "source_window_pages": 6, "learner_level": "beginner"})
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    with api(integration, lesson) as client:
        again = client.post("/v1/lesson-jobs", json=data.model_dump(mode="json"), headers={"Idempotency-Key": "retry-one"})
        assert again.status_code == 202 and again.json() == target
        path = again.headers["Location"]
        status = client.get(path).json()
        assert status["lesson_id"] == data.lesson_id and status["status"] == "DONE"
        result = client.get(path+"/result")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["status"] == "DRAFT" and body["label"] == "AI-generated"
        assert body["learner_level"] == "beginner" and not body["learner_level_applied"]
        assert body["expires_at"] and body["versions"]["math_runtime"] == "MathJax-4.1.3-v3"
        assert [c["index"] for c in body["chunks"]] == list(range(len(body["chunks"])))
        assert len({c["chunk_id"] for c in body["chunks"]}) == len(body["chunks"])
        assert body["reviewer_report"]["issues"]  # Unverified extraction remains visible.
        for chunk in body["chunks"]:
            assert chunk["sources"][0]["document_id"] == doc["document_id"]
            audio = client.get(chunk["audio_url"])
            assert audio.status_code == 200 and audio.content.startswith(b"RIFF")
            assert audio.headers["content-type"] == "audio/wav"
            assert chunk["duration_seconds"] > 0 and chunk["visemes"]
        source = integration[4].staging.source_path(doc["job_id"]).read_bytes()
        assert client.delete(path, params={"reason": "RECEIVED"}).status_code == 204
        assert client.delete(path, params={"reason": "RECEIVED"}).status_code == 204
        assert client.get(path+"/result").status_code == 410
        assert client.get(path).json()["acknowledged"]
        assert not integration[4].staging.job_dir(target["job_id"]).exists()
        assert integration[4].staging.source_path(doc["job_id"]).read_bytes() == source
        assert integration[3].document_count(doc["document_id"], "course-1") > 0
        new = client.post("/v1/lesson-jobs", json=data.model_dump(mode="json"), headers={"Idempotency-Key": "new-generation"})
        assert new.status_code == 202 and new.json() != target


def test_rejected_draft_gets_exactly_one_revision(integration, lesson):
    accept(integration, lesson)
    lesson[5].reject = True
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    assert (lesson[5].drafts, lesson[5].reviews, lesson[5].revisions) == (1, 1, 1)
    assert all(c.revisions == 1 for c in lesson[3].result(row).chunks)


def test_waiting_ingestion_does_not_consume_failure_attempts(integration, lesson):
    target, doc, _ = accept(integration, lesson, ready=False)
    row = process(lesson)
    assert row["status"] == "QUEUED" and row["step"] == "WAITING_FOR_INGESTION" and row["attempts"] == 0
    assert lesson[5].drafts == 0
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    assert process(lesson)["status"] == "DONE"


def test_wait_timeout_is_terminal_and_has_ttl(integration, lesson):
    target, _, _ = accept(integration, lesson, ready=False)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET created_at=clock_timestamp()-interval '2 days' WHERE job_id=%s", (target["job_id"],))
    row = process(lesson)
    assert row["status"] == "FAILED" and row["error_code"] == "DOCUMENTS_NOT_READY" and row["expires_at"]


def test_unknown_cross_course_and_cross_client_do_not_leak(integration, lesson):
    with api(integration, lesson) as client:
        assert client.post("/v1/lesson-jobs", json=request_data()).status_code == 404
    target, doc, data = accept(integration, lesson)
    with api(integration, lesson) as client:
        other = Principal("other-client", ("course-1",), lesson[4].scopes)
        app.dependency_overrides[ingress] = lambda: other
        assert client.get("/v1/jobs/"+target["job_id"]).status_code == 404
        assert client.get("/v1/jobs/"+target["job_id"]+"/result").status_code == 404
        assert client.delete("/v1/jobs/"+target["job_id"], params={"reason": "CANCEL"}).status_code == 404
        assert client.post("/v1/lesson-jobs", json=data.model_dump(mode="json")).status_code == 404


def test_idempotency_conflict_cancel_and_fenced_writes(integration, lesson):
    target, _, data = accept(integration, lesson, key="key")
    row = lesson[1].claim(lesson[0].lease_seconds, lesson[0].lesson_signature)
    with api(integration, lesson) as client:
        payload = data.model_dump(mode="json")
        payload["voice"] = "Matthew"
        assert client.post("/v1/lesson-jobs", json=payload, headers={"Idempotency-Key": "key"}).status_code == 409
        path = "/v1/jobs/" + target["job_id"]
        assert client.delete(path, params={"reason": "CANCEL"}).status_code == 204
        assert client.delete(path, params={"reason": "CANCEL"}).status_code == 204
        assert not lesson[1].renew(row["job_id"], row["lease_token"], 120)
        with pytest.raises(LeaseLost):
            lesson[1].progress(row["job_id"], row["lease_token"], "SPEECH", 0.5)
        lesson[2].process(row)
        assert lesson[1].get(row["job_id"])["status"] == "CANCELLED"


def test_running_cancel_waits_for_writer_and_purges_after_it_stops(integration, lesson):
    target, _, _ = accept(integration, lesson)
    entered, resume = Event(), Event()
    original = lesson[5].draft

    def pause(*args):
        entered.set()
        assert resume.wait(15)
        return original(*args)

    lesson[5].draft = pause
    row = lesson[1].claim(lesson[0].lease_seconds, lesson[0].lesson_signature)
    with api(integration, lesson) as client, ThreadPoolExecutor(1) as executor:
        future = executor.submit(lesson[2].process, row)
        assert entered.wait(10)
        path = "/v1/jobs/"+target["job_id"]
        assert client.delete(path, params={"reason": "CANCEL"}).status_code == 503
        resume.set()
        future.result(15)
        assert client.delete(path, params={"reason": "CANCEL"}).status_code == 204
    assert not lesson[2].staging.job_dir(target["job_id"]).exists()


def test_ttl_expiry_revokes_pull_and_cleans_only_lesson_output(integration, lesson):
    target, doc, _ = accept(integration, lesson)
    process(lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (target["job_id"],))
    with api(integration, lesson) as client:
        assert client.get("/v1/jobs/"+target["job_id"]+"/result").status_code == 410
    for row in lesson[1].maintenance():
        lesson[3].complete_cleanup(str(row["job_id"]))
    assert not lesson[2].staging.job_dir(target["job_id"]).exists()
    assert integration[4].staging.source_path(doc["job_id"]).is_file()
    assert integration[3].document_count(doc["document_id"], "course-1") > 0
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        assert conn.execute("SELECT count(*) FROM lesson_audit WHERE job_id=%s AND event_type='OUTPUT_TTL'", (target["job_id"],)).fetchone()[0] == 1


def test_source_deleted_during_generation_cannot_publish(integration, lesson):
    target, doc, _ = accept(integration, lesson)
    original = lesson[5].draft

    def delete(*args):
        with integration[1].document_transaction(doc["document_id"]) as (conn, row):
            integration[1].request_deletion(conn, doc["document_id"])
        return original(*args)

    lesson[5].draft = delete
    row = process(lesson)
    assert row["status"] == "FAILED" and row["error_code"] == "LESSON_DOCUMENT_UNAVAILABLE"


def test_audio_failure_reuses_completed_section_on_retry(integration, lesson):
    target, _, _ = accept(integration, lesson)
    speech = lesson[2].speech
    original = speech.synthesize
    speech.synthesize = lambda *args: (_ for _ in ()).throw(IngestionError("TEMPORARY_SPEECH", retryable=True))
    row = process(lesson)
    assert row["status"] == "QUEUED" and lesson[5].drafts == 1
    speech.synthesize = original
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    assert process(lesson)["status"] == "DONE"
    assert lesson[5].drafts == 1


def test_result_and_audio_corruption_are_not_delivered(integration, lesson):
    target, _, _ = accept(integration, lesson)
    row = process(lesson)
    result = lesson[3].result(row)
    audio = lesson[2].staging.job_dir(target["job_id"]) / f"{result.chunks[0].chunk_id}.wav"
    audio.write_bytes(b"corrupted")
    with pytest.raises(IngestionError, match="AUDIO_INTEGRITY"):
        lesson[3].audio(row, result.chunks[0].chunk_id)
    (audio.parent / "result.json").write_text("{}")
    with pytest.raises(IngestionError, match="RESULT_INTEGRITY"):
        lesson[3].result(row)


def hanging_child(settings, job, events, parent_pid):
    prepare_child(parent_pid)
    events.send(("ready", os.getpid()))
    events.send(("workload", "lesson", 40))  # 200 pages with default five-page windows.
    while True:
        time.sleep(0.1)


def test_stalled_lesson_process_is_terminated(integration, lesson):
    accept(integration, lesson)
    job = lesson[1].claim(lesson[0].lease_seconds, lesson[0].lesson_signature)
    settings = lesson[0].model_copy(update={"job_timeout_seconds": 2, "worker_startup_timeout_seconds": 2,
                                          "worker_supervisor_poll_seconds": 0.05, "worker_terminate_grace_seconds": 0.1})
    supervisor = JobSupervisor(settings, child_target=hanging_child,
                               read_state=lambda job_id, timeout: read_lesson_state(settings.jobs_dsn.get_secret_value(), job_id, timeout))
    result = supervisor.run(job, Event())
    assert result.reason == "PROCESSING_TIMEOUT"
    lesson[2].failure(job, IngestionError(result.reason, retryable=True))
    assert lesson[1].get(job["job_id"])["status"] == "QUEUED"


def test_native_v3_uses_source_text_without_an_extra_pdf_judge(integration, lesson):
    from test_a3_model_compat import Converse, review_data

    target, _, _ = accept(integration, lesson)
    client = Converse(["A supported explanation [p. 1].", json.dumps(review_data())])
    lesson[2].models = LessonModels(lesson[0], client)
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    assert len(client.calls) == 2
    assert [call["modelId"] for call in client.calls] == [
        lesson[0].lesson_generation_model_id, lesson[0].lesson_review_model_id]
    with api(integration, lesson) as http:
        body = http.get(f"/v1/jobs/{target['job_id']}/result").json()
        assert body["versions"]["visual_review_model"] == "not-used-v3"
        assert all(chunk["revisions"] == 0 for chunk in body["chunks"])


def test_callback_state_retries_do_not_rerun_generation_or_delete_output(integration, lesson):
    target, _, _ = accept(integration, lesson)
    process(lesson)
    jobs = lesson[1]
    with jobs.connect() as conn:
        conn.execute("UPDATE lesson_jobs SET callback_status='PENDING' WHERE job_id=%s", (target["job_id"],))
    callback = jobs.claim_callback(2)
    assert callback["callback_attempts"] == 1
    jobs.finish_callback(callback, False, 2)
    assert jobs.get(target["job_id"])["callback_status"] == "PENDING"
    with jobs.connect() as conn:
        conn.execute("UPDATE lesson_jobs SET callback_available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    callback = jobs.claim_callback(2)
    jobs.finish_callback(callback, True, 2)
    row = jobs.get(target["job_id"])
    assert row["callback_status"] == "DELIVERED" and not row["cleanup_requested_at"]
    assert lesson[3].result(row).chunks and lesson[5].drafts == 1


def test_last_callback_attempt_crash_becomes_visible_failure(integration, lesson):
    target, _, _ = accept(integration, lesson)
    process(lesson)
    with lesson[1].connect() as conn:
        conn.execute("""UPDATE lesson_jobs SET callback_status='SENDING',callback_attempts=2,
                     callback_until=clock_timestamp()-interval '1 second' WHERE job_id=%s""", (target["job_id"],))
    lesson[1].maintenance(2)
    assert lesson[1].get(target["job_id"])["callback_status"] == "FAILED"


def test_pre_edit_findings_remain_visible_without_additional_review(integration, lesson):
    accept(integration, lesson)
    def reject(*args):
        lesson[5].reviews += 1
        return ReviewReport(approved=False, summary="Unresolved issue.",
                            issues=[{"category": "accuracy", "message": "Needs human review."}],
                            revision_instructions=["Check source."])
    lesson[5].review = reject
    row = process(lesson)
    assert row["status"] == "DONE" and lesson[5].revisions == 1 and lesson[5].reviews == 1
    result = lesson[3].result(row)
    assert result.status == "DRAFT" and not result.reviewer_report.approved
    assert any(issue.category == "accuracy" for issue in result.reviewer_report.issues)


@pytest.mark.parametrize("module,heartbeat", [("lessons", "/tmp/upraise-lesson-heartbeat"),
                                              ("lesson_delivery", "/tmp/upraise-delivery-heartbeat")])
def test_lesson_services_start_idle_and_stop_cleanly(integration, lesson, module, heartbeat):
    from pathlib import Path

    settings = lesson[0]
    environment = {**os.environ, "UPRAISE_JOBS_DSN": settings.jobs_dsn.get_secret_value(),
                   "UPRAISE_AUDIT_DSN": settings.audit_dsn.get_secret_value(),
                   "UPRAISE_QDRANT_URL": settings.qdrant_url, "UPRAISE_QDRANT_COLLECTION": settings.qdrant_collection,
                   "UPRAISE_DATA_DIR": str(settings.data_dir), "UPRAISE_ENVIRONMENT": "production",
                   "UPRAISE_LOCAL_TEST_MODE": "false"}
    started = time.time()
    child = subprocess.Popen([sys.executable, "-m", "upraise_ai.workers."+module], env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            assert child.poll() is None, "Lesson service exited during startup"
            if Path(heartbeat).exists() and Path(heartbeat).stat().st_mtime >= started:
                break
            time.sleep(0.1)
        else:
            pytest.fail("Lesson service heartbeat did not start")
        child.terminate()
        assert child.wait(timeout=10) == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_v3_prose_and_finalizer_recovery_complete_through_existing_api(integration, lesson):
    from test_a3_model_compat import review_data

    calls = []
    reviews = 0

    class Client:
        def converse(self, **request):
            nonlocal reviews
            calls.append(request)
            system = request["system"][0]["text"]
            if "For a prepared lecture" in system:
                payload = "A supported teaching explanation [p. 1]."
            elif "Uprise Lecture Reviewer" in system:
                reviews += 1
                payload = json.dumps(review_data(**({"approved": False,
                    "clarity_issues": ["Clarify the explanation."],
                    "revision_instructions": ["Clarify using only source facts."]} if reviews == 1 else {})))
            elif "Uprise Final Lecture Editor" in system:
                if "outputConfig" in request:
                    raise RuntimeError("Simulated structured output unsupported")
                payload = '{"lecture_text":"A clearer source-grounded explanation [p. 1]."}'
            else:
                payload = json.dumps({"approved": True, "summary": "PDF checked.",
                                      "issues": [], "revision_instructions": []})
            return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": payload}]}}}

    target, source, _ = accept(integration, lesson)
    lesson[2].models = LessonModels(lesson[0], Client())
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    with api(integration, lesson) as client:
        result = client.get(f"/v1/jobs/{target['job_id']}/result")
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "DRAFT" and body["course_id"] == "course-1"
        assert body["chunks"] and all(chunk["revisions"] == 1 for chunk in body["chunks"])
        assert all(source["document_id"] == provenance["document_id"]
                   for chunk in body["chunks"] for provenance in chunk["sources"])
        audio = client.get(body["chunks"][0]["audio_url"])
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    drafts = [call for call in calls if "For a prepared lecture" in call["system"][0]["text"]]
    editors = [call for call in calls if "Uprise Final Lecture Editor" in call["system"][0]["text"]]
    assert len(drafts) == 1 and len(editors) == 2 and reviews == 1
    assert "outputConfig" not in drafts[0]


def test_local_failed_reply_diagnostics_are_cleaned_without_removing_sources(integration, lesson):
    target, doc, _ = accept(integration, lesson)
    settings = lesson[0].model_copy(update={"environment": "development", "local_test_mode": True,
                                          "lesson_capture_model_outputs": True, "max_attempts": 1})
    diagnostic_dir = integration[4].staging.job_dir(target["job_id"]) / "model-diagnostics"

    class Client:
        def converse(self, **request):
            return {"stopReason": "end_turn", "output": {"message": {"content": [
                {"text": "A malformed citation [p. 999]."}]}}}

    lesson[2].settings = settings
    lesson[2].models = LessonModels(settings, Client(), diagnostic_dir=diagnostic_dir)
    row = process(lesson)
    assert row["error_code"] == "LESSON_MODEL_INVALID_OUTPUT"
    assert len(list(diagnostic_dir.glob("*.json"))) == 1
    source_path = integration[4].staging.source_path(doc["job_id"])
    original = source_path.read_bytes()
    with api(integration, lesson) as client:
        response = client.delete(f"/v1/jobs/{target['job_id']}", params={"reason": "RECEIVED"})
        assert response.status_code == 204
    assert not diagnostic_dir.exists() and source_path.read_bytes() == original
    assert integration[3].document_count(doc["document_id"], "course-1") > 0


def test_bad_draft_citation_is_repaired_by_original_v3_through_api(integration, lesson):
    from test_a3_model_compat import Converse, review_data

    target, _, _ = accept(integration, lesson)
    client = Converse(["A source explanation [p. 999].",
        json.dumps(review_data(approved=False, citation_issues=["Wrong page."],
                              revision_instructions=["Use page 1."])),
        json.dumps({"lecture_text": "A corrected explanation [p. 1]."})])
    lesson[2].models = LessonModels(lesson[0], client)
    row = process(lesson)
    assert row["status"] == "DONE" and row["attempts"] == 1, row["error_code"]
    assert len(client.calls) == 3
    with api(integration, lesson) as http:
        response = http.get(f"/v1/jobs/{target['job_id']}/result")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "DRAFT" and not body["reviewer_report"]["approved"]
        assert all("[p. 999]" not in chunk["text"] and chunk["revisions"] == 1 for chunk in body["chunks"])
        assert any(issue["category"] == "citations" for issue in body["reviewer_report"]["issues"])
        audio = http.get(body["chunks"][0]["audio_url"])
        assert audio.status_code == 200 and audio.content.startswith(b"RIFF")

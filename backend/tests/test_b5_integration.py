"""B5 on disposable PostgreSQL/Qdrant and fake-provider lessons only."""

from uuid import uuid4

import psycopg
from test_b1_integration import b1_api, completed
from test_b4 import Speech, Teacher

from upraise_ai.main import app
from upraise_ai.workflows.interrupts import InterruptService

pytest_plugins = ["test_a1_integration", "test_a3_integration", "test_b1"]


def end_events(lesson, job_id):
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        return conn.execute("SELECT details FROM lesson_audit WHERE job_id=%s AND event_type='session_ended'",
                            (job_id,)).fetchall()


def test_b5_retains_course_wavs_pg_text_source_vectors_and_ready_catalog(integration, lesson):
    target, doc, row, body = completed(integration, lesson)
    prepared = lesson[1].prepared_chunks(target["job_id"])
    source = integration[1].get(doc["job_id"])
    vectors = integration[3]
    source_count = vectors.document_count(str(doc["document_id"]), body["course_id"], source["active_attempt"])
    teacher = Teacher()
    with b1_api(integration, lesson) as client:
        body.update(session_id=str(uuid4()), consent_scope=["questions"])
        started = client.post("/v1/sessions", json=body)
        assert started.status_code == 201
        session_id = started.json()["session_id"]
        result = client.get(f"/v1/jobs/{target['job_id']}/result")
        chunk_id = result.json()["chunks"][0]["chunk_id"]
        audio_path = f"/v1/jobs/{target['job_id']}/audio/{chunk_id}"
        audio = client.get(audio_path).content
        app.state.interrupts = InterruptService(
            lesson[0].model_copy(update={"environment": "development", "local_test_mode": True}),
            app.state.lessons, app.state.sessions, vectors, teacher_factory=lambda *args: teacher,
            speech_factory=Speech)
        question = {"request_id": str(uuid4()), "chunk_id": chunk_id, "text": "Explain this private learner request"}
        interruption = client.post(f"/v1/sessions/{session_id}/interrupts", json=question)
        assert interruption.status_code == 200 and interruption.json()["audio"]
        session = app.state.sessions.get(session_id, row["client_id"])
        assert session.interrupt_results
        for _ in range(2):
            ended = client.post(f"/v1/sessions/{session_id}/end", json={"reason": "STOPPED_BY_LEARNER"})
            assert ended.status_code == 204 and ended.content == b""
        assert not session.chat_cache and not session.interrupt_results
        rejected = client.post(f"/v1/sessions/{session_id}/interrupts", json=question)
        assert rejected.status_code == 410 and rejected.json()["detail"] == "SESSION_ENDED"
        assert client.post("/v1/sessions", json=body).status_code == 410
        assert client.get(audio_path).content == audio
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 200
        assert target["job_id"] in [item["job_id"] for item in client.get("/v1/lesson-jobs").json()]
        assert lesson[1].prepared_chunks(target["job_id"]) == prepared
        assert lesson[1].get(target["job_id"]) == row
        assert vectors.document_count(str(doc["document_id"]), body["course_id"], source["active_attempt"]) == source_count
        body["session_id"] = str(uuid4())
        assert client.post("/v1/sessions", json=body).status_code == 201
    events = end_events(lesson, target["job_id"])
    assert len(events) == 1 and events[0][0]["reason"] == "STOPPED_BY_LEARNER"
    assert question["text"] not in str(events) and teacher.answer not in str(events)


def test_b5_fixed_ttl_audits_timeout_and_end_retry_is_idempotent(integration, lesson):
    target, _, _, body = completed(integration, lesson)
    with b1_api(integration, lesson) as client:
        started = client.post("/v1/sessions", json=body)
        assert started.status_code == 201
        session_id = started.json()["session_id"]
        session = app.state.sessions.get(session_id, lesson[4].client_id)
        session.chat_cache.append({"text": "private expired question"})
        session.deadline = 0
        app.state.sessions.prune()
        app.state.sessions.prune()
        assert not session.chat_cache
        assert client.post(f"/v1/sessions/{session_id}/end", json={"reason": "COMPLETED"}).status_code == 204
    events = end_events(lesson, target["job_id"])
    assert len(events) == 1 and events[0][0]["reason"] == "TIMEOUT"


def test_b5_can_close_session_after_lesson_output_is_disposed(integration, lesson):
    target, _, _, body = completed(integration, lesson)
    with b1_api(integration, lesson) as client:
        started = client.post("/v1/sessions", json=body)
        assert started.status_code == 201
        session_id = started.json()["session_id"]
        assert client.delete(f"/v1/jobs/{target['job_id']}", params={"reason": "RECEIVED"}).status_code == 204
        assert client.post(f"/v1/sessions/{session_id}/end", json={"reason": "STOPPED_BY_LEARNER"}).status_code == 204
    assert len(end_events(lesson, target["job_id"])) == 1

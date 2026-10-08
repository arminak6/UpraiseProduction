"""Exact session/job/PG/Qdrant integration on disposable services, no AWS."""

from uuid import uuid4

import psycopg
from test_b1_integration import b1_api, completed
from test_b4 import Speech, Teacher

from upraise_ai.main import app
from upraise_ai.workflows.interrupts import InterruptService

pytest_plugins = ["test_a1_integration", "test_a3_integration", "test_b1"]


def test_b4_real_prepared_chunk_and_scoped_qdrant_question(integration, lesson):
    target, doc, row, body = completed(integration, lesson)
    teacher = Teacher()
    teacher.intent.is_course_question = True
    vectors = integration[3]
    teacher.answer = "A supported explanation. [p. 1]"
    with b1_api(integration, lesson) as client:
        started = client.post("/v1/sessions", json={**body, "consent_scope": ["questions"]})
        assert started.status_code == 201
        result = app.state.lessons.result(row)
        stored = lesson[1].prepared_chunk(target["job_id"], result.chunks[0].chunk_id)
        app.state.interrupts = InterruptService(app.state.lessons.settings.model_copy(update={
            "environment": "development", "local_test_mode": True}), app.state.lessons, app.state.sessions, vectors,
            teacher_factory=lambda *args: teacher, speech_factory=Speech,
            embeddings=type("Embed", (), {"embed_query": lambda _, query: [1.0]+[0.0]*(lesson[0].embedding_dimension-1)})())
        payload = {"request_id": str(uuid4()), "chunk_id": result.chunks[0].chunk_id, "text": "Why does this work?"}
        path = f"/v1/sessions/{started.json()['session_id']}/interrupts"
        first = client.post(path, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["grounded"] is None and first.json()["audio"]
        assert first.json()["sources"] and "GROUNDING_NOT_VERIFIED" in first.json()["warnings"]
        assert ("check_output", first.json()["text"]) in teacher.calls
        assert teacher.context == stored["text"]
        assert all(chunk["document_id"] == str(doc["document_id"]) for chunk in teacher.chunks)
        assert client.post(path, json=payload).json() == first.json()
        with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
            events = conn.execute("SELECT details FROM lesson_audit WHERE job_id=%s AND event_type='learner_interrupt'", (target["job_id"],)).fetchall()
        assert len(events) == 1 and payload["text"] not in str(events)
        assert lesson[1].prepared_chunk(target["job_id"], result.chunks[0].chunk_id) == stored
        # A2/source revocation also prevents delivery of a previously cached answer.
        with integration[1].connect() as conn:
            conn.execute("UPDATE ingestion_jobs SET deletion_requested_at=clock_timestamp() WHERE document_id=%s", (doc["document_id"],))
        assert client.post(path, json=payload).status_code == 409


def test_b4_qdrant_filters_keep_attempt_and_document_pairing(integration):
    from qdrant_client import models
    settings, _, _, vectors, _, _ = integration
    first, second, foreign = str(uuid4()), str(uuid4()), str(uuid4())
    one, two, stale = str(uuid4()), str(uuid4()), str(uuid4())
    vectors.ensure_collection()
    payloads = [(first, one, "allowed-a"), (second, two, "allowed-b"), (first, stale, "stale"),
                (first, two, "crossed-token"), (foreign, one, "foreign")]
    points = [models.PointStruct(id=str(uuid4()), vector=[1.0]+[0.0]*(settings.embedding_dimension-1),
        payload={"document_id": doc, "ingestion_token": token, "course_id": "scope-test", "chunk_id": name})
              for doc, token, name in payloads]
    vectors.client.upsert(vectors.collection, points, wait=True)
    try:
        result = vectors.search_session([1.0]+[0.0]*(settings.embedding_dimension-1), "scope-test",
                                         [(first, one), (second, two)], limit=10)
        assert {item["chunk_id"] for item in result} == {"allowed-a", "allowed-b"}
    finally:
        vectors.client.delete(vectors.collection, models.PointIdsList(points=[item.id for item in points]), wait=True)


def test_b4_real_prepared_text_support_returns_matching_text_and_audio(integration, lesson):
    target, _, row, body = completed(integration, lesson)
    teacher = Teacher()
    teacher.answer = "The same teaching idea, expressed more simply."
    spoken = []

    class MatchingSpeech(Speech):
        def prepare(self, text, language):
            spoken.append(text)
            return super().prepare(text, language)

    with b1_api(integration, lesson) as client:
        started = client.post("/v1/sessions", json={**body, "consent_scope": ["questions"]})
        assert started.status_code == 201
        result = app.state.lessons.result(row)
        chunk = result.chunks[0]
        before = lesson[1].prepared_chunk(target["job_id"], chunk.chunk_id)
        app.state.interrupts = InterruptService(app.state.lessons.settings.model_copy(update={
            "environment": "development", "local_test_mode": True}), app.state.lessons,
            app.state.sessions, integration[3], teacher_factory=lambda *args: teacher, speech_factory=MatchingSpeech)
        reply = client.post(f"/v1/sessions/{started.json()['session_id']}/interrupts", json={
            "request_id": str(uuid4()), "chunk_id": chunk.chunk_id, "text": "Explain in other words"})
        assert reply.status_code == 200
        answer = reply.json()
        assert answer["text"] == teacher.answer and answer["audio"]
        assert spoken == [answer["text"]] and answer["grounded"] is None
        assert answer["sources"] and "ANSWER_NOT_SUPPORTED" not in answer["warnings"]
        assert ("check_output", answer["text"]) in teacher.calls
        assert teacher.context == before["text"]
        assert lesson[1].prepared_chunk(target["job_id"], chunk.chunk_id) == before

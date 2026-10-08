"""B4 routing, output safety/language, session isolation and HTTP privacy without AWS."""

import base64
import io
import json
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from upraise_ai.core.config import Settings, get_settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.schemas.interrupts import InterruptInput, LearnerIntent, OutputCheck
from upraise_ai.schemas.lessons import LessonChunk, LessonSource, ReviewReport, Viseme
from upraise_ai.schemas.sessions import StartedSession
from upraise_ai.services.interrupt_models import InterruptTeacher
from upraise_ai.services.interrupt_stt import InterruptSTT, wav_pcm
from upraise_ai.storage.sessions import RuntimeSession, SessionBinding, SessionStore
from upraise_ai.workflows.interrupts import InterruptService


def wav(seconds=0.1, rate=16000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x00" * int(seconds*rate)*channels)
    return buffer.getvalue()


class Teacher:
    def __init__(self):
        self.intent = LearnerIntent(operation="respond", student_goal="Explain the concept differently",
                                    is_course_question=False, use_course_retrieval=False, retrieval_query="")
        self.calls, self.context, self.history, self.chunks = [], None, None, None
        self.check = OutputCheck(safe=True, in_language=True)
        self.answer = "A supported explanation. [p. 1]"
        self.wait = None

    def classify(self, message, context, history):
        self.calls.append(("classify", message))
        self.context, self.history = context, history
        if self.wait:
            self.wait[0].set()
            assert self.wait[1].wait(5)
        return self.intent

    def respond(self, message, goal, context, history, chunks):
        self.calls.append(("respond", goal))
        self.chunks = chunks
        return self.answer

    def clarify(self, *args):
        return "Which concept do you mean?"

    def check_output(self, text):
        self.calls.append(("check_output", text))
        return self.check

    def rewrite_query(self, query, language):
        self.calls.append(("rewrite", language))
        return query + " source-language"


class Speech:
    def __init__(self, settings, teacher):
        pass

    def validate_voice(self, voice, language):
        return language

    def prepare(self, text, language):
        return "<speak>" + text + "</speak>", [text], []

    def synthesize(self, parts, voice, language_code):
        return wav(), [Viseme(time_ms=0, value="sil")], 0.1, "a"*64


@pytest.fixture
def runtime():
    settings = Settings(environment="development", local_test_mode=True)
    ids = [str(uuid4()) for _ in range(5)]
    session_id, job_id, document_id, chunk_id, token = ids
    response = StartedSession(session_id=session_id, lesson_job_id=job_id, lesson_id="lesson",
        course_id="course", activity_id="activity", document_ids=[document_id], language="it", voice="Bianca",
        learner_level="beginner", result_sha256="a"*64, preview=True, ai_notice="AI", expires_at=datetime.now(UTC)+timedelta(minutes=5))
    session = RuntimeSession(SessionBinding("client", job_id, "a"*64, "course", "activity", "lesson",
        (document_id,), "it", "beginner", ("questions",), True), response, time.monotonic()+300, (chunk_id,))
    store = SessionStore()
    store.start(session, lambda value: None)
    source = LessonSource(document_id=document_id, document_version_id="version", pages=[1],
                          source_chunk_ids=["source-1"], quality_status="AUTO_CHECKED")
    chunk = LessonChunk(chunk_id=chunk_id, index=0, text="Exact finalized Italian teaching text.",
        ssml="<speak>text</speak>", ssml_parts=["<speak>text</speak>"], audio_url="", audio_sha256="a"*64,
        visemes=[], duration_seconds=30, sources=[source], reviewer_report=ReviewReport(approved=True, summary="ok"),
        section_index=0, revisions=0)
    source_row = {"client_id": "client", "metadata": {"course_id": "course", "language": "en"},
                  "status": "DONE", "active_attempt": token, "deletion_requested_at": None}
    job_row = {"job_id": job_id, "result": {"result_sha256": "a"*64}}
    result = SimpleNamespace(chunks=[chunk])
    audit_calls = []

    @contextmanager
    def lock(*args, **kwargs):
        yield None

    @contextmanager
    def document(*args):
        yield None, source_row

    lessons = SimpleNamespace(authorized=lambda *args: job_row, result=lambda row: result,
        staging=SimpleNamespace(artifact_lock=lock), documents=SimpleNamespace(document_transaction=document),
        jobs=SimpleNamespace(prepared_chunk=lambda *args: {"text": chunk.text, "sources": [source.model_dump()]}),
        audit=SimpleNamespace(learner_interrupt=lambda *args: audit_calls.append(args)))
    vector_chunk = {"chunk_id": "source-1", "document_id": document_id, "course_id": "course",
                    "document_version_id": "version", "ingestion_token": token, "page_numbers": [1],
                    "text": "An original source fact.", "quality_status": "AUTO_CHECKED"}
    search_calls = []

    def search(vector, course, scope, **kwargs):
        search_calls.append((vector, course, scope))
        return [vector_chunk]

    vectors = SimpleNamespace(source_chunks=lambda *args: [vector_chunk], search_session=search)
    teacher, stt_calls = Teacher(), []
    stt = SimpleNamespace(transcribe=lambda *args: stt_calls.append(args) or "Spiegalo in altre parole")
    service = InterruptService(settings, lessons, store, vectors, teacher_factory=lambda *args: teacher,
        speech_factory=Speech, stt=stt, embeddings=SimpleNamespace(embed_query=lambda text: [1, 0]))
    principal = Principal("client", (), frozenset({"sessions:write"}), local_test=True)
    data = InterruptInput(request_id=uuid4(), chunk_id=chunk_id, text="Can you explain in other words?")
    return SimpleNamespace(**locals())


def run(r, data=None, **kwargs):
    return r.service.handle(r.session_id, data or r.data, r.principal, **kwargs)


def test_b4_reexplanation_gets_exact_postgres_context_and_original_source(runtime):
    r = runtime
    value = run(r)
    assert value.grounded is None and value.sources[0].document_id == r.document_id
    assert r.teacher.context == r.chunk.text and r.teacher.chunks[0]["text"] == r.vector_chunk["text"]
    assert not r.search_calls
    assert ("check_output", value.text) in r.teacher.calls
    assert "GROUNDING_NOT_VERIFIED" in value.warnings
    assert value.audio and base64.b64decode(value.audio.data).startswith(b"RIFF")
    assert len(r.audit_calls) == 1 and len(r.session.chat_cache) == 2
    serialized_audit = json.dumps(r.audit_calls, default=str)
    assert r.data.text not in serialized_audit and value.text not in serialized_audit


def test_b4_course_question_always_searches_even_if_optional_search_flag_is_false(runtime):
    r = runtime
    r.teacher.intent.is_course_question = True
    value = run(r)
    assert value.grounded is None and value.sources and len(r.search_calls) == 1
    assert "GROUNDING_NOT_VERIFIED" in value.warnings
    assert r.search_calls[0][1:] == ("course", [(r.document_id, r.token)])
    assert ("rewrite", "en") in r.teacher.calls


def test_b4_v2_support_returns_teacher_text_and_speech_from_that_exact_text(runtime):
    r = runtime
    r.teacher.answer = "La stessa idea spiegata con parole più semplici."
    prepared = []

    class MatchingSpeech(Speech):
        def prepare(self, text, language):
            prepared.append((text, language))
            return super().prepare(text, language)

    r.service.speech_factory = MatchingSpeech
    value = run(r)
    assert value.text == r.teacher.answer and value.audio
    assert prepared == [(value.text, "it")]
    assert value.ssml == "<speak>" + value.text + "</speak>"
    assert value.grounded is None and value.sources
    assert "ANSWER_NOT_SUPPORTED" not in value.warnings
    assert ("check_output", value.text) in r.teacher.calls


def test_b4_native_v2_respond_preserves_full_pg_context_and_source_pages(runtime):
    r = runtime
    calls = []
    answer = "Una spiegazione semplice. [p. 1]"

    def converse(**request):
        calls.append(request)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": answer}]}}}

    teacher = InterruptTeacher(r.settings, r.session.binding, client=SimpleNamespace(converse=converse))
    context = "PG text " * 800
    sources = [{"chunk_id": "source-1", "document_id": r.document_id, "document_version_id": "version",
                "text": "Original source fact", "pages": [1], "quality_status": "AUTO_CHECKED"}]
    assert teacher.respond("Explain differently", "Re-explain", context,
                           [{"role": "user", "text": "Earlier message"}], sources) == answer
    contents = calls[0]["messages"][0]["content"]
    assert context in contents[0]["text"] and "Earlier message" in contents[1]["text"]
    assert '<COURSE_CHUNK id="source-1" pages="1">' in contents[1]["text"]
    assert calls[0]["inferenceConfig"]["temperature"] == 0.2


def test_b4_output_reviewer_only_checks_safety_and_session_language(runtime):
    r = runtime
    calls = []

    def converse(**request):
        calls.append(request)
        return {"stopReason": "end_turn", "output": {"message": {"content": [
            {"text": '{"safe":true,"in_language":true}'}]}}}

    teacher = InterruptTeacher(r.settings, r.session.binding, client=SimpleNamespace(converse=converse))
    assert teacher.check_output("Risposta in italiano.") == OutputCheck(safe=True, in_language=True)
    prompt, schema = calls[0]["messages"][0]["content"][0]["text"].split("\nJSON schema:\n")
    assert json.loads(prompt) == {"answer": "Risposta in italiano.", "target_language": "it"}
    assert set(json.loads(schema)["properties"]) == {"safe", "in_language"}
    assert calls[0]["modelId"] == r.settings.lesson_review_model_id


@pytest.mark.parametrize("raw", ["not JSON", '{"safe":1,"in_language":true}', '{"safe":true}'])
def test_b4_invalid_output_verdict_fails_closed(runtime, raw):
    r = runtime
    client = SimpleNamespace(converse=lambda **kwargs: {"stopReason": "end_turn", "output": {
        "message": {"content": [{"text": raw}]}}})
    teacher = InterruptTeacher(r.settings, r.session.binding, client=client)
    with pytest.raises(IngestionError, match="INTERRUPT_MODEL_INVALID_OUTPUT") as failure:
        teacher.check_output("PRIVATE_ANSWER")
    assert failure.value.status == 503 and "PRIVATE_ANSWER" not in str(failure.value)


def test_b4_player_action_uses_no_retrieval_response_speech_or_history(runtime):
    r = runtime
    r.teacher.intent = LearnerIntent(operation="playback", student_goal="Slow the current playback",
        is_course_question=False, use_course_retrieval=False, retrieval_query="",
        playback_action={"action": "SET_SPEED", "rate": 0.8})
    r.vectors.source_chunks = lambda *args: pytest.fail("Player action must not retrieve")
    r.service.speech_factory = lambda *args: pytest.fail("Player action must not synthesize")
    value = run(r)
    assert value.playback_action.rate == 0.8 and value.text is None and value.answer_id is None
    assert r.teacher.calls == [("classify", r.data.text)] and r.session.chat_cache == []


def test_b4_successful_retry_is_stable_and_conflicting_body_is_rejected(runtime):
    r = runtime
    first = run(r)
    calls = list(r.teacher.calls)
    assert run(r) == first and r.teacher.calls == calls and len(r.audit_calls) == 1
    with pytest.raises(HTTPException, match="INTERRUPT_REQUEST_CONFLICT"):
        run(r, r.data.model_copy(update={"text": "Another message"}))


def test_b4_evicted_retry_does_not_regenerate_and_cache_is_pruned(runtime):
    r = runtime
    for _ in range(3):
        run(r, r.data.model_copy(update={"request_id": uuid4()}))
    assert len(r.session.interrupt_results) == 2
    first_id = next(iter(r.session.interrupt_ids))
    with pytest.raises(HTTPException, match="INTERRUPT_RETRY_EXPIRED"):
        run(r, r.data.model_copy(update={"request_id": UUID(first_id)}))
    r.session.deadline = 0
    r.store.prune()
    assert r.store._interrupt_cache_bytes == 0


@pytest.mark.parametrize("is_question", [False, True])
@pytest.mark.parametrize("change,warning", [
    ({"safe": False}, "ANSWER_SAFETY_REJECTED"),
    ({"in_language": False}, "ANSWER_LANGUAGE_REJECTED"),
])
def test_b4_output_checks_reject_question_and_support_before_speech(runtime, is_question, change, warning):
    r = runtime
    r.teacher.intent.is_course_question = is_question
    r.teacher.intent.playback_action = {"action": "RESUME", "rate": None}
    r.teacher.check = OutputCheck.model_validate({**r.teacher.check.model_dump(), **change})
    spoken = []

    class CapturedSpeech(Speech):
        def prepare(self, text, language):
            spoken.append(text)
            return super().prepare(text, language)

    r.service.speech_factory = CapturedSpeech
    value = run(r)
    assert value.grounded is None and value.sources == [] and value.playback_action is None
    assert warning in value.warnings and "ANSWER_NOT_SUPPORTED" not in value.warnings
    assert value.text != r.teacher.answer and spoken == [value.text]
    assert r.teacher.answer not in json.dumps(r.audit_calls, default=str)
    assert r.session.chat_cache[-1]["text"] == value.text


def test_b4_factual_question_preserves_teacher_wording_without_literal_evidence_matching(runtime):
    r = runtime
    r.teacher.intent.is_course_question = True
    r.vector_chunk["text"] = "| Pipe network | 21.3 | 11.2 |"
    r.teacher.answer = "Per la rete di tubi:\n- reale: **21.3**\n- casuale: **11.2** [p. 1]"
    value = run(r)
    assert value.text == r.teacher.answer and value.audio and value.grounded is None
    assert value.sources[0].source_chunk_ids == ["source-1"]
    assert "GROUNDING_NOT_VERIFIED" in value.warnings
    assert "ANSWER_NOT_SUPPORTED" not in value.warnings


def test_b4_speech_failure_retains_safety_checked_text_and_unverified_grounding(runtime):
    r = runtime
    r.teacher.intent.is_course_question = True
    r.service.speech_factory = lambda *args: (_ for _ in ()).throw(RuntimeError("PRIVATE_AWS_ERROR"))
    value = run(r)
    assert value.grounded is None and value.text == r.teacher.answer
    assert value.audio is None and "AUDIO_UNAVAILABLE" in value.warnings
    assert "PRIVATE_AWS_ERROR" not in value.model_dump_json()


def test_b4_audio_is_transcribed_once_and_discarded_from_cache_and_audit(runtime):
    r = runtime
    data = r.data.model_copy(update={"text": None})
    value = run(r, data, audio=wav())
    assert value.grounded is None and len(r.stt_calls) == 1
    assert run(r, data, audio=wav()) == value and len(r.stt_calls) == 1
    assert r.session.chat_cache[0]["text"] == "Spiegalo in altre parole"
    assert b"RIFF" not in json.dumps(r.session.chat_cache).encode()


@pytest.mark.parametrize("audio,status", [(b"not audio", 422), (wav(channels=2), 422), (wav(seconds=61), 413)])
def test_b4_bad_audio_never_reaches_stt(runtime, audio, status):
    r = runtime
    with pytest.raises(IngestionError) as rejected:
        run(r, r.data.model_copy(update={"text": None}), audio=audio)
    assert rejected.value.status == status and not r.stt_calls


def test_b4_no_consent_or_wrong_chunk_does_not_call_model(runtime):
    from dataclasses import replace
    r = runtime
    r.session.binding = replace(r.session.binding, consent_scope=())
    with pytest.raises(HTTPException, match="QUESTIONS_CONSENT_REQUIRED"):
        run(r)
    r.session.binding = replace(r.session.binding, consent_scope=("questions",))
    with pytest.raises(HTTPException, match="LESSON_CHUNK_NOT_FOUND"):
        run(r, r.data.model_copy(update={"chunk_id": uuid4()}))
    assert not r.teacher.calls


def test_b4_deleted_replaced_or_foreign_sources_are_rejected(runtime):
    r = runtime
    r.vector_chunk["ingestion_token"] = str(uuid4())
    with pytest.raises(IngestionError, match="INTERRUPT_SOURCE_VERSION_MISMATCH"):
        run(r)
    assert not any(name == "respond" for name, _ in r.teacher.calls)


def test_b4_overlap_returns_conflict_and_expiry_during_model_drops_answer(runtime):
    r = runtime
    started, release = Event(), Event()
    r.teacher.wait = (started, release)
    with ThreadPoolExecutor() as pool:
        running = pool.submit(run, r)
        assert started.wait(5)
        with pytest.raises(HTTPException, match="INTERRUPT_IN_PROGRESS"):
            run(r, r.data.model_copy(update={"request_id": uuid4()}))
        r.session.deadline = 0
        release.set()
        with pytest.raises(HTTPException, match="SESSION_EXPIRED"):
            running.result(5)
    assert not r.audit_calls and not r.session.chat_cache


def test_b4_different_learner_sessions_can_execute_concurrently(runtime):
    r = runtime
    started, release = Event(), Event()
    r.teacher.wait = (started, release)
    another = RuntimeSession(r.session.binding, r.response.model_copy(update={"session_id": uuid4()}),
                             time.monotonic()+300, r.session.chunk_ids)
    r.store.start(another, lambda value: None)
    teachers = iter([r.teacher, Teacher()])
    r.service.teacher_factory = lambda *args: next(teachers)
    with ThreadPoolExecutor() as pool:
        running = pool.submit(run, r)
        assert started.wait(5)
        second = pool.submit(r.service.handle, another.response.session_id,
                             r.data.model_copy(update={"request_id": uuid4()}), r.principal)
        assert second.result(5).text == "A supported explanation. [p. 1]"
        release.set()
        assert running.result(5).text == "A supported explanation. [p. 1]"


def test_b4_audit_failure_does_not_publish_or_commit_chat(runtime):
    r = runtime
    import psycopg
    r.lessons.audit.learner_interrupt = lambda *args: (_ for _ in ()).throw(psycopg.OperationalError("private"))
    with pytest.raises(IngestionError, match="AUDIT_STORAGE_UNAVAILABLE"):
        run(r)
    assert not r.session.chat_cache and not r.session.interrupt_results


@contextmanager
def http(r):
    app.dependency_overrides[ingress] = lambda: r.principal
    app.dependency_overrides[get_settings] = lambda: r.settings
    try:
        with TestClient(app) as client:
            app.state.interrupts = r.service
            yield client, f"/v1/sessions/{r.session_id}/interrupts"
    finally:
        app.dependency_overrides.clear()


def test_b4_http_json_and_multipart_with_sanitized_errors(runtime):
    r = runtime
    with http(r) as (client, path):
        response = client.post(path, json=r.data.model_dump(mode="json"))
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert "type" not in response.json() and "kind" not in response.json()
        response = client.post(path, data={"request_id": str(uuid4()), "chunk_id": r.chunk_id},
                               files={"audio": ("private-learner.wav", wav(), "audio/wav")})
        assert response.status_code == 200 and r.stt_calls
        for body in [{"text": "PRIVATE_LEARNER_TEXT"}, {**r.data.model_dump(mode="json"), "history": []}]:
            bad = client.post(path, json=body)
            assert bad.status_code == 422 and "PRIVATE_LEARNER_TEXT" not in bad.text
        assert client.post(path, content="text", headers={"Content-Type": "text/plain"}).status_code == 415
        assert client.post(path, data={"request_id": str(uuid4()), "chunk_id": r.chunk_id, "text": "also text"},
                           files={"audio": ("x.wav", wav())}).status_code == 422
        schema = client.get("/openapi.json").json()["paths"]["/v1/sessions/{session_id}/interrupts"]["post"]
        assert set(schema["requestBody"]["content"]) == {"application/json", "multipart/form-data"}


def test_b4_http_unknown_session_and_body_limits(runtime):
    r = runtime
    with http(r) as (client, path):
        assert client.post(f"/v1/sessions/{uuid4()}/interrupts", json=r.data.model_dump(mode="json")).status_code == 404
        response = client.post(path, content=b"x"*(r.settings.interrupt_max_audio_bytes+65537),
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 413


def test_b4_native_router_has_open_ended_goal_language_and_level(runtime):
    r = runtime
    calls = []
    def converse(**request):
        calls.append(request)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": r.teacher.intent.model_dump_json()}]}}}
    teacher = InterruptTeacher(r.settings, r.session.binding, client=SimpleNamespace(converse=converse))
    intent = teacher.classify("Invent another way to teach this idea", "Current text", [])
    assert intent.student_goal and "keyword list" in calls[0]["system"][0]["text"]
    teacher.respond("Explain", intent.student_goal, "EXACT_PG_TEXT", [], [{
        "chunk_id": "source-1", "text": "source", "pages": [1], "document_id": r.document_id,
        "document_version_id": "version"}])
    assert "EXACT_PG_TEXT" in calls[-1]["messages"][0]["content"][0]["text"]
    policy = " ".join(item["text"] for item in calls[-1]["system"])
    assert "beginner" in policy and "Italian" in policy
    assert "<COURSE_CHUNK" in calls[-1]["messages"][0]["content"][1]["text"]


def test_b4_stt_sanitizes_provider_errors(runtime, monkeypatch):
    stt = InterruptSTT(runtime.settings)
    async def offline(*args):
        raise RuntimeError("PRIVATE_STT_TRANSCRIPT")
    monkeypatch.setattr(stt, "_transcribe", offline)
    with pytest.raises(IngestionError, match="INTERRUPT_STT_UNAVAILABLE") as failed:
        stt.transcribe(*wav_pcm(wav(), runtime.settings), "it")
    assert "PRIVATE_STT_TRANSCRIPT" not in str(failed.value)


def test_b4_signed_session_binds_subject_and_exact_generation(runtime, monkeypatch):
    from dataclasses import replace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from test_b1 import assertion, assertion_settings
    r = runtime
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr("upraise_ai.guards.session_jwt.jwks_client", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())))
    r.service.settings = assertion_settings()
    r.principal = replace(r.principal, local_test=False, courses=("course",))
    r.session.binding = replace(r.session.binding, subject="learner-a", preview=False)
    claims = assertion(session_id=r.session_id, lesson_job_id=r.job_id, client_id="client", sub="learner-a",
        course_id="course", activity_id="activity", lesson_id="lesson", consent_scope=["questions"])
    token = jwt.encode(claims, private, algorithm="RS256")
    assert run(r, token=token).text == "A supported explanation. [p. 1]"
    for update in [{"sub": "learner-b"}, {"lesson_job_id": str(uuid4())}, {"result_sha256": "b"*64},
                   {"consent_scope": []}, {"lesson_approved": False}]:
        changed = jwt.encode({**claims, **update}, private, algorithm="RS256")
        with pytest.raises(HTTPException, match="SESSION_SCOPE_MISMATCH"):
            run(r, token=changed)
    with pytest.raises(HTTPException):
        run(r)


def test_b4_global_audio_retry_cache_has_a_memory_quota(runtime, monkeypatch):
    r = runtime
    monkeypatch.setattr("upraise_ai.storage.sessions.INTERRUPT_CACHE_BYTES", 1000)
    assert run(r).audio
    assert r.store._interrupt_cache_bytes <= 1000
    with pytest.raises(HTTPException, match="INTERRUPT_RETRY_EXPIRED"):
        run(r)


def test_b4_capacity_and_revocation_during_response(runtime):
    r = runtime
    for _ in range(r.settings.interrupt_max_concurrent):
        assert r.service.capacity.acquire(blocking=False)
    with pytest.raises(HTTPException, match="INTERRUPT_CAPACITY_REACHED"):
        run(r)
    for _ in range(r.settings.interrupt_max_concurrent):
        r.service.capacity.release()
    def revoke(*args):
        r.source_row["deletion_requested_at"] = datetime.now(UTC)
        return r.teacher.check
    r.teacher.check_output = revoke
    r.teacher.intent.is_course_question = True
    with pytest.raises(IngestionError, match="LESSON_DOCUMENT_UNAVAILABLE"):
        run(r)
    assert not r.audit_calls and not r.session.chat_cache


def test_b4_streaming_stt_uses_session_language_final_results_and_no_file(runtime, monkeypatch):
    import asyncio

    from amazon_transcribe.model import TranscriptEvent
    calls, sent = [], []
    class Input:
        async def send_audio_event(self, audio_chunk):
            sent.append(audio_chunk)
        async def end_stream(self):
            sent.append(b"END")
    async def output():
        yield TranscriptEvent(transcript=SimpleNamespace(results=[SimpleNamespace(
            is_partial=True, result_id="1", alternatives=[SimpleNamespace(transcript="partial")])]))
        yield TranscriptEvent(transcript=SimpleNamespace(results=[SimpleNamespace(
            is_partial=False, result_id="1", alternatives=[SimpleNamespace(transcript="Spiega ancora")])]))
    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        async def start_stream_transcription(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(input_stream=Input(), output_stream=output())
    credentials = SimpleNamespace(access_key="fake-key", secret_key="fake-secret", token="fake-token")
    monkeypatch.setattr("upraise_ai.services.interrupt_stt.aws_session", lambda _: SimpleNamespace(
        get_credentials=lambda: SimpleNamespace(get_frozen_credentials=lambda: credentials)))
    monkeypatch.setattr("amazon_transcribe.client.TranscribeStreamingClient", Client)
    pcm, rate = wav_pcm(wav(seconds=0.01), runtime.settings)
    text = asyncio.run(InterruptSTT(runtime.settings)._transcribe(pcm, rate, "it"))
    assert text == "Spiega ancora" and calls[1]["language_code"] == "it-IT"
    assert calls[1]["media_encoding"] == "pcm" and b"".join(sent[:-1]) == pcm

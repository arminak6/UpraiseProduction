"""Session ending, audit recovery, race fencing and authorization without AWS."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import jwt
import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_b1 import assertion, assertion_settings
from test_b1 import runtime as make_runtime
from test_b4 import run, wav
from test_b4 import runtime as interrupt_runtime

from upraise_ai.core.config import Settings, get_settings
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.storage.sessions import SessionStore

pytest_plugins = ["test_b1"]


@pytest.fixture
def b4_runtime():
    return interrupt_runtime.__wrapped__()


def setup_store(**kwargs):
    now = datetime.now(UTC)
    audits = []
    store = SessionStore(monotonic=lambda: 0, now=lambda: now, end_audit=audits.append, **kwargs)
    session = make_runtime(now, client_id="client")
    store.start(session, lambda _: None)
    return store, session, audits


@contextmanager
def http(store, *, principal=None, settings=None):
    configuration = settings or Settings(environment="development", local_test_mode=True)
    principal = principal or Principal("client", (), frozenset({"sessions:write"}), local_test=True)
    app.dependency_overrides[ingress] = lambda: principal
    app.dependency_overrides[get_settings] = lambda: configuration
    try:
        with TestClient(app) as client:
            app.state.sessions = store
            app.state.lessons = SimpleNamespace(audit=SimpleNamespace(session_ended=store.end_audit))
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("reason", ["COMPLETED", "STOPPED_BY_LEARNER", "TIMEOUT", "ERROR"])
def test_b5_http_204_without_questions_consent_and_idempotent_retry(reason):
    store, session, audits = setup_store()
    with http(store) as client:
        path = f"/v1/sessions/{session.response.session_id}/end"
        for requested in (reason, reason, "ERROR"):
            result = client.post(path, json={"reason": requested})
            assert result.status_code == 204 and result.content == b""
            assert result.headers["cache-control"] == "no-store"
        assert len(audits) == 1 and audits[0].reason == reason
        with pytest.raises(HTTPException, match="SESSION_ENDED|SESSION_EXPIRED"):
            store.get(session.response.session_id, "client")
        with pytest.raises(HTTPException):
            store.start(session, lambda _: None)


@pytest.mark.parametrize("body", [{}, {"reason": "OTHER"}, {"reason": None},
                                 {"reason": "COMPLETED", "text": "private"}])
def test_b5_invalid_input_does_not_close_session(body):
    store, session, audits = setup_store()
    with http(store) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/end", json=body)
        assert result.status_code == 422
        assert store.get(session.response.session_id, "client") is session
        assert not audits
        assert client.post("/v1/sessions/not-a-uuid/end", json={"reason": "COMPLETED"}).status_code == 422


@pytest.mark.parametrize("principal,status", [
    (Principal("foreign", (), frozenset({"sessions:write"}), local_test=True), 404),
    (Principal("client", ("other-course",), frozenset({"sessions:write"})), 403),
    (Principal("client", (), frozenset(), local_test=True), 403),
])
def test_b5_client_course_and_scope_isolation(principal, status):
    store, session, audits = setup_store()
    with http(store, principal=principal) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/end", json={"reason": "COMPLETED"})
        assert result.status_code == status and not audits
        assert store.get(session.response.session_id, "client") is session


def test_b5_unknown_session_is_404():
    store, _, audits = setup_store()
    with http(store) as client:
        assert client.post(f"/v1/sessions/{uuid4()}/end", json={"reason": "ERROR"}).status_code == 404
        assert not audits


def test_b5_signed_identity_cannot_end_another_learner_or_generation(signing):
    for overrides in ({}, {"sub": "another"}, {"lesson_job_id": str(uuid4())},
                      {"session_id": str(uuid4())}, {"consent_scope": ["questions"]},
                      {"result_sha256": "b" * 64}, {"lesson_approved": False}):
        store, session, audits = setup_store()
        session.binding = replace(session.binding, preview=False, subject="learner")
        session.response.preview = False
        claims = assertion(client_id="client", sub="learner", session_id=str(session.response.session_id),
                           lesson_job_id=session.binding.lesson_job_id, consent_scope=[],
                           **{key: getattr(session.binding, key) for key in ("course_id", "activity_id", "lesson_id")})
        claims.update(overrides)
        token = jwt.encode(claims, signing, algorithm="RS256")
        with http(store, settings=assertion_settings()) as client:
            path = f"/v1/sessions/{session.response.session_id}/end"
            assert client.post(path, json={"reason": "COMPLETED"}).status_code == 401
            result = client.post(path, json={"reason": "COMPLETED"}, headers={"X-UPRAISE-Session-JWT": token})
            assert result.status_code == (403 if overrides else 204), result.text
            assert len(audits) == (0 if overrides else 1)


def test_b5_wipes_actual_b4_audio_cache_and_preserves_other_learner(b4_runtime):
    r = b4_runtime
    audits = []
    r.store.end_audit = audits.append
    value = run(r)
    assert value.audio and r.store._interrupt_cache_bytes > 0
    second = make_runtime(datetime.now(UTC), client_id="client")
    second.deadline = r.session.deadline
    r.store.start(second, lambda _: None)
    second.chat_cache.append({"text": "another learner"})
    r.session.cognitive_load_state = {"load": "high"}
    r.store.end(r.session_id, "client", "STOPPED_BY_LEARNER")
    assert not r.session.chat_cache and not r.session.interrupt_results and not r.session.interrupt_ids
    assert not r.session.prompt_guard_counters and r.session.cognitive_load_state is None
    assert r.session.current_chunk_index == 0 and not r.session.chunk_ids
    assert r.store._interrupt_cache_bytes == 0 and not r.store._interrupt_cache
    assert r.store.get(second.response.session_id, "client").chat_cache == [{"text": "another learner"}]
    with pytest.raises(HTTPException, match="SESSION_ENDED"):
        run(r)
    assert len(audits) == 1


def test_b5_fences_a_running_b4_without_waiting_for_model(b4_runtime):
    r = b4_runtime
    r.store.end_audit = lambda _: None
    waiting, release = Event(), Event()
    r.teacher.wait = (waiting, release)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run, r)
        try:
            assert waiting.wait(5)
            r.store.end(r.session_id, "client", "STOPPED_BY_LEARNER")
            assert not r.session.chat_cache and not r.session.interrupt_results
        finally:
            release.set()
        with pytest.raises(HTTPException, match="SESSION_ENDED"):
            future.result(timeout=5)
    assert not r.audit_calls and not r.store._interrupt_cache


def test_b5_discards_inflight_stt_before_any_teacher_call(b4_runtime):
    r = b4_runtime
    r.store.end_audit = lambda _: None
    waiting, release = Event(), Event()

    def transcribe(*args):
        waiting.set()
        assert release.wait(5)
        return "private spoken learner request"

    r.service.stt = SimpleNamespace(transcribe=transcribe)
    data = r.data.model_copy(update={"text": None})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run, r, data, audio=wav())
        try:
            assert waiting.wait(5)
            r.store.end(r.session_id, "client", "STOPPED_BY_LEARNER")
        finally:
            release.set()
        with pytest.raises(HTTPException, match="SESSION_ENDED"):
            future.result(timeout=5)
    assert not r.teacher.calls and not r.audit_calls and not r.session.chat_cache


def test_b5_audit_failure_closes_immediately_then_retries_once():
    store, session, audits = setup_store()
    session.chat_cache.append({"text": "private"})
    available = [False]

    def audit(ended):
        if not available[0]:
            raise psycopg.OperationalError("PRIVATE_DATABASE_DETAIL")
        audits.append(ended)

    store.end_audit = audit
    with http(store) as client:
        path = f"/v1/sessions/{session.response.session_id}/end"
        result = client.post(path, json={"reason": "STOPPED_BY_LEARNER"})
        assert result.status_code == 503 and result.json()["detail"] == "AUDIT_STORAGE_UNAVAILABLE"
        assert "PRIVATE_DATABASE_DETAIL" not in result.text
        assert not session.chat_cache and not store._sessions
        available[0] = True
        assert client.post(path, json={"reason": "STOPPED_BY_LEARNER"}).status_code == 204
        assert client.post(path, json={"reason": "ERROR"}).status_code == 204
        assert len(audits) == 1 and audits[0].reason == "STOPPED_BY_LEARNER"


def test_b5_expiry_uses_same_cleanup_and_retries_background_audit():
    now = [datetime.now(UTC)]
    tick = [0]
    calls = []
    available = [False]

    def audit(ended):
        if not available[0]:
            raise psycopg.OperationalError("offline")
        calls.append(ended)

    store = SessionStore(1, now=lambda: now[0], monotonic=lambda: tick[0], end_audit=audit)
    session = make_runtime(now[0], client_id="client")
    store.start(session, lambda _: None)
    session.chat_cache.append({"text": "private"})
    tick[0] = 121
    store.prune()
    assert not session.chat_cache and not store._sessions and not calls
    with pytest.raises(HTTPException):
        store.start(make_runtime(now[0]), lambda _: None)
    available[0] = True
    store.prune()
    store.prune()
    assert len(calls) == 1 and calls[0].reason == "TIMEOUT"
    store.end(session.response.session_id, "client", "COMPLETED")
    assert len(calls) == 1
    tick[0] += 1801
    store.prune()
    assert not store._ended


def test_b5_concurrent_end_audit_is_once_and_does_not_block_other_sessions():
    store, first, audits = setup_store()
    second = make_runtime(datetime.now(UTC), client_id="client")
    store.start(second, lambda _: None)
    waiting, release = Event(), Event()

    def slow(ended):
        waiting.set()
        assert release.wait(5)
        audits.append(ended)

    store.end_audit = slow
    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(store.end, first.response.session_id, "client", "COMPLETED")
        try:
            assert waiting.wait(5)
            two = pool.submit(store.end, first.response.session_id, "client", "COMPLETED")
            assert store.get(second.response.session_id, "client") is second
            assert not first.chat_cache
        finally:
            release.set()
        one.result(timeout=5)
        two.result(timeout=5)
    assert len(audits) == 1


def test_b5_tombstones_are_bounded_without_evicting_live_sessions():
    store, first, audits = setup_store(capacity=1)
    store.end(first.response.session_id, "client", "COMPLETED")
    second = make_runtime(datetime.now(UTC), client_id="client")
    store.start(second, lambda _: None)
    store.end(second.response.session_id, "client", "COMPLETED")
    assert len(store._ended) == 1 and len(audits) == 2


def test_b5_audit_metadata_has_no_learner_content(monkeypatch):
    from upraise_ai.storage.audit import AuditRepository
    store, session, _ = setup_store()
    store.end(session.response.session_id, "client", "COMPLETED")
    captured = []

    @contextmanager
    def connection(*args, **kwargs):
        yield SimpleNamespace(execute=lambda query, params: captured.append((query, params)))

    monkeypatch.setattr("upraise_ai.storage.audit.psycopg.connect", connection)
    ended = store.get(session.response.session_id, "client", include_ended=True)
    AuditRepository("unused-test-dsn").session_ended(ended)
    query, params = captured[0]
    assert "session_ended" in query and str(params[1]) == session.binding.lesson_job_id
    details = params[2].obj
    assert set(details) == {"session_id", "lesson_id", "course_id", "activity_id", "reason", "ended_at"}
    assert "subject" not in json.dumps(details) and "consent" not in json.dumps(details)

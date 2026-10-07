"""Real signatures and concurrent in-memory session lifecycle, without AWS."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from pydantic import ValidationError

from upraise_ai.core.config import Settings
from upraise_ai.guards.session_jwt import validate_session_jwt
from upraise_ai.schemas.sessions import SessionInput, StartedSession
from upraise_ai.storage.sessions import RuntimeSession, SessionBinding, SessionStore


def assertion_settings(**updates):
    return Settings(**{"session_jwt_issuer": "https://mw.example", "session_jwt_jwks_url": "https://mw.example/keys",
                       "oauth_audience": "upraise-service", **updates})


def assertion(**updates):
    return {"iss": "https://mw.example", "aud": "upraise-learner-session", "sub": "pseudonymous-learner",
            "iat": int(time.time()), "exp": int(time.time())+1200, "client_id": "middleware",
            "session_id": str(uuid4()), "course_id": "course-1", "activity_id": "activity-1",
            "lesson_id": "lesson-1", "lesson_job_id": str(uuid4()), "result_sha256": "a"*64,
            "consent_scope": ["questions", "telemetry"], "lesson_approved": True, **updates}


@pytest.fixture
def signing(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr("upraise_ai.guards.session_jwt.jwks_client", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key()),
    ))
    return private


def test_signed_session_assertion_has_distinct_purpose(signing):
    data = assertion()
    claims = validate_session_jwt(jwt.encode(data, signing, algorithm="RS256"), assertion_settings())
    assert str(claims.session_id) == data["session_id"]
    assert claims.consent_scope == ["questions", "telemetry"]
    assert claims.lesson_approved


@pytest.mark.parametrize("change", [
    {"iss": "https://other.example"}, {"aud": "upraise-service"}, {"exp": int(time.time())-60},
    {"session_id": "invalid"}, {"lesson_job_id": "invalid"}, {"consent_scope": "questions"},
    {"consent_scope": ["admin"]}, {"consent_scope": ["questions", "questions"]},
    {"lesson_approved": "true"}, {"exp": True}, {"result_sha256": "bad"},
])
def test_invalid_assertions_are_sanitized(signing, change):
    with pytest.raises(HTTPException) as denied:
        validate_session_jwt(jwt.encode(assertion(**change), signing, algorithm="RS256"), assertion_settings())
    assert denied.value.status_code == 401 and denied.value.detail == "INVALID_SESSION_JWT"


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "aud", "sub", "client_id", "session_id",
                                     "course_id", "activity_id", "lesson_id", "lesson_job_id",
                                     "result_sha256", "consent_scope", "lesson_approved"])
def test_assertion_requires_authorization_claims(signing, missing):
    data = assertion()
    del data[missing]
    with pytest.raises(HTTPException) as denied:
        validate_session_jwt(jwt.encode(data, signing, algorithm="RS256"), assertion_settings())
    assert denied.value.status_code == 401


def test_forged_unsigned_and_wrong_algorithm_assertions_are_rejected(signing):
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tokens = [jwt.encode(assertion(), forged, algorithm="RS256"),
              jwt.encode(assertion(), "", algorithm="none"),
              jwt.encode(assertion(), "untrusted-test-key-which-is-long-enough", algorithm="HS256")]
    for token in tokens:
        with pytest.raises(HTTPException) as denied:
            validate_session_jwt(token, assertion_settings())
        assert denied.value.status_code == 401


def test_session_auth_is_not_configured_or_unavailable(monkeypatch, signing):
    token = jwt.encode(assertion(), signing, algorithm="RS256")
    for settings in [Settings(), assertion_settings(session_jwt_jwks_url="http://mw.example/keys"),
                     assertion_settings(oauth_audience="upraise-learner-session")]:
        with pytest.raises(HTTPException) as denied:
            validate_session_jwt(token, settings)
        assert denied.value.status_code == 503
    for token_value in [None, "", "x"*16385]:
        with pytest.raises(HTTPException) as denied:
            validate_session_jwt(token_value, assertion_settings())
        assert denied.value.status_code == 401

    def offline(_):
        raise jwt.PyJWKClientConnectionError("PRIVATE_KEY_SERVER_ERROR")

    monkeypatch.setattr("upraise_ai.guards.session_jwt.jwks_client", offline)
    with pytest.raises(HTTPException) as denied:
        validate_session_jwt(token, assertion_settings())
    assert denied.value.detail == "SESSION_IDENTITY_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("update", [{"document_ids": []}, {"document_ids": ["bad"]},
                                    {"document_ids": [str(uuid4())]*2}, {"consent_scope": ["questions"]*2},
                                    {"consent_scope": ["unknown"]}, {"language": "bad_language"},
                                    {"lesson_job_id": "bad"}, {"extra": True}])
def test_session_input_is_bounded_and_validated(update):
    with pytest.raises(ValidationError):
        SessionInput.model_validate({"course_id": "course-1", "activity_id": "activity-1",
                                     "lesson_id": "lesson-1", "lesson_job_id": str(uuid4()), **update})


def runtime(now, *, session_id=None, client_id="middleware", course_id="course-1", seconds=120):
    job_id, document_id = str(uuid4()), str(uuid4())
    response = StartedSession(
        session_id=session_id or uuid4(), lesson_job_id=job_id, lesson_id="lesson-1", course_id=course_id,
        activity_id="activity-1", document_ids=[document_id], language="it", learner_level="beginner",
        voice="Bianca", result_sha256="a"*64, preview=True,
        ai_notice="AI notice", expires_at=now+timedelta(seconds=seconds),
    )
    binding = SessionBinding(client_id, job_id, "a"*64, course_id, "activity-1", "lesson-1", (document_id,),
                             "it", "beginner", (), True)
    return RuntimeSession(binding, response, seconds, (str(uuid4()),))


def test_sessions_are_isolated_idempotent_and_not_recovered_after_restart():
    now = datetime.now(UTC)
    store = SessionStore(monotonic=lambda: 0, now=lambda: now)
    first, second = runtime(now), runtime(now)
    audits = []
    assert store.start(first, audits.append)[1]
    assert store.start(second, audits.append)[1]
    first.chat_cache.append({"content": "private question"})
    reply, created = store.start(first, audits.append)
    assert not created and len(audits) == 2
    reply.document_ids.clear()
    assert store.get(first.response.session_id, "middleware").response.document_ids
    assert second.chat_cache == [] and second.prompt_guard_counters == {"rejections": 0, "prompts": 0}
    with pytest.raises(HTTPException) as denied:
        store.get(first.response.session_id, "other-client")
    assert denied.value.status_code == 404
    store.clear()
    with pytest.raises(HTTPException):
        store.get(first.response.session_id, "middleware")
    with pytest.raises(HTTPException):
        SessionStore().get(first.response.session_id, "middleware")


def test_expiry_and_capacity_do_not_evict_active_learners():
    now = datetime.now(UTC)
    clock = [0]
    store = SessionStore(1, monotonic=lambda: clock[0], now=lambda: now)
    first, second = runtime(now), runtime(now, seconds=240)
    store.start(first, lambda _: None)
    with pytest.raises(HTTPException) as denied:
        store.start(second, lambda _: None)
    assert denied.value.detail == "SESSION_CAPACITY_REACHED"
    clock[0] = 120
    with pytest.raises(HTTPException) as denied:
        store.get(first.response.session_id, "middleware")
    assert denied.value.status_code == 410
    assert store.start(second, lambda _: None)[1]
    clock[0] = 240
    store.prune()
    assert not store._sessions


def test_wall_clock_expiry_and_binding_conflicts():
    current = [datetime.now(UTC)]
    store = SessionStore(monotonic=lambda: 0, now=lambda: current[0])
    first = runtime(current[0])
    store.start(first, lambda _: None)
    other = runtime(current[0], session_id=first.response.session_id)
    with pytest.raises(HTTPException) as denied:
        store.start(other, lambda _: None)
    assert denied.value.detail == "SESSION_CONTEXT_CONFLICT"
    foreign = runtime(current[0], session_id=first.response.session_id, client_id="another-client")
    with pytest.raises(HTTPException) as denied:
        store.start(foreign, lambda _: None)
    assert denied.value.status_code == 404
    current[0] += timedelta(seconds=120)
    store.prune()
    assert not store._sessions


def test_audit_failure_never_exposes_unaudited_session():
    now = datetime.now(UTC)
    store = SessionStore(monotonic=lambda: 0, now=lambda: now)
    first = runtime(now)

    def failing(_):
        raise RuntimeError("audit failed")

    with pytest.raises(RuntimeError):
        store.start(first, failing)
    assert not store._sessions and not store._pending
    assert store.start(first, lambda _: None)[1]


def test_concurrent_audit_does_not_block_other_learners_or_duplicate_same_session():
    now = datetime.now(UTC)
    store = SessionStore(monotonic=lambda: 0, now=lambda: now)
    first, second = runtime(now), runtime(now)
    waiting, release = Event(), Event()

    def slow(_):
        waiting.set()
        assert release.wait(5)

    with ThreadPoolExecutor(2) as executor:
        started = executor.submit(store.start, first, slow)
        try:
            assert waiting.wait(5)
            assert store.start(second, lambda _: None)[1]
            with pytest.raises(HTTPException) as denied:
                store.start(first, lambda _: None)
            assert denied.value.detail == "SESSION_START_IN_PROGRESS"
        finally:
            release.set()
        assert started.result()[1]


def test_shutdown_and_deadline_during_audit_cannot_publish_session():
    now = datetime.now(UTC)
    store = SessionStore(monotonic=lambda: 0, now=lambda: now)
    with pytest.raises(HTTPException) as denied:
        store.start(runtime(now), lambda _: store.clear())
    assert denied.value.detail == "SESSION_STORAGE_UNAVAILABLE"
    assert not store._sessions
    current = [now]
    store = SessionStore(monotonic=lambda: 0, now=lambda: current[0])

    def elapse(_):
        current[0] += timedelta(seconds=120)

    with pytest.raises(HTTPException) as denied:
        store.start(runtime(now), elapse)
    assert denied.value.detail == "SESSION_EXPIRED"

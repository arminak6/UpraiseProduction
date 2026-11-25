"""B1 against disposable PostgreSQL/Qdrant and prepared fake-provider lessons."""

import json
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from test_a3_integration import accept, api, process
from test_b1 import assertion, assertion_settings

from upraise_ai.core.config import get_settings
from upraise_ai.guards.ingress import ingress
from upraise_ai.main import app

pytest_plugins = ["test_a1_integration", "test_a3_integration", "test_b1"]


def completed(integration, lesson, **updates):
    target, doc, data = accept(integration, lesson, **updates)
    # These tests inspect session metadata, not translation. Label the synthetic
    # source consistently so the fake providers never need a translation call.
    with integration[1].connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET metadata=jsonb_set(metadata,'{language}',%s::jsonb) WHERE document_id=%s",
                     (json.dumps(data.language), doc["document_id"]))
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    body = {"course_id": data.course_id, "activity_id": data.activity_id, "lesson_id": data.lesson_id,
            "lesson_job_id": target["job_id"]}
    return target, doc, row, body


@contextmanager
def b1_api(integration, lesson, *, local=True, principal=None, **config):
    settings = lesson[0].model_copy(update={"environment": "development" if local else "production",
                                           "local_test_mode": local, **config})
    principal = principal or replace(lesson[4], local_test=local)
    with api(integration, lesson) as client:
        app.dependency_overrides[ingress] = lambda: principal
        app.dependency_overrides[get_settings] = lambda: settings
        yield client


def events(lesson, job_id):
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value(), row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute("SELECT * FROM lesson_audit WHERE job_id=%s AND event_type='session_started'",
                            (job_id,)).fetchall()


def headers(signing, row, body, **updates):
    claims = assertion(client_id=row["client_id"], course_id=body["course_id"], activity_id=body["activity_id"],
                       lesson_id=body["lesson_id"], lesson_job_id=body["lesson_job_id"],
                       result_sha256=row["result"]["result_sha256"], **updates)
    return {"X-UPRAISE-Session-JWT": jwt.encode(claims, signing, algorithm="RS256")}


def test_local_draft_session_uses_actual_lesson_language_level_and_sources(integration, lesson, monkeypatch):
    monkeypatch.setattr(lesson[2].speech.client, "describe_voices", lambda **kwargs: {
        "Voices": [{"Id": "Bianca", "LanguageCode": "it-IT", "SupportedEngines": ["neural"]}],
    })
    target, doc, row, body = completed(integration, lesson, language="it", voice="Bianca",
                                       options={"learner_level": "beginner"})
    body.update(session_id=str(uuid4()), consent_scope=["questions", "telemetry"], language="IT")
    before = lesson[1].get(target["job_id"])
    with b1_api(integration, lesson) as client:
        response = client.post("/v1/sessions", json=body)
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        result = response.json()
        assert result["session_id"] == body["session_id"] and result["preview"]
        assert result["language"] == "it" and result["learner_level"] == "beginner"
        assert result["voice"] == "Bianca" and result["document_ids"] == [doc["document_id"]]
        assert "intelligenza artificiale" in result["ai_notice"]
        assert "capabilities" not in result
        response_schema = client.get("/openapi.json").json()["components"]["schemas"]["StartedSession"]
        assert "capabilities" not in response_schema["properties"]
        expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
        assert datetime.now(UTC) < expiry <= row["expires_at"]
        session = app.state.sessions.get(body["session_id"], row["client_id"])
        assert session.binding.consent_scope == ("questions", "telemetry")
        assert session.chat_cache == [] and session.current_chunk_index == 0
        assert session.chunk_ids
        session.chat_cache.append({"content": "PRIVATE_LEARNER_TEXT"})
        again = client.post("/v1/sessions", json=body)
        assert again.status_code == 200 and again.json() == result
        assert session.chat_cache == [{"content": "PRIVATE_LEARNER_TEXT"}]
        assert not any(key in result for key in ("chunks", "text", "ssml", "chat_cache", "consent_scope"))
    audit, = events(lesson, target["job_id"])
    assert audit["details"]["session_id"] == body["session_id"]
    assert "PRIVATE_LEARNER_TEXT" not in str(audit)
    assert lesson[1].get(target["job_id"]) == before
    # The next backend lifespan starts with an empty dictionary, even with durable audit.
    with b1_api(integration, lesson):
        assert not app.state.sessions._sessions


def test_no_jwt_local_requests_create_distinct_sessions_with_no_consent(integration, lesson):
    _, _, _, body = completed(integration, lesson)
    with b1_api(integration, lesson) as client:
        first = client.post("/v1/sessions", json=body)
        second = client.post("/v1/sessions", json=body)
        assert first.status_code == second.status_code == 201
        assert first.json()["session_id"] != second.json()["session_id"]
        session = app.state.sessions.get(first.json()["session_id"], lesson[4].client_id)
        assert session.binding.consent_scope == ()


def test_production_requires_verified_approval_and_caps_expiry_to_jwt(integration, lesson, signing):
    target, _, row, body = completed(integration, lesson)
    configuration = assertion_settings()
    token_expiry = int(time.time())+120
    assertion_headers = headers(signing, row, body, exp=token_expiry)
    with b1_api(integration, lesson, local=False, session_jwt_issuer=configuration.session_jwt_issuer,
                session_jwt_jwks_url=configuration.session_jwt_jwks_url,
                oauth_audience=configuration.oauth_audience) as client:
        response = client.post("/v1/sessions", json=body, headers=assertion_headers)
        assert response.status_code == 201, response.text
        assert not response.json()["preview"]
        assert datetime.fromisoformat(response.json()["expires_at"].replace("Z", "+00:00")).timestamp() == token_expiry
        assert client.post("/v1/sessions", json=body, headers=assertion_headers).status_code == 200
        assert client.post("/v1/sessions", json=body).status_code == 401
        assert client.post("/v1/sessions", json={**body, "consent_scope": []},
                           headers=assertion_headers).status_code == 422
        assert client.post("/v1/sessions", json={**body, "session_id": str(uuid4())},
                           headers=assertion_headers).status_code == 422
    assert len(events(lesson, target["job_id"])) == 1


@pytest.mark.parametrize("update,status,code", [
    ({"lesson_approved": False}, 403, "LESSON_APPROVAL_REQUIRED"),
    ({"client_id": "other-client"}, 403, "SESSION_CLIENT_MISMATCH"),
    ({"course_id": "other-course"}, 403, "SESSION_SCOPE_MISMATCH"),
    ({"lesson_job_id": str(uuid4())}, 403, "SESSION_SCOPE_MISMATCH"),
    ({"result_sha256": "0"*64}, 409, "LESSON_VERSION_MISMATCH"),
])
def test_production_session_assertion_cannot_change_scope_or_approval(integration, lesson, signing, update, status, code):
    target, _, row, body = completed(integration, lesson)
    configuration = assertion_settings()
    # Pass overrides separately to avoid duplicate arguments for tested claims.
    claims = assertion(client_id=row["client_id"], course_id=body["course_id"], activity_id=body["activity_id"],
                       lesson_id=body["lesson_id"], lesson_job_id=body["lesson_job_id"],
                       result_sha256=row["result"]["result_sha256"])
    claims.update(update)
    token = {"X-UPRAISE-Session-JWT": jwt.encode(claims, signing, algorithm="RS256")}
    with b1_api(integration, lesson, local=False, session_jwt_issuer=configuration.session_jwt_issuer,
                session_jwt_jwks_url=configuration.session_jwt_jwks_url,
                oauth_audience=configuration.oauth_audience) as client:
        response = client.post("/v1/sessions", json=body, headers=token)
        assert response.status_code == status and response.json()["detail"] == code
        assert not app.state.sessions._sessions
    assert not events(lesson, target["job_id"])


@pytest.mark.parametrize("update,status,code", [
    ({"lesson_id": "other-lesson"}, 409, "LESSON_CONTEXT_MISMATCH"),
    ({"activity_id": "other-activity"}, 409, "LESSON_CONTEXT_MISMATCH"),
    ({"language": "it"}, 409, "LESSON_LANGUAGE_MISMATCH"),
    ({"document_ids": [str(uuid4())]}, 403, "SESSION_DOCUMENT_SCOPE_DENIED"),
])
def test_session_input_cannot_select_different_context(integration, lesson, update, status, code):
    target, _, _, body = completed(integration, lesson)
    with b1_api(integration, lesson) as client:
        response = client.post("/v1/sessions", json={**body, **update})
        assert response.status_code == status and response.json()["detail"] == code
    assert not events(lesson, target["job_id"])


def test_session_client_and_course_isolation(integration, lesson):
    target, _, _, body = completed(integration, lesson)
    for principal, status in [
        (replace(lesson[4], client_id="other", local_test=True), 404),
        (replace(lesson[4], courses=("other",)), 403),
    ]:
        with b1_api(integration, lesson, principal=principal) as client:
            response = client.post("/v1/sessions", json=body)
            assert response.status_code == status, response.text
    assert not events(lesson, target["job_id"])


@pytest.mark.parametrize("kind,status,code", [
    ("running", 409, "LESSON_RESULT_NOT_READY"), ("expired", 410, "LESSON_OUTPUT_EXPIRED"),
    ("received", 410, "LESSON_OUTPUT_EXPIRED"), ("source_deleted", 409, "LESSON_DOCUMENT_UNAVAILABLE"),
    ("missing_audio", 503, "LESSON_AUDIO_UNAVAILABLE"), ("missing_result", 503, "LESSON_OUTPUT_UNAVAILABLE"),
    ("null_expiry", 503, "LESSON_OUTPUT_UNAVAILABLE"),
])
def test_session_does_not_open_unavailable_lessons(integration, lesson, kind, status, code):
    target, doc, _, body = completed(integration, lesson)
    with lesson[1].connect() as conn:
        if kind == "running":
            conn.execute("UPDATE lesson_jobs SET status='RUNNING' WHERE job_id=%s", (target["job_id"],))
        elif kind == "expired":
            conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                         (target["job_id"],))
        elif kind == "received":
            conn.execute("UPDATE lesson_jobs SET cleanup_requested_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
        elif kind == "source_deleted":
            conn.execute("UPDATE ingestion_jobs SET deletion_requested_at=clock_timestamp() WHERE document_id=%s",
                         (doc["document_id"],))
        elif kind == "null_expiry":
            conn.execute("UPDATE lesson_jobs SET expires_at=NULL WHERE job_id=%s", (target["job_id"],))
    folder = lesson[3].staging.job_dir(target["job_id"])
    if kind == "missing_result":
        (folder / "result.json").unlink()
    if kind == "missing_audio":
        next(folder.glob("*.wav")).unlink()
    with b1_api(integration, lesson) as client:
        response = client.post("/v1/sessions", json=body)
        assert response.status_code == status and response.json()["detail"] == code
        assert not app.state.sessions._sessions
    assert not events(lesson, target["job_id"])


def test_audit_outage_fails_closed_and_retry_can_succeed(integration, lesson, monkeypatch):
    target, _, _, body = completed(integration, lesson)
    body["session_id"] = str(uuid4())
    original = lesson[3].audit.session_started

    def offline(*args):
        raise psycopg.OperationalError("PRIVATE_DATABASE_ERROR")

    monkeypatch.setattr(lesson[3].audit, "session_started", offline)
    with b1_api(integration, lesson) as client:
        response = client.post("/v1/sessions", json=body)
        assert response.status_code == 503 and response.json()["detail"] == "AUDIT_STORAGE_UNAVAILABLE"
        assert "PRIVATE_DATABASE_ERROR" not in response.text
        assert not app.state.sessions._sessions and not app.state.sessions._pending
        monkeypatch.setattr(lesson[3].audit, "session_started", original)
        assert client.post("/v1/sessions", json=body).status_code == 201
    assert len(events(lesson, target["job_id"])) == 1


def test_retry_rechecks_source_and_retention_without_extending_it(integration, lesson):
    target, _, row, body = completed(integration, lesson)
    body["session_id"] = str(uuid4())
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()+interval '90 seconds' WHERE job_id=%s",
                     (target["job_id"],))
    row = lesson[1].get(target["job_id"])
    with b1_api(integration, lesson) as client:
        first = client.post("/v1/sessions", json=body)
        assert first.status_code == 201
        assert datetime.fromisoformat(first.json()["expires_at"].replace("Z", "+00:00")) == row["expires_at"]
        with lesson[1].connect() as conn:
            conn.execute("UPDATE lesson_jobs SET cleanup_requested_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
        assert client.post("/v1/sessions", json=body).status_code == 410


def test_same_lesson_id_different_generation_cannot_rebind_active_session(integration, lesson):
    _, _, _, first = completed(integration, lesson)
    _, _, _, second = completed(integration, lesson)
    first["session_id"] = second["session_id"] = str(uuid4())
    with b1_api(integration, lesson) as client:
        assert client.post("/v1/sessions", json=first).status_code == 201
        response = client.post("/v1/sessions", json=second)
        assert response.status_code == 409 and response.json()["detail"] == "SESSION_CONTEXT_CONFLICT"


def test_real_local_ingress_supports_b1_and_enforces_network(integration, lesson):
    target, doc, _, body = completed(integration, lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET client_id='local-test-client' WHERE job_id=%s", (target["job_id"],))
        conn.execute("UPDATE ingestion_jobs SET client_id='local-test-client' WHERE document_id=%s", (doc["document_id"],))
    local = lesson[0].model_copy(update={"environment": "development", "local_test_mode": True})
    app.dependency_overrides[get_settings] = lambda: local
    try:
        for peer, expected in [("127.0.0.1", 201), ("8.8.8.8", 403)]:
            with TestClient(app, client=(peer, 12345)) as client:
                app.state.lessons = lesson[3]
                response = client.post("/v1/sessions", json=body)
                assert response.status_code == expected, response.text
                if expected == 201:
                    assert response.headers["x-upraise-auth-mode"] == "local-test"
                    assert response.json()["preview"]
    finally:
        app.dependency_overrides.clear()


def test_jwt_subject_cannot_rebind_session_and_shorter_token_caps_retry(integration, lesson, signing):
    _, _, row, body = completed(integration, lesson)
    configuration = assertion_settings()
    session_id = str(uuid4())
    with b1_api(integration, lesson, local=False, session_jwt_issuer=configuration.session_jwt_issuer,
                session_jwt_jwks_url=configuration.session_jwt_jwks_url,
                oauth_audience=configuration.oauth_audience) as client:
        first = client.post("/v1/sessions", json=body, headers=headers(signing, row, body, session_id=session_id))
        assert first.status_code == 201
        other = client.post("/v1/sessions", json=body,
                            headers=headers(signing, row, body, session_id=session_id, sub="another-learner"))
        assert other.status_code == 409 and other.json()["detail"] == "SESSION_CONTEXT_CONFLICT"
        token_expiry = int(time.time())+60
        shorter = client.post("/v1/sessions", json=body,
                              headers=headers(signing, row, body, session_id=session_id, exp=token_expiry))
        assert shorter.status_code == 200
        assert datetime.fromisoformat(shorter.json()["expires_at"].replace("Z", "+00:00")).timestamp() == token_expiry


def test_far_future_token_cannot_overflow_or_extend_session_ttl(integration, lesson, signing):
    _, _, row, body = completed(integration, lesson)
    configuration = assertion_settings()
    with b1_api(integration, lesson, local=False, session_jwt_issuer=configuration.session_jwt_issuer,
                session_jwt_jwks_url=configuration.session_jwt_jwks_url,
                oauth_audience=configuration.oauth_audience) as client:
        before = datetime.now(UTC)
        response = client.post("/v1/sessions", json=body, headers=headers(signing, row, body, exp=10**30))
        assert response.status_code == 201, response.text
        expiry = datetime.fromisoformat(response.json()["expires_at"].replace("Z", "+00:00"))
        assert 1790 < (expiry-before).total_seconds() <= 1810

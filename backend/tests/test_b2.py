"""B2 threshold, session binding, consent, privacy and close-race checks; no AWS."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event
from uuid import uuid4

import jwt
import psycopg
import pytest
from test_b1 import assertion, assertion_settings
from test_b5 import http, setup_store

from upraise_ai.guards.ingress import Principal
from upraise_ai.main import app

pytest_plugins = ["test_b1"]


def setup_b2(*, consent=("telemetry",)):
    store, session, ends = setup_store()
    session.binding = replace(session.binding, consent_scope=consent)
    session.chunk_ids += (str(uuid4()),)
    body = {"lesson_id": session.binding.lesson_id, "chunk_id": session.chunk_ids[1], "cognitive_load_score": 0.7}
    return store, session, ends, body


@contextmanager
def b2_http(store, audits, *, audit=None, **kwargs):
    with http(store, **kwargs) as client:
        app.state.lessons.audit.chunk_ended = audit or (lambda session, chunk: audits.append({
            "session_id": str(session.response.session_id), "chunk_id": chunk}))
        yield client


@pytest.mark.parametrize("score,decision", [(0, "PROCEED"), (0.3, "PROCEED"), (0.5, "PROCEED"),
    (0.500001, "HIGH_COGNITIVE_LOAD"), (0.7, "HIGH_COGNITIVE_LOAD"), (1, "HIGH_COGNITIVE_LOAD")])
def test_b2_threshold_exact_contract_and_fixed_ttl(score, decision):
    store, session, _, body = setup_b2()
    audits = []
    deadline, expiry = session.deadline, session.response.expires_at
    with b2_http(store, audits) as client:
        path = f"/v1/sessions/{session.response.session_id}/chunk-end"
        for _ in range(2):
            result = client.post(path, json={**body, "cognitive_load_score": score})
            assert result.status_code == 200 and result.json() == {"decision": decision}
            assert result.headers["cache-control"] == "no-store"
        assert session.cognitive_load_state == score and session.current_chunk_index == 1
        assert (session.deadline, session.response.expires_at) == (deadline, expiry)
        assert len(audits) == 2
        assert all(set(event) == {"session_id", "chunk_id"} for event in audits)
        schema = client.get("/openapi.json").json()["components"]["schemas"]
        assert set(schema["ChunkEndInput"]["properties"]) == {"lesson_id", "chunk_id", "cognitive_load_score"}
        assert schema["ChunkEndInput"]["required"] == ["lesson_id", "chunk_id", "cognitive_load_score"]


@pytest.mark.parametrize("consent", [(), ("questions",)])
def test_b2_without_telemetry_consent_discards_score_and_proceeds(consent):
    store, session, _, body = setup_b2(consent=consent)
    with b2_http(store, []) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body)
        assert result.status_code == 200 and result.json() == {"decision": "PROCEED"}
        assert session.cognitive_load_state is None and session.current_chunk_index == 1


@pytest.mark.parametrize("change", [{"cognitive_load_score": -0.1}, {"cognitive_load_score": 1.1},
    {"cognitive_load_score": True}, {"cognitive_load_score": "0.7"}, {"cognitive_load_score": None},
    {"lesson_id": " "}, {"lesson_id": "x"*121}, {"chunk_id": "invalid"}, {"chunk_index": 0.7}])
def test_b2_invalid_input_keeps_runtime_unchanged(change):
    store, session, _, body = setup_b2()
    audits = []
    with b2_http(store, audits) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json={**body, **change})
        assert result.status_code == 422
        assert session.current_chunk_index == 0 and session.cognitive_load_state is None and not audits


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_b2_nonfinite_json_numbers_return_422(value):
    store, session, _, body = setup_b2()
    with b2_http(store, []) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end",
            content='{"lesson_id":"lesson-1","chunk_id":"'+body["chunk_id"]+'","cognitive_load_score":'+value+'}',
            headers={"Content-Type": "application/json"})
        assert result.status_code == 422 and session.cognitive_load_state is None


@pytest.mark.parametrize("field", ["lesson_id", "chunk_id", "cognitive_load_score"])
def test_b2_required_fields(field):
    store, session, _, body = setup_b2()
    del body[field]
    with b2_http(store, []) as client:
        assert client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body).status_code == 422


@pytest.mark.parametrize("change,status,detail", [({"lesson_id": "other-lesson"}, 409, "SESSION_LESSON_MISMATCH"),
    ({"chunk_id": str(uuid4())}, 404, "LESSON_CHUNK_NOT_FOUND")])
def test_b2_rejects_wrong_lesson_or_chunk(change, status, detail):
    store, session, _, body = setup_b2()
    audits = []
    with b2_http(store, audits) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json={**body, **change})
        assert result.status_code == status and result.json() == {"detail": detail}
        assert not audits and session.cognitive_load_state is None


@pytest.mark.parametrize("principal,status", [
    (Principal("foreign", (), frozenset({"sessions:write"}), local_test=True), 404),
    (Principal("client", ("other-course",), frozenset({"sessions:write"})), 403),
    (Principal("client", (), frozenset(), local_test=True), 403)])
def test_b2_client_course_and_scope_isolation(principal, status):
    store, session, _, body = setup_b2()
    with b2_http(store, [], principal=principal) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body)
        assert result.status_code == status and session.cognitive_load_state is None


def test_b2_signed_session_uses_verified_consent_and_exact_binding(signing):
    store, session, _, body = setup_b2()
    session.binding = replace(session.binding, preview=False, subject="pseudonymous-learner")
    claims = assertion(session_id=str(session.response.session_id), client_id="client",
        lesson_job_id=session.binding.lesson_job_id, consent_scope=["telemetry"])
    principal = Principal("client", ("course-1",), frozenset({"sessions:write"}))
    with b2_http(store, [], principal=principal, settings=assertion_settings()) as client:
        path = f"/v1/sessions/{session.response.session_id}/chunk-end"
        assert client.post(path, json=body).status_code == 401
        wrong = jwt.encode({**claims, "lesson_job_id": str(uuid4())}, signing, algorithm="RS256")
        assert client.post(path, json=body, headers={"X-UPRAISE-Session-JWT": wrong}).status_code == 403
        valid = jwt.encode(claims, signing, algorithm="RS256")
        result = client.post(path, json=body, headers={"X-UPRAISE-Session-JWT": valid})
        assert result.status_code == 200 and result.json() == {"decision": "HIGH_COGNITIVE_LOAD"}


def test_b2_b5_and_expiry_clear_score_and_reject_later_decisions():
    for reason in ("COMPLETED", "TIMEOUT"):
        store, session, _, body = setup_b2()
        with b2_http(store, []) as client:
            path = f"/v1/sessions/{session.response.session_id}"
            assert client.post(path+"/chunk-end", json=body).status_code == 200
            if reason == "TIMEOUT":
                session.deadline = 0
                store.prune()
            else:
                assert client.post(path+"/end", json={"reason": reason}).status_code == 204
            assert session.cognitive_load_state is None
            assert client.post(path+"/chunk-end", json=body).status_code == 410


def test_b2_close_during_audit_cannot_restore_score_or_deliver_decision():
    store, session, _, body = setup_b2()
    entered, release = Event(), Event()
    def audit(*args):
        entered.set()
        assert release.wait(5)
    with b2_http(store, [], audit=audit) as client, ThreadPoolExecutor() as pool:
        future = pool.submit(client.post, f"/v1/sessions/{session.response.session_id}/chunk-end", json=body)
        assert entered.wait(5)
        store.end(session.response.session_id, "client", "STOPPED_BY_LEARNER")
        release.set()
        assert future.result().status_code == 410
        assert session.cognitive_load_state is None and session.chunk_ids == ()


def test_b2_audit_outage_keeps_score_uncommitted():
    store, session, _, body = setup_b2()
    def offline(*args):
        raise psycopg.OperationalError("Invented outage")
    with b2_http(store, [], audit=offline) as client:
        result = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body)
        assert result.status_code == 503 and result.json() == {"detail": "AUDIT_STORAGE_UNAVAILABLE"}
        assert session.cognitive_load_state is None and session.current_chunk_index == 0


def test_b2_unknown_and_restart_lost_sessions_do_not_decide():
    store, session, _, body = setup_b2()
    with b2_http(store, []) as client:
        assert client.post(f"/v1/sessions/{uuid4()}/chunk-end", json=body).status_code == 404
        assert client.post("/v1/sessions/not-a-uuid/chunk-end", json=body).status_code == 422
        store.clear()
        assert client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body).status_code == 404

"""Explicit development authentication and unchanged production access controls."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request
from test_a1 import metadata, pdf_bytes

from upraise_ai.core.config import Settings, get_settings
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.storage.files import StagingStore
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.jobs import JobService
from upraise_ai.workflows.submission import SubmissionService

pytest_plugins = ["test_a1_integration"]


def request(peer="127.0.0.1"):
    return Request({"type": "http", "method": "POST", "path": "/v1/documents",
                    "client": (peer, 12345), "headers": []})


def test_auth_defaults_are_production_and_no_implicit_fallback(monkeypatch):
    monkeypatch.delenv("UPRAISE_ENVIRONMENT", raising=False)
    monkeypatch.delenv("UPRAISE_LOCAL_TEST_MODE", raising=False)
    settings = Settings()
    assert settings.environment == "production" and settings.local_test_mode is False
    for settings in (settings, Settings(environment="development")):
        with pytest.raises(HTTPException) as denied:
            ingress(request(), settings)
        assert denied.value.status_code == 503 and denied.value.detail == "INGRESS_NOT_CONFIGURED"
    with pytest.raises(ValidationError, match="requires environment=development"):
        Settings(local_test_mode=True, environment="production")


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "172.20.0.5"])
def test_opt_in_allows_local_testing_courses_and_limited_scopes(peer):
    settings = Settings(environment="development", local_test_mode=True)
    principal = ingress(request(peer), settings)
    assert principal.client_id == "local-test-client" and principal.local_test
    principal.require_course("any-course-entered-in-the-testbench")
    for scope in ("documents:read", "documents:write", "jobs:read", "jobs:write"):
        principal.require_scope(scope)
    with pytest.raises(HTTPException):
        principal.require_scope("admin:write")
    real = Principal("middleware", ("allowed",), frozenset())
    assert not real.can_access_course("other")
    assert not Principal("middleware", ("*",), frozenset()).can_access_course("other")


@pytest.mark.parametrize("peer", ["8.8.8.8", "0.0.0.0", "169.254.1.2", "224.0.0.1", "unknown"])
def test_local_mode_rejects_nonlocal_and_invalid_peers(peer):
    with pytest.raises(HTTPException) as denied:
        ingress(request(peer), Settings(environment="development", local_test_mode=True))
    assert denied.value.status_code == 403 and denied.value.detail == "LOCAL_TEST_NETWORK_REQUIRED"


def test_real_ingress_local_branch_supports_a1_a2_a4_without_auth_override(integration):
    settings, jobs, audit, vectors, worker, _ = integration
    local = Settings(environment="development", local_test_mode=True)
    raw = pdf_bytes()
    data = metadata(raw).model_dump(exclude={"source_filename"}, exclude_none=True)
    data["course_id"] = "a-new-local-course"
    app.dependency_overrides[get_settings] = lambda: local
    assert ingress not in app.dependency_overrides
    try:
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            app.state.submission = SubmissionService(settings, jobs, StagingStore(settings.data_dir))
            app.state.documents = DocumentService(jobs, vectors, audit)
            app.state.jobs = JobService(jobs, vectors, audit, worker.staging)
            accepted = client.post("/v1/documents", data=data,
                                   files={"file": ("local-test.pdf", raw, "application/pdf")})
            assert accepted.status_code == 202, accepted.text
            assert set(accepted.json()) == {"document_id"}
            assert accepted.headers["x-upraise-auth-mode"] == "local-test"
            document_id = accepted.json()["document_id"]
            status = accepted.headers["location"]
            assert client.get(status).json()["status"] == "QUEUED"
            worker.process(jobs.claim(settings.lease_seconds))
            assert client.get(status).json()["status"] == "DONE"
            rows = client.get("/v1/documents", params={"course_id": data["course_id"]})
            assert rows.status_code == 200
            assert rows.json()[0]["document_id"] == document_id
            assert rows.json()[0]["version"] == "1" and rows.json()[0]["chunk_count"] == 3
            received = client.delete(status, params={"reason": "RECEIVED"})
            assert received.status_code == 204 and received.content == b""
            assert client.get(status).json()["acknowledged"]
            deleted = client.delete("/v1/documents/" + document_id)
            assert deleted.status_code == 204 and deleted.content == b""
            assert client.get("/v1/documents", params={"course_id": data["course_id"]}).json() == []
            assert client.get(status).status_code == 404
    finally:
        app.dependency_overrides.clear()

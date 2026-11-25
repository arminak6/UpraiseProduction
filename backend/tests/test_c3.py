"""C3 contract, operator boundaries, B2 bypass and fail-closed runtime reads; no providers."""


import psycopg
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_b2 import b2_http, setup_b2

from upraise_ai.api.operations import router
from upraise_ai.core.config import Settings, get_settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.guards.operations import control_state, feature_enabled, require_feature
from upraise_ai.schemas.operations import KillSwitchInput
from upraise_ai.storage.controls import ControlsRepository
from upraise_ai.workflows.controls import ControlService


@pytest.mark.parametrize("body", [
    {}, {"scope": "other", "feature": "all", "enabled": True},
    {"scope": "global", "feature": "other", "enabled": True},
    {"scope": "global", "feature": "all", "enabled": "true"},
    {"scope": "global", "feature": "all", "enabled": 1},
    {"scope": "global", "feature": "all", "enabled": True, "course_id": "course"},
    {"scope": "global", "feature": "all", "enabled": True, "unknown": "private"},
    {"scope": "course_id", "feature": "all", "enabled": True},
    *[{"scope": "course_id", "feature": "all", "enabled": True, "course_id": value}
      for value in ("", " ", " padded", "x" * 121)],
])
def test_c3_rejects_invalid_contract(body):
    with pytest.raises(ValidationError):
        KillSwitchInput(**body)


def test_c3_admin_scope_course_claim_and_local_opt_in_are_required():
    service = ControlService(Settings(jobs_dsn="postgresql://invented"), repository=object())
    global_change = KillSwitchInput(scope="global", feature="all", enabled=True)
    course_change = KillSwitchInput(scope="course_id", course_id="foreign", feature="answers", enabled=True)
    for data, principal in (
        (global_change, Principal("mw", ())),
        (course_change, Principal("admin", ("allowed",), frozenset({"admin:operations"}))),
        (global_change, Principal("local", (), frozenset({"admin:operations"}), local_test=True)),
    ):
        with pytest.raises(HTTPException) as error:
            service.update(data, principal)
        assert error.value.status_code == 403


def test_c3_http_denies_normal_callers_and_local_admin_is_not_enabled_by_default():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(jobs_dsn="", audit_dsn="")
    app.dependency_overrides[ingress] = lambda: Principal("mw", ())
    with TestClient(app) as client:
        assert client.put("/v1/admin/kill-switch", json={"scope": "global", "feature": "all", "enabled": True}).status_code == 403
    assert not Settings().operations_local_admin
    with pytest.raises(ValidationError):
        Settings(environment="production", local_test_mode=False, operations_local_admin=True)
    with pytest.raises(ValidationError):
        Settings(environment="development", local_test_mode=False, operations_local_admin=True)


def test_c3_actual_local_ingress_requires_explicit_opt_in():
    app = FastAPI()
    app.get("/principal")(lambda principal=Depends(ingress): sorted(principal.scopes))
    settings = Settings(environment="development", local_test_mode=True)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert "admin:operations" not in client.get("/principal").json()
        settings.operations_local_admin = True
        assert "admin:operations" in client.get("/principal").json()


def test_c3_state_combines_deployment_global_and_course_without_overrides():
    settings = Settings(operations_disabled_features={"stt"}, operations_kill_switch_courses={"static"})
    rows = [{"scope_key": "", "feature": "answers"}, {"scope_key": "closed", "feature": "all"},
            {"scope_key": "generation-only", "feature": "generation"}]
    state = control_state(settings, rows=rows)
    assert not state.enabled("answers", "ordinary") and not state.enabled("stt", "ordinary")
    assert state.enabled("generation", "ordinary")
    assert state.killed("closed") and state.killed("static") and not state.killed()
    assert state.blocked_courses({"generation", "tts"}) == ["closed", "generation-only", "static"]
    assert state.blocked_courses(set()) == ["closed", "static"]


def test_c3_missing_control_storage_never_falls_back_to_enabled(monkeypatch):
    def offline(*args):
        raise psycopg.OperationalError("PRIVATE PASSWORD")
    monkeypatch.setattr(ControlsRepository, "read", offline)
    settings = Settings(jobs_dsn="postgresql://invented")
    for action in (lambda: feature_enabled(settings, "answers"), lambda: require_feature(settings, "generation")):
        with pytest.raises(IngestionError, match="OPERATIONS_STORAGE_UNAVAILABLE") as error:
            action()
        assert error.value.status == 503
        assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("feature", ["adaptive_levels", "all"])
def test_c3_b2_disabled_adaptation_discards_score_and_preserves_pinned_level(monkeypatch, feature):
    store, session, _, body = setup_b2()
    settings = Settings(environment="development", local_test_mode=True, jobs_dsn="postgresql://invented")
    rows = [{"scope_key": session.binding.course_id, "feature": feature}]
    monkeypatch.setattr(ControlsRepository, "read", lambda self: rows)
    level = session.binding.learner_level
    session.cognitive_load_state = 0.9
    with b2_http(store, [], settings=settings) as client:
        response = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body)
        assert response.status_code == 200 and response.json() == {"decision": "PROCEED"}
        assert session.cognitive_load_state is None and session.binding.learner_level == level
        rows.clear()
        assert client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body).json() == {
            "decision": "HIGH_COGNITIVE_LOAD"}


def test_c3_b2_switch_rechecked_after_audit_and_storage_failure_keeps_state_uncommitted(monkeypatch):
    store, session, _, body = setup_b2()
    settings = Settings(environment="development", local_test_mode=True, jobs_dsn="postgresql://invented")
    rows = []
    monkeypatch.setattr(ControlsRepository, "read", lambda self: rows)
    with b2_http(store, [], settings=settings,
                 audit=lambda *_: rows.append({"scope_key": "", "feature": "adaptive_levels"})) as client:
        assert client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body).json() == {"decision": "PROCEED"}
    store, session, _, body = setup_b2()
    def offline(*args):
        raise psycopg.OperationalError("private")
    monkeypatch.setattr(ControlsRepository, "read", offline)
    with b2_http(store, [], settings=settings) as client:
        response = client.post(f"/v1/sessions/{session.response.session_id}/chunk-end", json=body)
        assert response.status_code == 503 and response.json() == {"detail": "OPERATIONS_STORAGE_UNAVAILABLE"}
        assert session.current_chunk_index == 0 and session.cognitive_load_state is None

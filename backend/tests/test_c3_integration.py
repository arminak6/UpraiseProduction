"""C3 shared persistence, atomic audit, recovery, rate limits and worker admission with disposable storage."""

import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_a1 import metadata, pdf_bytes

from upraise_ai.api.operations import router
from upraise_ai.core.config import get_settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.guards.operations import (
    blocked_generation_courses,
    control_state,
    require_ai,
    require_feature,
)
from upraise_ai.schemas.operations import KillSwitchInput
from upraise_ai.services.operations import OperationsMonitor
from upraise_ai.services.versions import VersionCatalog
from upraise_ai.storage.audit import AuditRepository
from upraise_ai.storage.controls import ControlsRepository
from upraise_ai.storage.lessons import LessonRepository
from upraise_ai.workers.lessons import LessonWorker
from upraise_ai.workflows.controls import ControlService
from upraise_ai.workflows.lessons import LessonService
from upraise_ai.workflows.submission import SubmissionService

pytest_plugins = ["test_a1_integration"]


@pytest.fixture
def controls(integration):
    original, jobs, _, vectors, worker, _ = integration
    audit_schema = "test_c3_" + uuid4().hex
    base = original.audit_dsn.get_secret_value()
    with psycopg.connect(base) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(audit_schema)))
    settings = original.model_copy(update={"audit_dsn": type(original.audit_dsn)(
        make_conninfo(base, options=f"-c search_path={audit_schema}"))})
    audit = AuditRepository(settings.audit_dsn.get_secret_value())
    audit.migrate()
    repository = ControlsRepository(settings)
    service = ControlService(settings, repository=repository, catalog=VersionCatalog(settings))
    principal = Principal("admin", ("course-1",), frozenset({"admin:operations", "jobs:read"}))
    try:
        yield SimpleNamespace(**locals())
    finally:
        with psycopg.connect(base) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(audit_schema)))


def change(feature="answers", enabled=True, course="course-1"):
    return KillSwitchInput(scope="global" if course is None else "course_id", course_id=course,
                           feature=feature, enabled=enabled)


@contextmanager
def api(r):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[ingress] = lambda: r.principal
    app.dependency_overrides[get_settings] = lambda: r.settings
    app.state.versions = r.service.catalog
    app.state.operations = OperationsMonitor(r.settings)
    @app.exception_handler(IngestionError)
    def error(request, exc):
        return JSONResponse({"detail": exc.code}, status_code=exc.status)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.state.operations.close()


def test_c3_integration_http_new_state_c2_and_fresh_process_observe_same_controls(controls):
    r = controls
    with api(r) as client:
        reply = client.put("/v1/admin/kill-switch", json=change().model_dump())
        assert reply.status_code == 200, reply.text
        assert reply.headers["cache-control"] == "no-store"
        value = reply.json()
        assert value["enabled"] and not value["features_enabled"]["answers"] and not value["audit_pending"]
        assert value["features_enabled"]["generation"] and not value["kill_switch_active"]
        assert not client.get("/v1/status?course_id=course-1").json()["features"]["answers"]["enabled"]
        assert client.get("/v1/status").json()["features"]["answers"]["enabled"]
        process = subprocess.run([sys.executable, "-c",
            "from upraise_ai.core.config import get_settings; "
            "from upraise_ai.guards.operations import feature_enabled; "
            "print(feature_enabled(get_settings(),'answers','course-1'))"],
            env={**os.environ, "UPRAISE_JOBS_DSN": r.settings.jobs_dsn.get_secret_value()},
            capture_output=True, text=True, timeout=15)
        assert process.returncode == 0 and process.stdout.strip() == "False"
        assert ControlsRepository(r.settings).read() == [{"scope_key": "course-1", "feature": "answers"}]
        released = client.put("/v1/admin/kill-switch", json=change(enabled=False).model_dump())
        assert released.status_code == 200 and released.json()["features_enabled"]["answers"]
    with psycopg.connect(r.settings.audit_dsn.get_secret_value()) as conn:
        events = conn.execute("SELECT event_type,details FROM admin_audit ORDER BY created_at").fetchall()
    assert len(events) == 2 and all(event[0] == "KILL_SWITCH_UPDATED" for event in events)
    assert events[0][1]["actor_client_id"] == "admin" and events[1][1]["previous_enabled"]
    assert "version_snapshot" in events[0][1]
    assert all(key not in json.dumps(events) for key in ("aws_secret_access_key", "cognitive_load_score", "Bearer"))


def test_c3_integration_global_course_and_environment_disables_do_not_override_each_other(controls):
    r = controls
    r.service.update(change("all", course=None), r.principal)
    reply = r.service.update(change(enabled=False), r.principal)
    assert reply.kill_switch_active and not any(reply.features_enabled.values())
    with pytest.raises(IngestionError, match="AI_DISABLED"):
        require_ai(r.settings, "course-1")
    r.service.update(change("all", enabled=False, course=None), r.principal)
    r.settings.operations_disabled_features = {"answers"}
    reply = r.service.update(change(enabled=False), r.principal)
    assert not reply.features_enabled["answers"] and reply.features_enabled["generation"]
    r.service.update(change("all"), r.principal)
    with pytest.raises(IngestionError, match="AI_DISABLED"):
        require_feature(r.settings, "generation", "course-1")
    assert control_state(r.settings).enabled("generation", "ordinary")


def test_c3_integration_audit_insert_failure_rolls_back_control_change(controls):
    r = controls
    with r.repository.connect() as conn:
        conn.execute("""CREATE FUNCTION reject_control_event() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'invented audit failure'; END $$""")
        conn.execute("""CREATE TRIGGER reject_control_event BEFORE INSERT ON operational_control_events
            FOR EACH ROW EXECUTE FUNCTION reject_control_event()""")
    with api(r) as client:
        reply = client.put("/v1/admin/kill-switch", json=change().model_dump())
        assert reply.status_code == 503 and reply.json() == {"detail": "OPERATIONS_STORAGE_UNAVAILABLE"}
    assert not r.repository.read()


def test_c3_integration_audit_outage_has_durable_event_and_idempotent_recovery(controls, monkeypatch):
    r = controls
    original = AuditRepository.admin_control_changed
    def outage(*args):
        raise psycopg.OperationalError("PRIVATE DATABASE PASSWORD")
    monkeypatch.setattr(AuditRepository, "admin_control_changed", outage)
    reply = r.service.update(change(), r.principal)
    assert reply.audit_pending and not reply.features_enabled["answers"]
    assert "PRIVATE" not in reply.model_dump_json()
    with r.repository.connect() as conn:
        event = conn.execute("SELECT * FROM operational_control_events").fetchone()
    assert event["projected_at"] is None
    monkeypatch.setattr(AuditRepository, "admin_control_changed", original)
    ControlsRepository(r.settings).project_pending()
    assert r.repository.project(event)
    with psycopg.connect(r.settings.audit_dsn.get_secret_value()) as conn:
        assert conn.execute("SELECT count(*) FROM admin_audit WHERE event_id=%s", (reply.audit_event_id,)).fetchone()[0] == 1
    with r.repository.connect() as conn:
        assert conn.execute("SELECT projected_at FROM operational_control_events").fetchone()["projected_at"]


def test_c3_integration_rate_limit_is_shared_atomic_and_does_not_change_state(controls):
    r = controls
    for _ in range(29):
        r.repository.update(change(), "rate-test", {})
    def write(_):
        try:
            ControlsRepository(r.settings).update(change(), "rate-test", {})
            return 200
        except HTTPException as error:
            assert error.headers == {"Retry-After": "60"}
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [200, 429]
    with pytest.raises(HTTPException) as error:
        r.repository.update(change(enabled=False), "rate-test", {})
    assert error.value.status_code == 429 and r.repository.read()
    r.repository.update(change(enabled=False), "another-admin", {})
    assert not r.repository.read()


def test_c3_integration_course_bound_and_control_storage_outage(controls):
    r = controls
    with r.repository.connect() as conn:
        conn.execute("""INSERT INTO operational_controls(scope_key,feature)
            SELECT 'course-'||i,'answers' FROM generate_series(1,1000) i""")
    with pytest.raises(HTTPException) as error:
        r.repository.update(change(course="new-course"), "admin", {})
    assert error.value.status_code == 409
    r.repository.update(change(course="course-1", enabled=False), "admin", {})
    r.repository.update(change(course="new-course"), "admin", {})
    with r.repository.connect() as conn:
        conn.execute("ALTER TABLE operational_controls RENAME TO unavailable_controls")
    with pytest.raises(IngestionError, match="OPERATIONS_STORAGE_UNAVAILABLE"):
        require_feature(r.settings, "answers", "course-1")


def test_c3_integration_worker_queues_and_claim_races_preserve_attempts(controls):
    r = controls
    source = SubmissionService(r.settings, r.jobs, r.worker.staging).submit(
        metadata(pdf_bytes()), io.BytesIO(pdf_bytes()), r.principal, "c3-source")
    r.service.update(change("all"), r.principal)
    controls_state = control_state(r.settings)
    assert r.jobs.claim(r.settings.lease_seconds, excluded_courses=controls_state.blocked_courses(set())) is None
    assert r.jobs.get(source["job_id"])["attempts"] == 0
    # Simulate a switch activated after claim but before the child admits provider work.
    job = r.jobs.claim(r.settings.lease_seconds)
    r.worker.process(job)
    paused = r.jobs.get(source["job_id"])
    assert paused["status"] == "QUEUED" and paused["attempts"] == 0 and paused["step"] == "CONTROL_PAUSED"
    r.service.update(change("all", enabled=False), r.principal)
    lesson_jobs = LessonRepository(r.settings.jobs_dsn.get_secret_value())
    accepted = lesson_jobs.accept("admin", "c3-lesson", "fingerprint",
        {"course_id": "course-1", "document_ids": [source["document_id"]]}, r.settings.lesson_signature)
    r.service.update(change("generation"), r.principal)
    assert lesson_jobs.claim(r.settings.lease_seconds, r.settings.lesson_signature,
                             excluded_courses=blocked_generation_courses(r.settings)) is None
    assert lesson_jobs.get(accepted["job_id"])["attempts"] == 0
    lesson_job = lesson_jobs.claim(r.settings.lease_seconds, r.settings.lesson_signature)
    worker = LessonWorker(r.settings, jobs=lesson_jobs, documents=r.jobs, audit=r.audit, vectors=r.vectors)
    worker.process(lesson_job)
    paused = lesson_jobs.get(accepted["job_id"])
    assert paused["status"] == "QUEUED" and paused["attempts"] == 0 and paused["step"] == "CONTROL_PAUSED"
    with pytest.raises(IngestionError, match="GENERATION_DISABLED"):
        LessonService(r.settings, None, None, None, None).submit(SimpleNamespace(course_id="course-1"), r.principal)

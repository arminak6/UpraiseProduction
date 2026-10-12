"""C5 is deterministic, secret-free metadata; it never probes dependencies."""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from upraise_ai.api.operations import router
from upraise_ai.core.config import Settings, get_settings
from upraise_ai.core.versions import cognitive_load_decision
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.services import versions
from upraise_ai.services.versions import VersionCatalog, digest_files, fingerprint, prompt_version


def test_c5_matches_effective_config_and_policy_not_cloud_readiness():
    settings = Settings(lesson_generation_model_id="invented-generator", polly_engine="standard")
    body = VersionCatalog(settings).snapshot().model_dump(mode="json")
    assert body["application"]["version"] == "0.1.0"
    assert body["application"]["build_id"].startswith("sha256:")
    assert body["models"]["lesson_generator"]["model_id"] == "invented-generator"
    assert body["models"]["lesson_finalizer"]["model_id"] == "invented-generator"
    assert body["models"]["tts"]["engine"] == "standard"
    assert body["models"]["stt"]["version_status"] == "PROVIDER_MANAGED"
    assert body["models"]["stt"]["model_id"] is None
    assert "lesson_visual" not in body["models"]
    assert body["pipelines"]["ingestion"]["version"] == settings.pipeline_signature
    assert body["pipelines"]["generation"]["version"] == settings.lesson_signature
    assert body["pipelines"]["aligned_generation"]["version"] == settings.lesson_v2_signature
    assert body["pipelines"]["aligned_generation"]["parameters"]["math_validation"] == "source-review-math-markup-v1"
    assert body["prompts"]["alignment"]["version"] == "aligned-teaching-v6"
    assert body["pipelines"]["aligned_generation"]["parameters"]["identity_recovery"] == "aligned-unit-id-recovery-v1"
    assert body["pipelines"]["aligned_generation"]["parameters"]["review_strategy"] == "original-three-role-v1"
    assert body["pipelines"]["aligned_generation"]["parameters"]["post_edit_material_review"] == "deferred"
    assert body["pipelines"]["aligned_generation"]["parameters"]["response_format"] == "aligned-json-strings-v1"
    assert body["cognitive_load"]["fusion_rule"] == {"status": "NOT_IMPLEMENTED", "version": None}
    policy = body["cognitive_load"]["threshold_method"]
    assert policy == {"name": "fixed_threshold", "version": "test-v1", "value": 0.5,
                      "comparison": ">", "telemetry_consent_required": True}
    assert cognitive_load_decision(policy["value"]) == "PROCEED"
    assert cognitive_load_decision(policy["value"] + 0.01) == "HIGH_COGNITIVE_LOAD"
    assert all(item["status"] == "AVAILABLE" and len(item["sha256"]) == 64 for item in body["prompts"].values())
    assert body["prompts"]["translation"]["version"] == "finalized-lesson-translation-v3"
    assert body["prompts"]["math_speech"]["version"] == "math-narration-v4.2-semantic-plan"
    assert body.pop("configuration_id") == fingerprint(body)


def test_c5_deterministic_and_snapshots_cannot_mutate_catalog():
    catalog = VersionCatalog(Settings())
    first = catalog.snapshot()
    assert first == VersionCatalog(Settings()).snapshot()
    first.models["lesson_generator"].model_id = "changed-by-caller"
    first.prompts["translation"].version = "changed-by-caller"
    assert catalog.snapshot() == VersionCatalog(Settings()).snapshot()


@pytest.mark.parametrize("update", [
    {"lesson_generation_model_id": "invented-new-model"}, {"lesson_review_model_id": "invented-review"},
    {"embedding_dimension": 512}, {"enable_ocr": False}, {"polly_engine": "standard"},
    {"interrupt_source_characters": 30000}, {"aws_region": "eu-west-1"},
])
def test_relevant_settings_change_configuration_id(update):
    original = Settings()
    assert VersionCatalog(original).snapshot().configuration_id != VersionCatalog(original.model_copy(update=update)).snapshot().configuration_id


def test_c5_never_exports_or_hashes_secrets_paths_or_course_controls(tmp_path):
    from pydantic import SecretStr
    original = Settings()
    changed = original.model_copy(update={"jobs_dsn": SecretStr("invented-database-secret"),
        "audit_dsn": SecretStr("invented-audit-secret"), "callback_client_secret": SecretStr("invented-client-secret"),
        "aws_credentials_file": tmp_path / "invented-private-path", "data_dir": tmp_path,
        "oauth_clients": ["invented-private-client"], "operations_kill_switch_courses": {"invented-private-course"}})
    old, new = VersionCatalog(original).snapshot(), VersionCatalog(changed).snapshot()
    assert old == new
    wire = new.model_dump_json()
    assert "invented-" not in wire
    assert "credentials" not in wire and "jobs_dsn" not in wire


def test_source_hash_tracks_prompt_text_and_normalizes_line_endings(tmp_path):
    first = tmp_path / "a.py"
    first.write_bytes(b'PROMPT = "one"\r\n')
    digest = digest_files(tmp_path, ["a.py"])
    first.write_bytes(b'PROMPT = "one"\n')
    assert digest_files(tmp_path, ["a.py"]) == digest
    first.write_bytes(b'PROMPT = "two"\n')
    assert digest_files(tmp_path, ["a.py"]) != digest
    assert digest_files(tmp_path, ["missing.py"]) is None
    missing = prompt_version(None, ("missing.py",))
    assert missing.status == "UNAVAILABLE" and missing.sha256 is None and missing.version is None


def test_build_prompt_or_threshold_changes_change_fingerprint(monkeypatch):
    original = VersionCatalog(Settings()).snapshot().configuration_id
    with monkeypatch.context() as context:
        context.setattr(versions, "build_identity", lambda: ("sha256:invented-new-build", "source_sha256"))
        assert VersionCatalog(Settings()).snapshot().configuration_id != original
    with monkeypatch.context() as context:
        context.setattr(versions, "COGNITIVE_LOAD_THRESHOLD", 0.6)
        assert VersionCatalog(Settings()).snapshot().configuration_id != original
    with monkeypatch.context() as context:
        context.setattr(versions, "prompt_version", lambda *args: prompt_version("invented-version", args[1]))
        assert VersionCatalog(Settings()).snapshot().configuration_id != original


def version_app(scopes=("jobs:read",)):
    app = FastAPI()
    app.include_router(router)
    app.state.versions = VersionCatalog(Settings())
    app.dependency_overrides[ingress] = lambda: Principal("operator", (), frozenset(scopes))
    return app


def test_authorized_c5_needs_no_session_course_or_database_and_no_provider_calls():
    app = version_app()
    with patch("psycopg.connect", side_effect=AssertionError("C5 must not use storage")), \
            patch("boto3.Session", side_effect=AssertionError("C5 must not use providers")), TestClient(app) as client:
        first = client.get("/v1/version")
        assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
        assert client.get("/v1/version").json() == first.json()
        assert not app.openapi()["paths"]["/v1/version"]["get"].get("parameters")
        assert "requestBody" not in app.openapi()["paths"]["/v1/version"]["get"]
        assert "session_id" not in json.dumps(first.json())


def test_scope_denied_and_unconfigured_ingress_fail_closed():
    with TestClient(version_app(scopes=("sessions:write",))) as client:
        assert client.get("/v1/version").status_code == 403
    app = version_app()
    app.dependency_overrides.pop(ingress)
    app.dependency_overrides[get_settings] = lambda: Settings()
    with TestClient(app) as client:
        assert client.get("/v1/version").status_code == 503


def test_explicit_local_test_mode_works_without_learner_jwt():
    app = version_app()
    app.dependency_overrides.pop(ingress)
    app.dependency_overrides[get_settings] = lambda: Settings(environment="development", local_test_mode=True)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert client.get("/v1/version").status_code == 200

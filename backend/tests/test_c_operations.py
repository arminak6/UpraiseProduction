"""C1/C2 dependency outages, stale evidence, access boundaries and actual SDK events."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_b4 import run, wav
from test_b4 import runtime as interrupt_runtime

from upraise_ai.api.operations import router
from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.guards.operations import blocked_generation_courses, require_feature
from upraise_ai.services.aws import aws_session
from upraise_ai.services.operations import CORE, PROVIDERS, OperationsMonitor, dependency
from upraise_ai.storage.operations import OperationsRepository, configuration_id, observe
from upraise_ai.workflows.lessons import LessonService
from upraise_ai.workflows.submission import SubmissionService


@pytest.fixture
def runtime():
    return interrupt_runtime.__wrapped__()


@pytest.fixture
def operational(tmp_path):
    credential_file = tmp_path / "credentials.json"
    credential_file.write_text(json.dumps({"aws_access_key_id": "invented-key", "aws_secret_access_key": "invented-secret"}))
    settings = Settings(aws_credentials_file=credential_file, data_dir=tmp_path)
    rows = [dict(component=key, state="AVAILABLE", reason_code="PROVIDER_CALL_SUCCEEDED",
                 observed_at=datetime.now(UTC), age_seconds=0) for key in (*PROVIDERS, "lesson_worker")]
    calls, clock = [], [100.0]

    def probe(key):
        calls.append(key)
        return dependency("AVAILABLE", "REACHABLE")

    monitor = OperationsMonitor(settings, probes={key: lambda key=key: probe(key) for key in CORE},
                                repository=SimpleNamespace(read=lambda: rows), clock=lambda: clock[0])
    monitor.refresh()
    try:
        yield SimpleNamespace(**locals())
    finally:
        monitor.close()


def test_c1_c2_ready_and_repeated_reads_are_cached_without_aws(operational):
    r = operational
    assert r.monitor.snapshot().status == "READY"
    assert len(r.calls) == 4
    for _ in range(20):
        status = r.monitor.status("course")
        assert status.answers_available and status.stt_available and status.tts_available and status.generation_available
        assert not status.kill_switch_active
    assert len(r.calls) == 4


def test_provider_not_seen_expired_or_bad_clock_is_unknown(operational):
    r = operational
    r.rows.clear()
    r.monitor.refresh()
    assert r.monitor.snapshot().status == "DEGRADED"
    assert r.monitor.status().answers_available is None
    r.rows.append(dict(component="bedrock_generation", state="AVAILABLE", reason_code="PROVIDER_CALL_SUCCEEDED",
                       observed_at=datetime.now(UTC), age_seconds=901))
    r.monitor.refresh()
    assert r.monitor.snapshot().dependencies["bedrock_generation"].reason_code == "PROVIDER_OBSERVATION_EXPIRED"
    r.rows[0]["age_seconds"] = -1
    r.monitor.refresh()
    assert r.monitor.status().answers_available is None


@pytest.mark.parametrize("component", CORE)
def test_critical_dependency_failure_gives_not_ready_and_no_false_answers(operational, component):
    r = operational
    r.monitor.probes[component] = lambda: dependency("UNAVAILABLE", "DEPENDENCY_DOWN")
    r.monitor.refresh()
    assert r.monitor.snapshot().status == "NOT_READY"
    assert r.monitor.status().answers_available is False
    assert r.monitor.status().generation_available is False


def test_stt_outage_keeps_text_answers_tts_generation_available(operational):
    r = operational
    next(row for row in r.rows if row["component"] == "transcribe").update(state="UNAVAILABLE", reason_code="PROVIDER_ACCESS_DENIED")
    r.monitor.refresh()
    status = r.monitor.status()
    assert r.monitor.snapshot().status == "DEGRADED"
    assert status.stt_available is False and status.answers_available and status.tts_available and status.generation_available


@pytest.mark.parametrize("vector_config", [None, {"size": 7, "distance": "Cosine"}])
def test_missing_or_wrong_collection_does_not_block_initial_a1_ingestion(operational, monkeypatch, vector_config):
    client_class = httpx.Client

    def respond(request):
        if request.url.path == "/readyz":
            return httpx.Response(200)
        if vector_config is None:
            return httpx.Response(404)
        return httpx.Response(200, json={"result": {"config": {"params": {"vectors": vector_config}}}})

    monkeypatch.setattr("upraise_ai.services.operations.httpx.Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    r = operational
    r.monitor.probes["qdrant"] = r.monitor.qdrant
    r.monitor.refresh()
    assert r.monitor.snapshot().status == "DEGRADED"
    assert r.monitor.status().answers_available is False
    assert r.monitor.snapshot().dependencies["qdrant"].state == "AVAILABLE"


def test_tts_outage_preserves_text_and_blocks_full_course_generation(operational):
    r = operational
    next(row for row in r.rows if row["component"] == "polly").update(state="UNAVAILABLE", reason_code="PROVIDER_ACCESS_DENIED")
    r.monitor.refresh()
    status = r.monitor.status()
    assert status.answers_available and status.stt_available
    assert status.tts_available is False and status.generation_available is False


def test_expired_worker_and_temporary_throttle_evidence(operational):
    r = operational
    worker = next(row for row in r.rows if row["component"] == "lesson_worker")
    worker["age_seconds"] = 151
    cloud = next(row for row in r.rows if row["component"] == "bedrock_generation")
    cloud.update(state="UNAVAILABLE", reason_code="PROVIDER_THROTTLED", age_seconds=31)
    r.monitor.refresh()
    assert r.monitor.status().generation_available is False
    assert r.monitor.status().answers_available is None
    assert r.monitor.snapshot().dependencies["lesson_worker"].reason_code == "WORKER_HEARTBEAT_EXPIRED"


def test_stale_monitor_never_claims_readiness_and_snapshot_cannot_be_mutated(operational):
    r = operational
    r.monitor.snapshot().dependencies["polly"].state = "UNAVAILABLE"
    assert r.monitor.status().tts_available
    r.clock[0] += 31
    assert r.monitor.snapshot().status == "NOT_READY"
    assert r.monitor.status().answers_available is None
    assert r.monitor.status().features["answers"].reason_codes == ["MONITOR_STALE"]


def test_storage_observation_error_and_provider_exceptions_are_sanitized(operational):
    r = operational

    def fail():
        raise RuntimeError("PRIVATE_PASSWORD_AND_COURSE_TEXT")

    r.monitor.probes["qdrant"] = fail
    r.monitor.repository.read = fail
    r.monitor.refresh()
    assert "PRIVATE" not in r.monitor.snapshot().model_dump_json()
    assert r.monitor.snapshot().status == "NOT_READY"


def test_invalid_credentials_and_rotation_do_not_reuse_success(operational):
    r = operational
    r.credential_file.write_text("invalid private credentials")
    r.monitor.refresh()
    assert r.monitor.status().answers_available is False
    assert r.monitor.snapshot().dependencies["polly"].reason_code == "AWS_CREDENTIAL_FILE_INVALID"
    assert "private credentials" not in r.monitor.snapshot().model_dump_json()


def test_operator_flags_and_course_filters_are_separate_from_global_availability(operational):
    r = operational
    r.settings.operations_course_disabled_features = {"restricted": {"stt", "tts"}}
    r.settings.operations_kill_switch_courses = {"closed"}
    assert r.monitor.status().stt_available
    assert r.monitor.status("ordinary").generation_available
    restricted = r.monitor.status("restricted")
    assert restricted.answers_available and restricted.stt_available is False and restricted.tts_available is False
    assert restricted.generation_available is False
    assert r.monitor.status("closed").kill_switch_active
    assert all(value.available is False for value in r.monitor.status("closed").features.values())
    assert blocked_generation_courses(r.settings) == ["closed", "restricted"]
    with pytest.raises(IngestionError, match="AI_DISABLED"):
        require_feature(r.settings, "generation", "closed")
    r.settings.operations_kill_switch = True
    assert r.monitor.status().kill_switch_active


@pytest.mark.parametrize("updates", [{"operations_disabled_features": ["other"]},
    {"operations_course_disabled_features": {"bad": ["other"]}}, {"operations_kill_switch_courses": [" "]},
    {"operations_course_disabled_features": {" spaced ": ["stt"]}}])
def test_invalid_operator_configuration_rejected(updates):
    with pytest.raises(ValidationError):
        Settings(**updates)


def test_c1_public_c2_authenticated_scoped_and_returns_200_for_disabled_features(operational):
    app = FastAPI()
    app.include_router(router)
    app.state.operations = operational.monitor
    denied = Principal("mw", ("allowed",), frozenset())
    app.dependency_overrides[ingress] = lambda: denied
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "alive"}
        assert client.get("/ready").status_code == 200
        assert client.get("/v1/status").status_code == 403
        app.dependency_overrides[ingress] = lambda: Principal("mw", ("allowed",), frozenset({"jobs:read"}))
        assert client.get("/v1/status?course_id=foreign").status_code == 403
        for course in ("", " ", "x"*121):
            assert client.get("/v1/status", params={"course_id": course}).status_code == 422
        operational.settings.operations_kill_switch = True
        response = client.get("/v1/status?course_id=allowed")
        assert response.status_code == 200 and response.json()["kill_switch_active"]
        assert response.headers["cache-control"] == "no-store"
        operational.clock[0] += 31
        response = client.get("/ready")
        assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
        assert client.get("/health").status_code == 200


def test_sdk_observation_records_no_input_output_and_ignores_validation_errors(tmp_path, monkeypatch):
    file = tmp_path / "aws.json"
    file.write_text(json.dumps({"aws_access_key_id": "invented-key", "aws_secret_access_key": "invented-secret"}))
    settings = Settings(aws_credentials_file=file, jobs_dsn="postgresql://invented-operations-test")
    records = []
    monkeypatch.setattr(OperationsRepository, "report", lambda self, *args, **kwargs: records.append(args))
    client = aws_session(settings).client("bedrock-runtime", region_name=settings.aws_region)
    request = {"modelId": settings.embedding_model_id, "body": '{"inputText":"invented sample"}',
               "contentType": "application/json"}
    with Stubber(client) as stub:
        stub.add_response("invoke_model", {"body": b"invented output", "contentType": "application/json"}, request)
        client.invoke_model(**request)
        stub.add_client_error("invoke_model", service_error_code="ValidationException", http_status_code=400,
                              service_message="PRIVATE INPUT", expected_params=request)
        with pytest.raises(ClientError):
            client.invoke_model(**request)
        stub.add_client_error("invoke_model", service_error_code="AccessDeniedException", http_status_code=403,
                              service_message="PRIVATE INPUT", expected_params=request)
        with pytest.raises(ClientError):
            client.invoke_model(**request)
    assert records == [(["bedrock_embeddings"], "AVAILABLE", "PROVIDER_CALL_SUCCEEDED"),
                       (["bedrock_embeddings"], "UNAVAILABLE", "PROVIDER_ACCESS_DENIED")]
    assert "PRIVATE" not in str(records) and "invented sample" not in str(records)


def test_observation_storage_outage_does_not_change_provider_result(monkeypatch):
    monkeypatch.setattr(OperationsRepository, "report", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private")))
    observe(Settings(jobs_dsn="postgresql://invented"), ["polly"], "AVAILABLE", "PROVIDER_CALL_SUCCEEDED")


def test_c2_observes_converse_and_polly_through_actual_sdk(tmp_path, monkeypatch):
    file = tmp_path / "aws.json"
    file.write_text(json.dumps({"aws_access_key_id": "invented-key", "aws_secret_access_key": "invented-secret"}))
    settings = Settings(aws_credentials_file=file, jobs_dsn="postgresql://invented")
    records = []
    identities = []
    monkeypatch.setattr(OperationsRepository, "report", lambda self, *args, **kwargs:
                        (records.append(args), identities.append(kwargs["configuration"])))
    session = aws_session(settings)
    original_identity = configuration_id(settings)
    # A live client keeps its old credentials: its success cannot validate a new file.
    file.write_text(json.dumps({"aws_access_key_id": "rotated-invented-key", "aws_secret_access_key": "invented-secret"}))
    assert configuration_id(settings) != original_identity
    bedrock = session.client("bedrock-runtime", region_name=settings.aws_region)
    data = {"modelId": settings.lesson_generation_model_id, "messages": [{"role": "user", "content": [{"text": "invented sample"}]}]}
    with Stubber(bedrock) as stub:
        stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [{"text": "invented answer"}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1}}, data)
        assert bedrock.converse(**data)["stopReason"] == "end_turn"
    polly = session.client("polly", region_name=settings.aws_region)
    with Stubber(polly) as stub:
        stub.add_response("synthesize_speech", {"AudioStream": b"invented pcm", "ContentType": "audio/pcm"},
                          {"Text": "invented sample", "VoiceId": "Joanna", "OutputFormat": "pcm"})
        assert polly.synthesize_speech(Text="invented sample", VoiceId="Joanna", OutputFormat="pcm")["AudioStream"]
    assert [row[0] for row in records] == [["bedrock_generation"], ["polly"]]
    assert all(row[1:] == ("AVAILABLE", "PROVIDER_CALL_SUCCEEDED") for row in records)
    assert identities == [original_identity, original_identity]


def test_controls_enforced_for_a3_a1_before_storage_or_upload():
    settings = Settings(operations_kill_switch=True)
    principal = Principal("mw", ("course",), frozenset())
    data = SimpleNamespace(course_id="course")
    with pytest.raises(IngestionError, match="AI_DISABLED"):
        LessonService(settings, None, None, None, None).submit(data, principal)
    with pytest.raises(IngestionError, match="AI_DISABLED"):
        SubmissionService(settings, None, None).submit(data, None, principal, None)


def test_controls_enforced_for_b4_without_disabling_typed_questions(runtime):
    r = runtime
    r.settings.operations_course_disabled_features = {"course": {"stt", "tts"}}
    reply = run(r)
    assert reply.text and reply.audio is None and "AUDIO_DISABLED" in reply.warnings
    voice = r.data.model_copy(update={"request_id": uuid4(), "text": None})
    with pytest.raises(IngestionError, match="STT_DISABLED"):
        run(r, voice, audio=wav())
    assert not r.stt_calls
    r.settings.operations_kill_switch = True
    with pytest.raises(IngestionError, match="AI_DISABLED"):
        r.service.authorized_session(r.session_id, r.principal, None)
    # B5 still uses shared session authorization, not B4's feature gate.
    from upraise_ai.workflows.sessions import authorize_session
    assert authorize_session(r.settings, r.store, r.session_id, r.principal) is r.session

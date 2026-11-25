"""B7 uses isolated real stores/artifacts, never live deletion or model calls."""

import hashlib
from uuid import uuid4

import psycopg
import pytest
from test_a3_integration import accept, api, process
from test_a3_v2_integration import accept_bundle
from test_a7_all_levels import body, setup_revision, submit
from test_b1_integration import b1_api
from test_course_deletion_integration import api as deletion_api

from upraise_ai.core.errors import LeaseLost
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.services.lesson_replacement import artifact_job

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def endpoint(row):
    return f"/v1/lesson-jobs/{row['job_id']}"


def event_count(lesson, job_id):
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        return conn.execute("SELECT count(*) FROM lesson_audit WHERE job_id=%s AND event_type='GENERATED_CONTENT_DELETED'",
                            (job_id,)).fetchone()[0]


def test_b7_selected_level_preserves_sources_siblings_plan_audio_and_expiry(integration, lesson, monkeypatch):
    row, data, _ = accept_bundle(integration, lesson, monkeypatch)
    before = lesson[3].result(row)
    plan = lesson[1].get_plan(row["job_id"])
    source = integration[1].get_document(str(data.document_ids[0]))
    points = integration[3].document_count(str(data.document_ids[0]), data.course_id)
    with api(integration, lesson) as client:
        response = client.delete(endpoint(row), params={"learner_level": "intermediate"})
        assert response.status_code == 204 and not response.content, response.text
        current = client.get(f"/v1/jobs/{row['job_id']}/result").json()
        assert set(current["variants"]) == {"beginner", "advanced"}
        assert current["job_id"] == before.job_id and current["expires_at"] == before.expires_at
        for level in current["variants"]:
            assert current["variants"][level] == before.variants[level].model_dump(mode="json", exclude_none=True)
            for chunk in before.variants[level].chunks:
                assert hashlib.sha256(client.get(chunk.audio_url).content).hexdigest() == chunk.audio_sha256
        assert client.get(f"/v1/jobs/{row['job_id']}/result", params={"learner_level": "intermediate"}).status_code == 404
        assert all(client.get(chunk.audio_url).status_code == 404 for chunk in before.variants["intermediate"].chunks)
        assert {entry["learner_level"] for entry in client.get("/v1/lesson-jobs").json()} == {"beginner", "advanced"}
        assert lesson[1].prepared_chunks(row["job_id"], "intermediate") == []
        assert lesson[1].get_plan(row["job_id"]) == plan
        assert integration[1].get_document(str(data.document_ids[0])) == source
        assert integration[3].document_count(str(data.document_ids[0]), data.course_id) == points
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 204
        assert event_count(lesson, row["job_id"]) == 1
        folder = lesson[3].staging.job_dir(artifact_job(lesson[1].get(row["job_id"])))
        assert {path.name for path in folder.iterdir()} == {"current-result.json", *{
            chunk["chunk_id"] + ".wav" for variant in current["variants"].values() for chunk in variant["chunks"]}}
        assert not lesson[3].staging.job_dir(str(row["job_id"])).exists()


@pytest.mark.parametrize("scope", [None, "last_level"])
def test_b7_whole_lesson_and_last_level_remove_all_generated_content_only(integration, lesson, monkeypatch, scope):
    row, data, _ = accept_bundle(integration, lesson, monkeypatch)
    with api(integration, lesson) as client:
        if scope:
            for level in ("beginner", "intermediate"):
                assert client.delete(endpoint(row), params={"learner_level": level}).status_code == 204
        params = {"learner_level": "advanced"} if scope else {}
        assert client.delete(endpoint(row), params=params).status_code == 204
        assert client.delete(endpoint(row), params=params).status_code == 204
        assert client.get(f"/v1/jobs/{row['job_id']}/result").status_code == 404
        assert client.get("/v1/lesson-jobs").json() == []
        assert lesson[1].prepared_chunks(row["job_id"]) == [] and lesson[1].get_plan(row["job_id"]) is None
        assert not lesson[3].staging.job_dir(str(row["job_id"])).exists()
        with lesson[1].connect() as conn:
            operations = conn.execute("SELECT * FROM lesson_content_deletions WHERE job_id=%s", (row["job_id"],)).fetchall()
        assert all(operation["completed_at"] for operation in operations)
        assert all(not lesson[3].staging.job_dir(str(operation["deletion_id"])).exists() for operation in operations)
        assert integration[1].get_document(str(data.document_ids[0]))["deletion_requested_at"] is None
        assert integration[3].document_count(str(data.document_ids[0]), data.course_id)
        fresh = client.post("/v2/lesson-jobs", json=data.model_dump(mode="json"), headers={"Idempotency-Key": "after-b7"})
        assert fresh.status_code == 202 and fresh.json()["job_id"] != str(row["job_id"])


@pytest.mark.parametrize("cleanup_stage", ["parent_lock", "obsolete_files", "audit"])
def test_b7_pending_cleanup_is_fenced_durable_and_recovers_without_duplicate_audit(
        integration, lesson, monkeypatch, cleanup_stage):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch)
    original = lesson[3].result(row)
    if cleanup_stage == "parent_lock":
        with api(integration, lesson) as client, lesson[3].staging.artifact_lock(str(row["job_id"]), shared=True):
            response = client.delete(endpoint(row), params={"learner_level": "intermediate"})
            assert response.status_code == 503, response.text
    elif cleanup_stage == "obsolete_files":
        remove = lesson[3].staging.remove
        with monkeypatch.context() as patch:
            patch.setattr(lesson[3].staging, "remove", lambda *args: (_ for _ in ()).throw(OSError("invented outage")))
            with api(integration, lesson) as client:
                assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 503
        assert lesson[3].staging.remove == remove
    else:
        with monkeypatch.context() as patch:
            patch.setattr(lesson[3].audit, "generated_content_deleted",
                          lambda *args: (_ for _ in ()).throw(psycopg.OperationalError("invented outage")))
            with api(integration, lesson) as client:
                assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 503
    assert lesson[1].pending_content_deletion(row["job_id"])
    assert str(row["job_id"]) in {str(item["job_id"]) for item in lesson[1].maintenance()}
    assert lesson[1].result_hashes([str(row["job_id"])]) == {str(row["job_id"]): None}
    with api(integration, lesson) as client:
        assert client.get(f"/v1/jobs/{row['job_id']}/result").status_code == 503
        assert client.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": original.variants["advanced"].chunks[0].chunk_id, "teacher_review": "Clarify."}).status_code == 503
        lesson[3].complete_cleanup(str(row["job_id"]))
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 204
        assert set(client.get(f"/v1/jobs/{row['job_id']}/result").json()["variants"]) == {"beginner", "advanced"}
    assert not lesson[1].pending_content_deletion(row["job_id"]) and event_count(lesson, row["job_id"]) == 1


def test_b7_cancels_old_all_level_revision_and_remaining_levels_still_support_revision(integration, lesson, monkeypatch):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    with api(integration, lesson) as client:
        old = submit(client, body(row, original))
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 204
        cancelled = lesson[1].get(old)
        assert cancelled["status"] == "CANCELLED" and cancelled["lease_token"] is None
        assert lesson[1].prepared_chunks(old) == [] and lesson[1].get_plan(old) is None
        assert not lesson[3].staging.job_dir(old).exists()
        assert "revision_request" not in cancelled["metadata"]
        current = lesson[3].result(row)
        work = submit(client, body(row, current))
        assert process(lesson)["status"] == "DONE"
        assert set(lesson[3].result(row).variants) == {"beginner", "advanced"}
        assert lesson[1].get(work)["result"]["revised_levels"] == ["beginner", "advanced"]


def test_b7_expired_output_retained_text_can_be_deleted_by_level(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (row["job_id"],))
    lesson[1].maintenance()
    lesson[3].complete_cleanup(str(row["job_id"]))
    with api(integration, lesson) as client:
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 204
        assert lesson[1].prepared_chunks(row["job_id"], "intermediate") == []
        assert lesson[1].prepared_chunks(row["job_id"], "beginner")
        assert lesson[1].get_plan(row["job_id"])
        assert client.get(f"/v1/jobs/{row['job_id']}/result").status_code == 410
        assert not lesson[3].staging.job_dir(str(row["job_id"])).exists()


def test_b7_source_deletion_removes_new_artifact_directory(integration, lesson, monkeypatch):
    row, data, _ = accept_bundle(integration, lesson, monkeypatch)
    with deletion_api(integration, lesson) as (client, _):
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 204
        folder = lesson[3].staging.job_dir(artifact_job(lesson[1].get(row["job_id"])))
        assert folder.exists()
        response = client.delete(f"/v1/documents/{data.document_ids[0]}")
        assert response.status_code == 204, response.text
        assert not folder.exists() and lesson[1].prepared_chunks(row["job_id"]) == []
        assert integration[3].document_count(str(data.document_ids[0]), data.course_id) == 0


def test_b7_closes_existing_session_on_changed_lesson(integration, lesson, monkeypatch):
    row, data, _ = accept_bundle(integration, lesson, monkeypatch)
    request = {key: data.model_dump(mode="json")[key] for key in ("course_id", "activity_id", "lesson_id")}
    request.update(lesson_job_id=str(row["job_id"]), session_id=str(uuid4()),
                   learner_level="beginner", consent_scope=["telemetry"])
    with b1_api(integration, lesson) as client:
        response = client.post("/v1/sessions", json=request)
        assert response.status_code == 201, response.text
        assert app.state.sessions.active_job_ids() == [str(row["job_id"])]
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 204
        assert app.state.sessions.active_job_ids() == []


def test_b7_unrelated_queued_revision_and_old_processing_receipt_preserve_remaining_output(integration, lesson, monkeypatch):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    with api(integration, lesson) as client:
        work = submit(client, body(row, original, level="advanced", all_levels=False))
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 204
        assert lesson[1].get(work)["status"] == "QUEUED"
        assert process(lesson)["status"] == "DONE"
        assert client.delete(endpoint(row), params={"learner_level": "beginner"}).status_code == 204
        assert client.delete(f"/v1/jobs/{work}", params={"reason": "RECEIVED"}).status_code == 204
        current = client.get(f"/v1/jobs/{row['job_id']}/result")
        assert current.status_code == 200 and set(current.json()["variants"]) == {"advanced"}
        for chunk in current.json()["variants"]["advanced"]["chunks"]:
            assert hashlib.sha256(client.get(chunk["audio_url"]).content).hexdigest() == chunk["audio_sha256"]


def test_b7_running_generation_requires_whole_scope_and_revokes_ownership(integration, lesson):
    target, _, _ = accept(integration, lesson)
    row = lesson[1].claim(lesson[0].lease_seconds, lesson[0].lesson_signature)
    with api(integration, lesson) as client:
        assert client.delete(endpoint(row), params={"learner_level": "intermediate"}).status_code == 409
        assert client.delete(endpoint(row)).status_code == 204
    assert lesson[1].get(target["job_id"])["status"] == "CANCELLED"
    with pytest.raises(LeaseLost), lesson[1].owned_transaction(row["job_id"], row["lease_token"]):
        pass


def test_b7_permissions_unknown_level_and_invalid_input_do_not_mutate_lesson(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch)
    before = lesson[1].get(row["job_id"])["result"]
    with api(integration, lesson) as client:
        assert client.delete("/v1/lesson-jobs/not-a-uuid").status_code == 422
        assert client.delete(f"/v1/lesson-jobs/{uuid4()}").status_code == 404
        assert client.delete(endpoint(row), params={"learner_level": "../../intermediate"}).status_code == 422
        assert client.delete(endpoint(row), params={"learner_level": "unknown"}).status_code == 404
        app.dependency_overrides[ingress] = lambda: Principal("another-owner", lesson[4].courses)
        assert client.delete(endpoint(row)).status_code == 404
    assert lesson[1].get(row["job_id"])["result"] == before and not lesson[1].pending_content_deletion(row["job_id"])

"""Persistent archive lifecycle with disposable stores and fake model/speech calls."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_a3_integration import api, process
from test_a7_all_levels import body, setup_revision, submit
from test_b1_integration import b1_api, completed
from test_course_deletion_integration import documents

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.ingress import ingress
from upraise_ai.main import app
from upraise_ai.services.lesson_replacement import artifact_directory, artifact_job
from upraise_ai.storage.files import StagingStore
from upraise_ai.workflows.archive_lessons import archive_available_outputs

pytest_plugins = ["test_a1_integration", "test_a3_integration", "test_b1"]


def enable_archive(integration, lesson, tmp_path):
    root = tmp_path.parent / (tmp_path.name + "-archive")
    store = StagingStore(lesson[0].data_dir, root)
    lesson[2].settings = lesson[3].settings = lesson[0].model_copy(update={"lesson_archive_dir": root})
    for staging in (lesson[2].staging, lesson[3].staging, integration[4].staging):
        staging.archive = store.archive
    return store.archive


def expire_and_clean(lesson, job_id):
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (job_id,))
    lesson[1].maintenance()
    lesson[3].complete_cleanup(job_id)


def test_archive_ttl_removes_working_files_but_keeps_playback_review_and_sessions(integration, lesson, tmp_path):
    archive = enable_archive(integration, lesson, tmp_path)
    target, _, row, start = completed(integration, lesson)
    assert row["result"]["persistent_output"]
    folder = artifact_directory(lesson[3].staging, row)
    assert folder.is_relative_to(archive.root)
    manifest = folder / "result.json"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == row["result"]["result_sha256"]
    before = lesson[3].result(row)
    assert {path.name for path in folder.iterdir()} == {"result.json", *{f"{chunk.chunk_id}.wav" for chunk in before.chunks}}
    expire_and_clean(lesson, target["job_id"])
    assert not lesson[3].staging.job_dir(target["job_id"]).exists()
    assert folder.exists()
    with b1_api(integration, lesson) as client:
        catalog, = client.get("/v1/lesson-jobs").json()
        assert catalog["job_id"] == target["job_id"] and not catalog.get("expires_at")
        result = client.get(f"/v1/jobs/{target['job_id']}/result")
        assert result.status_code == 200, result.text
        assert not result.json().get("expires_at")
        for chunk in before.chunks:
            audio = client.get(chunk.audio_url)
            assert audio.status_code == 200
            assert hashlib.sha256(audio.content).hexdigest() == chunk.audio_sha256
        review = client.get(f"/v1/lesson-reviews/{target['job_id']}").json()
        assert review["revision_available"] and not review.get("audio_expires_at")
        response = client.post("/v1/sessions", json={**start, "session_id": str(uuid4()), "consent_scope": ["questions"]})
        assert response.status_code == 201, response.text
        expiry = datetime.fromisoformat(response.json()["expires_at"].replace("Z", "+00:00"))
        assert datetime.now(UTC) < expiry <= datetime.now(UTC) + timedelta(seconds=lesson[0].session_ttl_seconds)


def test_archive_a7_after_ttl_replaces_current_package_and_discards_obsolete_version(integration, lesson, monkeypatch, tmp_path):
    archive = enable_archive(integration, lesson, tmp_path)
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    old_folder = artifact_directory(lesson[3].staging, row)
    expire_and_clean(lesson, str(row["job_id"]))
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        done = process(lesson)
        assert done["status"] == "DONE", done["error_code"]
        current = lesson[1].get(row["job_id"])
        assert current["result"]["persistent_output"] and not current["cleanup_requested_at"]
        assert not old_folder.exists()
        folder = artifact_directory(lesson[3].staging, current)
        assert folder.exists() and folder.is_relative_to(archive.root)
        result = client.get(f"/v1/jobs/{row['job_id']}/result").json()
        for level, variant in result["variants"].items():
            assert variant["chunks"][1]["text"] != original.variants[level].chunks[1].text
        expire_and_clean(lesson, work)
        expire_and_clean(lesson, str(row["job_id"]))
        assert client.get(f"/v1/jobs/{row['job_id']}/result").status_code == 200
        assert folder.exists()


def test_archive_b7_partial_then_whole_delete_after_ttl(integration, lesson, monkeypatch, tmp_path):
    archive = enable_archive(integration, lesson, tmp_path)
    row, _, _ = setup_revision(integration, lesson, monkeypatch)
    owner = str(row["job_id"])
    expire_and_clean(lesson, owner)
    with api(integration, lesson) as client:
        assert client.delete(f"/v1/lesson-jobs/{owner}", params={"learner_level": "intermediate"}).status_code == 204
        current = lesson[1].get(owner)
        result = client.get(f"/v1/jobs/{owner}/result")
        assert result.status_code == 200, result.text
        assert set(result.json()["variants"]) == {"beginner", "advanced"}
        folder = artifact_directory(lesson[3].staging, current)
        assert set(archive.root.joinpath(owner).iterdir()) == {folder.parent}
        assert not lesson[3].staging.job_dir(artifact_job(current)).exists()
        for variant in result.json()["variants"].values():
            assert client.get(variant["chunks"][0]["audio_url"]).status_code == 200
        assert client.delete(f"/v1/lesson-jobs/{owner}").status_code == 204
        assert not archive.root.joinpath(owner).exists()
        assert client.get(f"/v1/jobs/{owner}/result").status_code == 404
        assert client.get("/v1/lesson-jobs").json() == []


def test_archive_source_deletion_removes_all_persisted_output(integration, lesson, tmp_path):
    archive = enable_archive(integration, lesson, tmp_path)
    target, doc, _, _ = completed(integration, lesson)
    expire_and_clean(lesson, target["job_id"])
    documents(integration, lesson).delete(doc["document_id"], lesson[4])
    assert not archive.root.joinpath(target["job_id"]).exists()
    assert lesson[1].prepared_chunks(target["job_id"]) == []
    with api(integration, lesson) as client:
        assert client.get("/v1/lesson-jobs").json() == []
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 404


def test_archive_backfill_copies_only_intact_available_output_and_is_idempotent(integration, lesson, tmp_path):
    target, _, row, _ = completed(integration, lesson)
    digest = row["result"]["result_sha256"]
    archive = enable_archive(integration, lesson, tmp_path)
    assert archive_available_outputs(lesson[3]) == 1
    assert archive_available_outputs(lesson[3]) == 0
    assert lesson[1].get(target["job_id"])["result"]["result_sha256"] == digest
    expire_and_clean(lesson, target["job_id"])
    assert archive.root.joinpath(target["job_id"]).exists()
    assert lesson[3].result(lesson[1].get(target["job_id"])).expires_at is None


def test_archive_never_restores_expired_missing_output_or_bypasses_ownership(integration, lesson, tmp_path):
    target, _, _, _ = completed(integration, lesson)
    expire_and_clean(lesson, target["job_id"])
    archive = enable_archive(integration, lesson, tmp_path)
    assert archive_available_outputs(lesson[3]) == 0
    assert not archive.root.joinpath(target["job_id"]).exists()
    with api(integration, lesson) as client:
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 410
        app.dependency_overrides[ingress] = lambda: replace(lesson[4], client_id="other")
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 404


def test_archive_backfill_salvages_intact_output_that_has_passed_its_temporary_deadline(integration, lesson, tmp_path):
    target, _, _, _ = completed(integration, lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 hour' WHERE job_id=%s", (target["job_id"],))
    enable_archive(integration, lesson, tmp_path)
    assert archive_available_outputs(lesson[3]) == 1
    expire_and_clean(lesson, target["job_id"])
    with api(integration, lesson) as client:
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 200


def test_archive_storage_outage_retries_without_repeating_generation(integration, lesson, monkeypatch, tmp_path):
    archive = enable_archive(integration, lesson, tmp_path)
    from test_a3_integration import accept
    target, _, _ = accept(integration, lesson)
    with monkeypatch.context() as patch:
        patch.setattr(archive, "_save", lambda *args: (_ for _ in ()).throw(OSError("invented archive outage")))
        row = process(lesson)
        assert row["status"] == "QUEUED" and row["error_code"] == "LESSON_ARCHIVE_STORAGE_UNAVAILABLE"
    calls = lesson[5].drafts
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    assert row["result"]["persistent_output"] and lesson[5].drafts == calls


def test_archive_corruption_is_rejected_even_when_temporary_copy_is_intact(integration, lesson, tmp_path):
    enable_archive(integration, lesson, tmp_path)
    target, _, row, _ = completed(integration, lesson)
    result = lesson[3].result(row)
    chunk = result.chunks[0]
    (artifact_directory(lesson[3].staging, row) / f"{chunk.chunk_id}.wav").write_bytes(b"corrupt archive")
    with api(integration, lesson) as client:
        response = client.get(chunk.audio_url)
        assert response.status_code == 503
        assert response.json()["detail"] == "LESSON_AUDIO_INTEGRITY_FAILURE"


def test_archive_write_failure_cannot_publish_done_output(integration, lesson, monkeypatch, tmp_path):
    archive = enable_archive(integration, lesson, tmp_path)
    monkeypatch.setattr(archive, "save", lambda *args: (_ for _ in ()).throw(IngestionError("INVENTED_ARCHIVE_OUTAGE")))
    from test_a3_integration import accept
    target, _, _ = accept(integration, lesson)
    row = process(lesson)
    assert row["status"] == "FAILED" and row["error_code"] == "INVENTED_ARCHIVE_OUTAGE"
    assert not row.get("result")
    assert lesson[1].prepared_chunks(target["job_id"])


def test_archive_a6_receipt_keeps_saved_files_but_withdraws_temporary_delivery(integration, lesson, tmp_path):
    archive = enable_archive(integration, lesson, tmp_path)
    target, _, _, _ = completed(integration, lesson)
    with api(integration, lesson) as client:
        assert client.delete(f"/v1/jobs/{target['job_id']}", params={"reason": "RECEIVED"}).status_code == 204
        assert archive.root.joinpath(target["job_id"]).exists()
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 410
        assert client.get("/v1/lesson-jobs").json() == []

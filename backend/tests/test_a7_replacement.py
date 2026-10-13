"""Atomic replacement, unrelated edits, legacy consolidation and cleanup recovery."""

import hashlib
import json

from test_a3_integration import api, process
from test_a7_integration import parent, payload, submit_revision

from upraise_ai.core.errors import IngestionError
from upraise_ai.services.lesson_replacement import consolidate_legacy_revisions

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def test_a7_different_chunk_edits_preserve_each_other(integration, lesson):
    parent_id, original, _ = parent(integration, lesson)
    with api(integration, lesson) as client:
        first = submit_revision(client, payload(parent_id, original))
        second = submit_revision(client, payload(parent_id, original, chunk_id=original.chunks[0].chunk_id))
        assert process(lesson)["status"] == "DONE"
        assert process(lesson)["status"] == "DONE"
        result = client.get(f"/v1/jobs/{parent_id}/result").json()
        assert result["chunks"][0]["text"].startswith("A clearer")
        assert result["chunks"][1]["text"].startswith("A clearer")
        assert len(client.get("/v1/lesson-jobs").json()) == 1
        assert lesson[1].prepared_chunks(first) == lesson[1].prepared_chunks(second) == []


def test_a7_failure_after_manifest_preparation_leaves_current_lesson_intact(integration, lesson, monkeypatch):
    from upraise_ai.workers import lessons as workers

    parent_id, original, _ = parent(integration, lesson)
    before = lesson[1].get(parent_id)["result"]
    publish = workers.publish_replacement

    def fail_after_prepare(*args, **kwargs):
        publish(*args, **kwargs)
        raise IngestionError("INVENTED_PUBLICATION_FAILURE")

    monkeypatch.setattr(workers, "publish_replacement", fail_after_prepare)
    with api(integration, lesson) as client:
        work = submit_revision(client, payload(parent_id, original))
        assert process(lesson)["status"] == "FAILED"
        assert lesson[1].get(parent_id)["result"] == before
        current = client.get(f"/v1/jobs/{parent_id}/result").json()
        assert current["chunks"][1]["text"] == original.chunks[1].text
        assert client.get(current["chunks"][1]["audio_url"]).status_code == 200
        assert client.get(f"/v1/jobs/{work}/result").status_code == 409


def test_a7_cleanup_failure_is_recovered_without_losing_current_output(integration, lesson, monkeypatch):
    parent_id, original, _ = parent(integration, lesson)
    remove = lesson[3].staging.remove

    def unavailable(job_id):
        if str(job_id) == parent_id:
            raise OSError("Invented cleanup outage")
        remove(job_id)

    monkeypatch.setattr(lesson[3].staging, "remove", unavailable)
    # The worker and API have separate StagingStore instances.
    monkeypatch.setattr(lesson[2].staging, "remove", unavailable)
    with api(integration, lesson) as client:
        work = submit_revision(client, payload(parent_id, original))
        assert process(lesson)["status"] == "DONE"
        current = lesson[1].get(parent_id)
        assert current["result"]["replacement_cleanup_jobs"] == [parent_id]
        assert client.get(f"/v1/jobs/{parent_id}/result").status_code == 200
        monkeypatch.setattr(lesson[3].staging, "remove", remove)
        lesson[3].complete_cleanup(parent_id)
        assert "replacement_cleanup_jobs" not in lesson[1].get(parent_id)["result"]
        assert not lesson[3].staging.job_dir(parent_id).exists()
        assert (lesson[3].staging.job_dir(work) / "current-result.json").exists()


def test_a7_legacy_revision_chain_consolidates_latest_content_and_discards_copies(integration, lesson, monkeypatch):
    from upraise_ai.workers import lessons as workers

    parent_id, original, _ = parent(integration, lesson)

    def legacy(jobs, staging, conn, job, revised, ttl):
        return {"lesson_id": revised.lesson_id, "lesson_status": "DRAFT", "chunk_count": len(revised.chunks),
                "revision": revised.revision.model_dump(mode="json"),
                "result_sha256": hashlib.sha256((staging.job_dir(str(job["job_id"])) / "result.json").read_bytes()).hexdigest()}

    monkeypatch.setattr(workers, "publish_replacement", legacy)
    with api(integration, lesson) as client:
        first = submit_revision(client, payload(parent_id, original))
        assert process(lesson)["status"] == "DONE"
        first_result = lesson[3].result(lesson[1].get(first))
        second = submit_revision(client, payload(first, first_result, chunk_id=original.chunks[0].chunk_id))
        assert process(lesson)["status"] == "DONE"
        expected = lesson[3].result(lesson[1].get(second))
        assert consolidate_legacy_revisions(lesson[3]) == [first, second]
        assert consolidate_legacy_revisions(lesson[3]) == []
        current = lesson[3].result(lesson[1].get(parent_id))
        assert [c.text for c in current.chunks] == [c.text for c in expected.chunks]
        assert current.job_id == parent_id and current.revision is None
        assert len(client.get("/v1/lesson-jobs").json()) == 1
        assert lesson[1].prepared_chunks(first) == lesson[1].prepared_chunks(second) == []
        assert not lesson[3].staging.job_dir(first).exists()
        assert not (lesson[3].staging.job_dir(second) / "revision-base.json").exists()
        assert "revision_request" not in lesson[1].get(first)["metadata"]
        assert "revision_request" not in lesson[1].get(second)["metadata"]
        assert client.get(f"/v1/jobs/{parent_id}/result").json()["job_id"] == parent_id
        assert all("teacher_review" not in json.dumps(lesson[1].get(job)["metadata"]) for job in [first, second])


def test_a7_legacy_available_level_survives_expired_parent_bundle(integration, lesson, monkeypatch):
    from upraise_ai.workers import lessons as workers

    parent_id, original, _ = parent(integration, lesson)

    def legacy(jobs, staging, conn, job, revised, ttl):
        return {"lesson_id": revised.lesson_id, "lesson_status": "DRAFT", "chunk_count": len(revised.chunks),
                "revision": revised.revision.model_dump(mode="json"),
                "result_sha256": hashlib.sha256((staging.job_dir(str(job["job_id"])) / "result.json").read_bytes()).hexdigest()}

    monkeypatch.setattr(workers, "publish_replacement", legacy)
    with api(integration, lesson) as client:
        work = submit_revision(client, payload(parent_id, original))
        assert process(lesson)["status"] == "DONE"
        with lesson[1].connect() as conn:
            conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (parent_id,))
        lesson[1].maintenance()
        lesson[3].complete_cleanup(parent_id)
        assert consolidate_legacy_revisions(lesson[3]) == [work]
        assert consolidate_legacy_revisions(lesson[3]) == []
        current = client.get(f"/v1/jobs/{work}/result")
        assert current.status_code == 200 and "revision" not in current.json()
        assert current.json()["chunks"][1]["text"].startswith("A clearer")
        assert client.get(current.json()["chunks"][1]["audio_url"]).status_code == 200
        assert {item["job_id"] for item in client.get("/v1/lesson-jobs").json()} == {work}
        assert lesson[1].prepared_chunks(parent_id) == []

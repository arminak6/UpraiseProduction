"""Teacher review reads durable text using isolated stores and fake providers."""

from dataclasses import replace

import pytest
from test_a3_integration import accept, api, process
from test_a3_v2_integration import accept_bundle
from test_a7_all_levels import body, setup_revision, submit

from upraise_ai.guards.ingress import ingress
from upraise_ai.main import app

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def completed(integration, lesson):
    target, doc, _ = accept(integration, lesson, options={"learner_level": "advanced"})
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    return target, doc, row


def test_review_retained_text_survives_a6_without_restoring_audio(integration, lesson):
    target, _, before = completed(integration, lesson)
    path = f"/v1/lesson-reviews/{target['job_id']}"
    with api(integration, lesson) as client:
        summary, = client.get("/v1/lesson-reviews").json()
        assert summary["levels"] == ["advanced"] and summary["revision_available"]
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        text = response.json()
        chunk = text["variants"]["advanced"]["chunks"][0]
        assert set(chunk) == {"chunk_id", "index", "text", "sources", "reviewer_report"}
        assert chunk["text"] == lesson[1].prepared_chunks(target["job_id"])[0]["text"]
        assert lesson[1].get(target["job_id"]) == before
        assert client.delete(f"/v1/jobs/{target['job_id']}", params={"reason": "RECEIVED"}).status_code == 204
        after = lesson[1].get(target["job_id"])
        retained = client.get(path).json()
        assert retained["variants"] == text["variants"]
        assert not retained["revision_available"]
        assert retained["revision_unavailable_reason"] == "LESSON_OUTPUT_EXPIRED"
        assert len(client.get("/v1/lesson-reviews").json()) == 1
        assert client.get("/v1/lesson-jobs").json() == []
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 410
        assert lesson[1].get(target["job_id"]) == after
        assert not lesson[3].staging.job_dir(target["job_id"]).exists()


def test_review_expired_text_is_readable_and_a7_still_requires_live_output(integration, lesson):
    target, _, _ = completed(integration, lesson)
    chunk = lesson[1].prepared_chunks(target["job_id"])[0]
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                     (target["job_id"],))
    with api(integration, lesson) as client:
        summary, = client.get("/v1/lesson-reviews").json()
        assert not summary["revision_available"]
        assert client.get(f"/v1/lesson-reviews/{target['job_id']}").status_code == 200
        assert client.post("/v1/audio:regenerate", json={"lesson_job_id": target["job_id"],
            "chunk_id": str(chunk["chunk_id"]), "teacher_review": "Clarify this."}).status_code == 410


@pytest.mark.parametrize("change,list_status,text_status", [
    ({"client_id": "another-client"}, 200, 404),
    ({"courses": ()}, 200, 404),
    ({"local_test": True, "courses": ()}, 200, 200),
    ({"local_test": True, "courses": (), "client_id": "another-local-client"}, 200, 404),
])
def test_review_authorization(integration, lesson, change, list_status, text_status):
    target, _, _ = completed(integration, lesson)
    with api(integration, lesson) as client:
        app.dependency_overrides[ingress] = lambda: replace(lesson[4], **change)
        response = client.get("/v1/lesson-reviews")
        assert response.status_code == list_status
        if list_status == 200:
            assert len(response.json()) == (1 if text_status == 200 else 0)
        assert client.get(f"/v1/lesson-reviews/{target['job_id']}").status_code == text_status
        if text_status == 200:
            # The local-test exception grants course access, never client access.
            assert client.get("/v1/lesson-reviews", params={"course_id": "course-1"}).status_code == 200


@pytest.mark.parametrize("mutation", [
    "deletion_requested_at=clock_timestamp()", "active_attempt=NULL", "status='FAILED'", "client_id='other'",
])
def test_review_source_fences(integration, lesson, mutation):
    target, doc, _ = completed(integration, lesson)
    with integration[1].connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET " + mutation + " WHERE document_id=%s", (doc["document_id"],))
    with api(integration, lesson) as client:
        assert client.get("/v1/lesson-reviews").json() == []
        assert client.get(f"/v1/lesson-reviews/{target['job_id']}").status_code == 409


def test_review_pagination_filters_before_limiting_and_rejects_scope_expansion(integration, lesson):
    first, _, _ = completed(integration, lesson)
    second, _, _ = completed(integration, lesson)
    third, _, _ = completed(integration, lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET client_id='other' WHERE job_id=%s", (third["job_id"],))
    with api(integration, lesson) as client:
        for offset, expected in [(0, second), (1, first)]:
            page = client.get("/v1/lesson-reviews", params={"limit": 1, "offset": offset}).json()
            assert [item["job_id"] for item in page] == [expected["job_id"]]
        assert client.get("/v1/lesson-reviews", params={"offset": 2}).json() == []
        assert client.get("/v1/lesson-reviews", params={"course_id": "outside"}).status_code == 403
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"course_id": ""}):
            assert client.get("/v1/lesson-reviews", params=params).status_code == 422


def test_review_aligned_levels_count_once_and_b7_removes_deleted_text(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch, profiles={"foundation": "Plain steps.", "expert": "Formal reasoning."})
    path = f"/v1/lesson-reviews/{row['job_id']}"
    with api(integration, lesson) as client:
        summary, = client.get("/v1/lesson-reviews").json()
        assert summary["levels"] == ["foundation", "expert"]
        assert summary["chunk_counts"] == {level: len(parts.chunks) for level, parts in lesson[3].result(row).variants.items()}
        assert client.delete(f"/v1/lesson-jobs/{row['job_id']}", params={"learner_level": "foundation"}).status_code == 204
        assert client.get(path).json()["levels"] == ["expert"]
        assert client.get("/v1/lesson-reviews").json()[0]["levels"] == ["expert"]
        assert client.delete(f"/v1/lesson-jobs/{row['job_id']}").status_code == 204
        assert client.get(path).status_code == 404
        assert client.get("/v1/lesson-reviews").json() == []


def test_review_reads_current_a7_replacement_and_never_lists_processing_jobs(integration, lesson, monkeypatch):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    path = f"/v1/lesson-reviews/{row['job_id']}"
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        assert client.get(f"/v1/lesson-reviews/{work}").status_code == 404
        assert len(client.get("/v1/lesson-reviews").json()) == 1
        done = process(lesson)
        assert done["status"] == "DONE", done["error_code"]
        assert client.get(f"/v1/lesson-reviews/{work}").status_code == 404
        assert len(client.get("/v1/lesson-reviews").json()) == 1
        current = client.get(path).json()
        for level, variant in current["variants"].items():
            assert variant["chunks"][1]["chunk_id"] == original.variants[level].chunks[1].chunk_id
            assert variant["chunks"][1]["text"] != original.variants[level].chunks[1].text
            assert variant["chunks"][0]["text"] == original.variants[level].chunks[0].text


def test_review_incomplete_root_text_is_readable_but_not_revision_ready(integration, lesson):
    target, _, _ = completed(integration, lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET status='FAILED',result=NULL WHERE job_id=%s", (target["job_id"],))
    with api(integration, lesson) as client:
        summary, = client.get("/v1/lesson-reviews").json()
        assert summary["job_status"] == "FAILED" and not summary["revision_available"]
        assert summary["revision_unavailable_reason"] == "LESSON_RESULT_NOT_READY"
        assert client.get(f"/v1/lesson-reviews/{target['job_id']}").status_code == 200

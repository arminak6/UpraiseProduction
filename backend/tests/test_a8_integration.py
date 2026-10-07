"""A8 catalog filtering with real disposable storage and fake generation."""

from dataclasses import replace

import pytest
from test_a3_integration import accept, api, process

from upraise_ai.guards.ingress import ingress
from upraise_ai.main import app

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def completed(integration, lesson):
    target, document, _ = accept(integration, lesson, options={"learner_level": "advanced"})
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    return target, document, row


def test_a8_ready_metadata_is_read_only_and_has_no_lesson_content(integration, lesson):
    target, _, before = completed(integration, lesson)
    with api(integration, lesson) as client:
        response = client.get("/v1/lesson-jobs", params={"course_id": "course-1"})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        item, = response.json()
        assert item["job_id"] == target["job_id"]
        assert item["status"] == "DRAFT" and item["requires_review"]
        assert item["learner_level"] == "advanced"
        assert item["language"] == before["metadata"]["language"]
        assert item["chunk_count"] == before["result"]["chunk_count"]
        assert item["expires_at"]
        assert set(item) == {"job_id", "lesson_id", "course_id", "activity_id", "language", "voice",
                             "learner_level", "chunk_count", "status", "label", "requires_review",
                             "review_issue_count", "created_at", "expires_at"}
        # Listing is repeatable and does not acknowledge or change a generation.
        assert client.get("/v1/lesson-jobs").json() == [item]
    assert lesson[1].get(target["job_id"]) == before


@pytest.mark.parametrize("principal_change, expected", [
    ({"client_id": "another-client"}, 200),
    ({"courses": ()}, 200),
    ({"scopes": frozenset({"lessons:write"})}, 403),
])
def test_a8_enforces_client_course_and_read_scope(integration, lesson, principal_change, expected):
    completed(integration, lesson)
    with api(integration, lesson) as client:
        app.dependency_overrides[ingress] = lambda: replace(lesson[4], **principal_change)
        response = client.get("/v1/lesson-jobs")
        assert response.status_code == expected
        if expected == 200:
            assert response.json() == []


def test_a8_course_filter_cannot_expand_access_and_local_mode_keeps_ownership(integration, lesson):
    completed(integration, lesson)
    with api(integration, lesson) as client:
        assert client.get("/v1/lesson-jobs", params={"course_id": "outside-course"}).status_code == 403
        app.dependency_overrides[ingress] = lambda: replace(lesson[4], courses=(), local_test=True)
        assert len(client.get("/v1/lesson-jobs").json()) == 1
        app.dependency_overrides[ingress] = lambda: replace(
            lesson[4], client_id="another-local-client", courses=(), local_test=True)
        assert client.get("/v1/lesson-jobs").json() == []


@pytest.mark.parametrize("mutation", [
    "status='FAILED'",
    "status='QUEUED'",
    "status='RUNNING'",
    "status='CANCELLED'",
    "expires_at=clock_timestamp()-interval '1 second'",
    "expires_at=NULL",
    "cleanup_requested_at=clock_timestamp(),cleanup_reason='RECEIVED'",
    "metadata=jsonb_set(metadata,'{document_ids}','[]')",
])
def test_a8_hides_incomplete_expired_or_disposed_lessons(integration, lesson, mutation):
    target, _, _ = completed(integration, lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET " + mutation + " WHERE job_id=%s", (target["job_id"],))
    with api(integration, lesson) as client:
        assert client.get("/v1/lesson-jobs").json() == []


@pytest.mark.parametrize("mutation", [
    "deletion_requested_at=clock_timestamp()",
    "active_attempt=NULL",
    "status='FAILED'",
    "client_id='other-client'",
    "metadata=jsonb_set(metadata,'{course_id}','\"other-course\"')",
])
def test_a8_hides_lessons_with_unavailable_sources(integration, lesson, mutation):
    _, document, _ = completed(integration, lesson)
    with integration[1].connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET " + mutation + " WHERE document_id=%s",
                     (document["document_id"],))
    with api(integration, lesson) as client:
        assert client.get("/v1/lesson-jobs").json() == []


def test_a8_filters_before_pagination_and_is_stably_ordered(integration, lesson):
    first, _, _ = completed(integration, lesson)
    second, _, _ = completed(integration, lesson)
    third, _, _ = completed(integration, lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET client_id='other-client' WHERE job_id=%s", (third["job_id"],))
    with api(integration, lesson) as client:
        for offset, expected in [(0, second), (1, first)]:
            response = client.get("/v1/lesson-jobs", params={"limit": 1, "offset": offset})
            assert response.status_code == 200
            assert [r["job_id"] for r in response.json()] == [expected["job_id"]]
        assert client.get("/v1/lesson-jobs", params={"offset": 2}).json() == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"course_id": ""}])
def test_a8_rejects_invalid_pagination(integration, lesson, params):
    with api(integration, lesson) as client:
        assert client.get("/v1/lesson-jobs", params=params).status_code == 422

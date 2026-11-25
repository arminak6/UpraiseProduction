"""A5 pull downloads with real isolated storage and fake generation providers."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
from test_a3_integration import accept, api, process

from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def access_events(lesson, job_id):
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        return conn.execute(
            """SELECT event_id,event_type,details FROM lesson_audit
            WHERE job_id=%s AND event_type IN ('RESULT_ACCESSED','AUDIO_ACCESSED')""", (job_id,),
        ).fetchall()


def completed(integration, lesson, **options):
    target, doc, _ = accept(integration, lesson, **options)
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    result = lesson[3].result(row)
    return target, doc, result


def test_a5_result_and_audio_audit_each_request_without_changing_output(integration, lesson):
    target, _, result = completed(integration, lesson)
    path = f"/v1/jobs/{target['job_id']}"
    with api(integration, lesson) as client:
        for _ in range(2):
            response = client.get(path+"/result")
            assert response.status_code == 200 and response.json()["job_id"] == target["job_id"]
            assert response.json()["status"] == "DRAFT"
            assert response.headers["cache-control"] == "no-store"
            audio = client.get(result.chunks[0].audio_url)
            assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
            assert hashlib.sha256(audio.content).hexdigest() == result.chunks[0].audio_sha256
            assert audio.headers["cache-control"] == "no-store"
            assert int(audio.headers["content-length"]) == len(audio.content)
    events = access_events(lesson, target["job_id"])
    assert len(events) == len({event[0] for event in events}) == 4
    assert sorted(event[1] for event in events) == ["AUDIO_ACCESSED"]*2 + ["RESULT_ACCESSED"]*2
    for _, event, details in events:
        assert details["mode"] == "pull" and len(details["result_sha256"]) == 64
        if event == "AUDIO_ACCESSED":
            assert details["chunk_id"] == result.chunks[0].chunk_id
            assert details["audio_sha256"] == result.chunks[0].audio_sha256
            assert details["bytes"] == len(audio.content)
        assert not any(key in details for key in ("text", "ssml", "url", "token", "client_secret"))
    row = lesson[1].get(target["job_id"])
    assert not row["cleanup_reason"] and not row["cleanup_requested_at"]


def test_a5_simultaneous_json_and_audio_downloads_succeed(integration, lesson, monkeypatch):
    target, _, result = completed(integration, lesson, options={"target_chunk_words": 30})
    paths = [f"/v1/jobs/{target['job_id']}/result", *[c.audio_url for c in result.chunks[:2]]]
    assert len(paths) == 3
    barrier = Barrier(3)
    original = lesson[3].result

    def read(row):
        output = original(row)
        # All requests must enter while holding their artifact read locks.
        barrier.wait(10)
        return output

    monkeypatch.setattr(lesson[3], "result", read)
    with api(integration, lesson) as client, ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(client.get, paths))
    assert all(response.status_code == 200 for response in responses)
    assert len(access_events(lesson, target["job_id"])) == 3


def test_a5_expiry_revokes_both_endpoints_before_physical_cleanup(integration, lesson):
    target, doc, result = completed(integration, lesson)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                     (target["job_id"],))
    with api(integration, lesson) as client:
        for path in [f"/v1/jobs/{target['job_id']}/result", result.chunks[0].audio_url]:
            response = client.get(path)
            assert response.status_code == 410 and response.json()["detail"] == "LESSON_OUTPUT_EXPIRED"
    assert not access_events(lesson, target["job_id"])
    for row in lesson[1].maintenance():
        lesson[3].complete_cleanup(str(row["job_id"]))
    assert not lesson[2].staging.job_dir(target["job_id"]).exists()
    assert integration[4].staging.source_path(doc["job_id"]).is_file()
    assert integration[3].document_count(doc["document_id"], "course-1") > 0


def test_a5_hides_other_clients_jobs(integration, lesson):
    target, _, result = completed(integration, lesson)
    paths = [f"/v1/jobs/{target['job_id']}/result", result.chunks[0].audio_url]
    principal = lesson[4]
    with api(integration, lesson) as client:
        for candidate, status in [
            (Principal("other-client", principal.courses, principal.scopes), 404),
            (Principal(principal.client_id, ("other-course",), principal.scopes), 404),
        ]:
            app.dependency_overrides[ingress] = lambda: candidate
            assert all(client.get(path).status_code == status for path in paths)
    assert not access_events(lesson, target["job_id"])


def test_a5_missing_chunk_and_corrupt_artifacts_are_not_audited_as_access(integration, lesson):
    target, _, result = completed(integration, lesson)
    folder = lesson[2].staging.job_dir(target["job_id"])
    with api(integration, lesson) as client:
        assert client.get(f"/v1/jobs/{target['job_id']}/audio/{uuid4()}").status_code == 404
        (folder / f"{result.chunks[0].chunk_id}.wav").write_bytes(b"corrupt")
        response = client.get(result.chunks[0].audio_url)
        assert response.status_code == 503 and response.json()["detail"] == "LESSON_AUDIO_INTEGRITY_FAILURE"
        (folder / "result.json").write_text("{}")
        response = client.get(f"/v1/jobs/{target['job_id']}/result")
        assert response.status_code == 503 and response.json()["detail"] == "LESSON_RESULT_INTEGRITY_FAILURE"
    assert not access_events(lesson, target["job_id"])


def test_a5_audit_outage_prevents_delivery_and_closes_audio_stream(integration, lesson, monkeypatch):
    target, _, result = completed(integration, lesson)
    streams = []
    original = Path.open

    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("PRIVATE_DATABASE_MESSAGE")

    def track(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        if path.suffix == ".wav":
            streams.append(stream)
        return stream

    monkeypatch.setattr(lesson[3].audit, "lesson_access", unavailable)
    monkeypatch.setattr(Path, "open", track)
    with api(integration, lesson) as client:
        for path in [f"/v1/jobs/{target['job_id']}/result", result.chunks[0].audio_url]:
            response = client.get(path)
            assert response.status_code == 503 and response.json()["detail"] == "AUDIT_STORAGE_UNAVAILABLE"
            assert "PRIVATE_DATABASE_MESSAGE" not in response.text
    assert streams and all(stream.closed for stream in streams)
    assert not access_events(lesson, target["job_id"])

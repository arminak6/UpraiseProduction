"""Immutable one-chunk revisions with isolated PG/Qdrant and fake providers."""

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from test_a3 import Polly
from test_a3_integration import accept, api, process

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.main import app
from upraise_ai.schemas.lessons import ReviewReport

pytest_plugins = ["test_a3_integration"]


class RevisionPolly(Polly):
    def synthesize_speech(self, **kwargs):
        response = super().synthesize_speech(**kwargs)
        if kwargs["OutputFormat"] == "pcm":
            response["AudioStream"] = io.BytesIO(b"\x01\x00" * 16000)
        return response


def parent(integration, lesson):
    accepted, _, _ = accept(integration, lesson,
        options={"target_chunk_words": 30, "learner_level": "beginner"})
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    result = lesson[3].result(row)
    assert len(result.chunks) > 1
    lesson[2].speech.client = RevisionPolly()
    calls = []

    def revise(section, original, feedback, neighbours, *, learner_level, notify):
        calls.append((section, original, feedback, neighbours, learner_level))
        notify("REVIEW")
        notify("FINALIZE")
        return "A clearer source-supported explanation. [p. 1]", ReviewReport(approved=True, summary="Checked revised draft."), 0

    lesson[5].revise_chunk = revise
    return accepted["job_id"], result, calls


def payload(job_id, result, **updates):
    return {"lesson_job_id": job_id, "chunk_id": result.chunks[1].chunk_id,
            "teacher_review": "Explain this part more simply and remove the final question.", **updates}


def submit_revision(client, body, **kwargs):
    response = client.post("/v1/audio:regenerate", json=body, **kwargs)
    assert response.status_code == 202, response.text
    assert set(response.json()) == {"job_id"}
    assert response.headers["Location"] == "/v1/jobs/" + response.json()["job_id"]
    assert response.headers["cache-control"] == "no-store"
    return response.json()["job_id"]


def test_a7_replaces_existing_chunk_a4_a5_a8_pg_audio_and_removes_old_content(integration, lesson):
    parent_id, original, calls = parent(integration, lesson)
    parent_hash = lesson[1].get(parent_id)["result"]["result_sha256"]
    old_audio = {chunk.chunk_id: (lesson[3].staging.job_dir(parent_id) / f"{chunk.chunk_id}.wav").read_bytes()
                 for chunk in original.chunks}
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, original))
        assert revised_id != parent_id
        assert client.get(f"/v1/jobs/{revised_id}/result").status_code == 409
        row = process(lesson)
        assert str(row["job_id"]) == revised_id and row["status"] == "DONE", row["error_code"]
        status = client.get(f"/v1/jobs/{revised_id}").json()
        assert status["result"]["replacement_target_job_id"] == parent_id
        revised = client.get(f"/v1/jobs/{revised_id}/result").json()
        assert revised["job_id"] == parent_id and "revision" not in revised
        assert revised["status"] == "DRAFT" and revised["learner_level"] == "beginner"
        assert (revised["language"], revised["voice"]) == (original.language, original.voice)
        assert len(revised["chunks"]) == len(original.chunks) and len(calls) == 1
        assert calls[0][1] == original.chunks[1].text and calls[0][4] == "beginner"
        assert {item["chunk_id"] for item in calls[0][0]["chunks"]} == set(original.chunks[1].sources[0].source_chunk_ids)
        prepared = lesson[1].prepared_chunks(parent_id)
        assert lesson[1].prepared_chunks(revised_id) == []
        assert [chunk["text"] for chunk in prepared] == [chunk["text"] for chunk in revised["chunks"]]
        assert [chunk["ssml"] for chunk in prepared] == [chunk["ssml"] for chunk in revised["chunks"]]
        for index, chunk in enumerate(revised["chunks"]):
            assert chunk["chunk_id"] == original.chunks[index].chunk_id and chunk["index"] == index
            audio = client.get(chunk["audio_url"])
            assert audio.status_code == 200 and audio.content.startswith(b"RIFF")
            assert hashlib.sha256(audio.content).hexdigest() == chunk["audio_sha256"]
            assert chunk["visemes"] and chunk["duration_seconds"] > 0
            if index == 1:
                assert chunk["text"].startswith("A clearer") and audio.content != old_audio[chunk["chunk_id"]]
            else:
                assert chunk["text"] == original.chunks[index].text
                assert chunk["ssml"] == original.chunks[index].ssml and audio.content == old_audio[chunk["chunk_id"]]
        assert len(lesson[2].speech.client.calls) == 2  # Edited audio + its visemes only.
        catalog = client.get("/v1/lesson-jobs").json()
        assert {item["job_id"] for item in catalog} == {parent_id}
        assert lesson[1].get(parent_id)["result"]["result_sha256"] != parent_hash
        assert client.get(f"/v1/jobs/{parent_id}/result").json()["chunks"][1]["text"].startswith("A clearer")
        assert not lesson[3].staging.job_dir(parent_id).exists()
        assert not (lesson[3].staging.job_dir(revised_id) / "revision-base.json").exists()
        # A processing-job receipt leaves the stable lesson's delivery intact.
        assert client.delete(f"/v1/jobs/{revised_id}?reason=RECEIVED").status_code == 204
        assert client.get(f"/v1/jobs/{parent_id}/result").status_code == 200
        assert client.delete(f"/v1/jobs/{parent_id}?reason=RECEIVED").status_code == 204
        assert client.get(f"/v1/jobs/{parent_id}/result").status_code == 410
        assert client.get(f"/v1/jobs/{revised_id}/result").status_code == 410
        for chunk in revised["chunks"]:
            assert client.get(chunk["audio_url"]).status_code == 410
        with lesson[1].connect() as conn:
            assert conn.execute("SELECT count(*) AS n FROM prepared_lesson_chunks WHERE job_id=%s", (parent_id,)).fetchone()["n"] == len(original.chunks)


def test_a7_authorization_validation_and_no_idempotency(integration, lesson):
    parent_id, result, _ = parent(integration, lesson)
    body = payload(parent_id, result)
    with api(integration, lesson) as client:
        for updates in ({"teacher_review": " "}, {"teacher_review": "x" * 8001},
                        {"teacher_review": "bad\x08text"}, {"language": "it"}, {"lesson_job_id": "bad"}):
            assert client.post("/v1/audio:regenerate", json={**body, **updates}).status_code == 422
        assert client.post("/v1/audio:regenerate", json={**body, "chunk_id": str(uuid4())}).status_code == 404
        assert client.post("/v1/audio:regenerate", json={**body, "lesson_job_id": str(uuid4())}).status_code == 404
        first = submit_revision(client, body, headers={"Idempotency-Key": "ignored-for-a7"})
        second = submit_revision(client, body, headers={"Idempotency-Key": "ignored-for-a7"})
        assert first != second
        app.dependency_overrides[ingress] = lambda: Principal("other-client", ("course-1",), lesson[4].scopes)
        assert client.post("/v1/audio:regenerate", json=body).status_code == 404
        app.dependency_overrides[ingress] = lambda: Principal(lesson[4].client_id, ("other-course",), lesson[4].scopes)
        assert client.post("/v1/audio:regenerate", json=body).status_code == 404


def test_a7_expired_parent_rejected_but_accepted_snapshot_survives_expiry(integration, lesson):
    parent_id, result, _ = parent(integration, lesson)
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, result))
        with lesson[1].connect() as conn:
            conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (parent_id,))
        assert client.post("/v1/audio:regenerate", json=payload(parent_id, result)).status_code == 410
        lesson[1].maintenance()
        lesson[3].complete_cleanup(parent_id)
        row = process(lesson)
        assert str(row["job_id"]) == revised_id and row["status"] == "FAILED", row["error_code"]
        assert row["error_code"] == "REVISION_TARGET_UNAVAILABLE"
        assert client.get(f"/v1/jobs/{revised_id}/result").status_code == 409


def test_a7_speech_retry_reuses_final_text_and_cancel_preserves_parent(integration, lesson):
    parent_id, result, calls = parent(integration, lesson)
    original_synthesize = lesson[2].speech.synthesize
    lesson[2].speech.synthesize = lambda *a: (_ for _ in ()).throw(IngestionError("EXAMPLE_TTS_DOWN", retryable=True))
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, result))
        row = process(lesson)
        assert row["status"] == "QUEUED" and len(calls) == 1
        assert client.get(f"/v1/jobs/{revised_id}/result").status_code == 409
        assert lesson[1].prepared_chunk(revised_id, result.chunks[1].chunk_id)["text"].startswith("A clearer")
        lesson[2].speech.synthesize = original_synthesize
        with lesson[1].connect() as conn:
            conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (revised_id,))
        assert process(lesson)["status"] == "DONE" and len(calls) == 1
        cancelled_id = submit_revision(client, payload(parent_id, result))
        assert client.delete(f"/v1/jobs/{cancelled_id}?reason=CANCEL").status_code == 204
        assert lesson[1].get(cancelled_id)["status"] == "CANCELLED"
        assert client.get(f"/v1/jobs/{parent_id}/result").status_code == 200


def test_a7_feedback_and_language_level_translation_are_preserved(integration, lesson):
    parent_id, result, calls = parent(integration, lesson)
    # Invented parent translation fixture: re-publish its immutable snapshot
    # consistently before submitting, without any cloud call.
    result.language, result.voice = "it", "Bianca"
    path = lesson[3].staging.job_dir(parent_id) / "result.json"
    raw = result.model_dump_json().encode()
    path.write_bytes(raw)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET metadata=jsonb_set(jsonb_set(metadata,'{language}','\"it\"'),'{voice}', '\"Bianca\"'), "
                     "result=jsonb_set(result,'{result_sha256}',%s::jsonb) WHERE job_id=%s",
                     (json.dumps(hashlib.sha256(raw).hexdigest()), parent_id))
    translations = []
    def translate(draft, source_language, target_language, *, learner_level):
        translations.append((source_language, target_language, learner_level))
        draft.paragraphs[0].text = "Una spiegazione più semplice. [p. 1]"
        return draft
    lesson[5].translate_section = translate
    lesson[2].speech.validate_voice = lambda voice, language: "it-IT"
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, result))
        completed = process(lesson)
        assert completed["status"] == "DONE", completed["error_code"]
        body = client.get(f"/v1/jobs/{revised_id}/result").json()
        assert body["chunks"][1]["text"] == "Una spiegazione più semplice. [p. 1]"
        assert translations == [("en", "it", "beginner")]
        assert calls[0][2] == payload(parent_id, result)["teacher_review"]


def test_a7_concurrent_same_chunk_edit_rejects_stale_snapshot(integration, lesson):
    from upraise_ai.schemas.revisions import ChunkRevisionInput
    from upraise_ai.workflows.revisions import submit_revision as submit_service

    parent_id, result, _ = parent(integration, lesson)
    data = ChunkRevisionInput.model_validate(payload(parent_id, result))
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: submit_service(lesson[3], data, lesson[4]), range(2)))
    assert results[0]["job_id"] != results[1]["job_id"]
    assert process(lesson)["status"] == "DONE"
    failed = process(lesson)
    assert failed["status"] == "FAILED" and failed["error_code"] == "REVISION_TARGET_CHANGED"
    assert lesson[3].result(lesson[1].get(parent_id)).chunks[1].text.startswith("A clearer")


def test_a7_cancel_while_generating_fences_pg_and_preserves_old_audio(integration, lesson):
    parent_id, result, _ = parent(integration, lesson)
    entered, resume = Event(), Event()
    original_revise = lesson[5].revise_chunk
    def pause(*args, **kwargs):
        entered.set()
        assert resume.wait(15)
        return original_revise(*args, **kwargs)
    lesson[5].revise_chunk = pause
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, result))
        claimed = lesson[1].claim(lesson[0].lease_seconds, lesson[0].lesson_signature)
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(lesson[2].process, claimed)
            assert entered.wait(10)
            assert client.delete(f"/v1/jobs/{revised_id}?reason=CANCEL").status_code == 503
            resume.set()
            future.result(timeout=15)
        assert lesson[1].get(revised_id)["status"] == "CANCELLED"
        assert lesson[1].prepared_chunks(revised_id) == []
        assert client.delete(f"/v1/jobs/{revised_id}?reason=CANCEL").status_code == 204
        assert client.get(f"/v1/jobs/{parent_id}/result").status_code == 200
        assert client.get(result.chunks[1].audio_url).status_code == 200


def test_a7_source_revocation_during_generation_prevents_publication(integration, lesson):
    parent_id, result, _ = parent(integration, lesson)
    original_revise = lesson[5].revise_chunk
    def revoke(*args, **kwargs):
        revised = original_revise(*args, **kwargs)
        with integration[1].connect() as conn:
            conn.execute("UPDATE ingestion_jobs SET deletion_requested_at=clock_timestamp() WHERE document_id=%s",
                         (result.chunks[1].sources[0].document_id,))
        return revised
    lesson[5].revise_chunk = revoke
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, result))
        row = process(lesson)
        assert row["status"] == "FAILED" and row["error_code"] == "LESSON_DOCUMENT_UNAVAILABLE"
        assert lesson[1].prepared_chunks(revised_id) == []


def test_a7_snapshot_tampering_fails_before_model_calls(integration, lesson):
    parent_id, result, calls = parent(integration, lesson)
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, result))
        (lesson[3].staging.job_dir(revised_id) / "revision-base.json").write_bytes(b"{}")
        row = process(lesson)
        assert row["status"] == "FAILED" and row["error_code"] == "REVISION_SNAPSHOT_INTEGRITY_FAILURE"
        assert not calls
        assert client.get(f"/v1/jobs/{parent_id}/result").status_code == 200


def test_a7_corrupt_parent_audio_cannot_create_job(integration, lesson):
    parent_id, result, _ = parent(integration, lesson)
    # Replace, rather than mutate in place: previously accepted links are immutable.
    audio = lesson[3].staging.job_dir(parent_id) / f"{result.chunks[0].chunk_id}.wav"
    temp = audio.with_suffix(".bad")
    temp.write_bytes(b"corrupt")
    temp.replace(audio)
    with api(integration, lesson) as client:
        response = client.post("/v1/audio:regenerate", json=payload(parent_id, result))
        assert response.status_code == 503 and response.json()["detail"] == "LESSON_AUDIO_INTEGRITY_FAILURE"
    with lesson[1].connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM lesson_jobs").fetchone()["n"] == 1


def test_a7_second_revision_uses_same_stable_lesson_id(integration, lesson):
    parent_id, result, _ = parent(integration, lesson)
    with api(integration, lesson) as client:
        first_id = submit_revision(client, payload(parent_id, result))
        assert process(lesson)["status"] == "DONE"
        first = lesson[3].result(lesson[1].get(first_id))
        second_id = submit_revision(client, payload(first_id, first, teacher_review="Make the explanation shorter."))
        assert process(lesson)["status"] == "DONE"
        second = client.get(f"/v1/jobs/{second_id}/result").json()
        assert second["job_id"] == parent_id and "revision" not in second
        assert lesson[1].get(parent_id)["result"]["replacement_count"] == 2
        assert lesson[1].prepared_chunks(first_id) == []
        assert not lesson[3].staging.job_dir(first_id).exists()
        assert len(second["chunks"]) == len(result.chunks)


def test_a7_operational_generation_and_tts_controls_reject_before_staging(integration, lesson):
    parent_id, result, _ = parent(integration, lesson)
    initial_settings = lesson[3].settings
    with api(integration, lesson) as client:
        for feature, code in (("generation", "GENERATION_DISABLED"), ("tts", "TTS_DISABLED")):
            lesson[3].settings = initial_settings.model_copy(update={"operations_disabled_features": {feature}})
            response = client.post("/v1/audio:regenerate", json=payload(parent_id, result))
            assert response.status_code == 503 and response.json()["detail"] == code
    lesson[3].settings = initial_settings
    with lesson[1].connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM lesson_jobs").fetchone()["n"] == 1


def test_a7_old_session_is_revoked_and_new_session_reads_current_text(integration, lesson):
    from test_b1_integration import b1_api

    from upraise_ai.schemas.interrupts import InterruptInput
    from upraise_ai.workflows.interrupts import InterruptService

    parent_id, result, _ = parent(integration, lesson)
    body = {"course_id": result.course_id, "activity_id": result.activity_id, "lesson_id": result.lesson_id,
            "lesson_job_id": parent_id, "consent_scope": ["questions"]}
    with b1_api(integration, lesson) as client:
        started = client.post("/v1/sessions", json=body)
        assert started.status_code == 201, started.text
        old_session = app.state.sessions.get(started.json()["session_id"], lesson[4].client_id)
        submit_revision(client, payload(parent_id, result))
        assert process(lesson)["status"] == "DONE"
        new = client.post("/v1/sessions", json=body)
        assert new.status_code == 201, new.text
        new_session = app.state.sessions.get(new.json()["session_id"], lesson[4].client_id)
        assert old_session.binding.lesson_job_id == parent_id
        assert new_session.binding.lesson_job_id == parent_id
        assert old_session.binding.result_sha256 != new_session.binding.result_sha256
        from upraise_ai.core.config import get_settings
        principal = app.dependency_overrides[ingress]()
        interrupts = InterruptService(app.dependency_overrides[get_settings](), lesson[3], app.state.sessions, integration[3])
        message = InterruptInput(request_id=uuid4(), chunk_id=result.chunks[1].chunk_id, text="Explain this part.")
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as changed:
            interrupts.authorized_session(old_session.response.session_id, principal, None)
        assert changed.value.status_code == 409
        _, _, new_text, _ = interrupts._lesson(new_session, message, principal)
        assert new_text.startswith("A clearer") and new_text != result.chunks[1].text

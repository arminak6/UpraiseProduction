"""Final text is committed before TTS and survives temporary-output disposal."""

import psycopg
import pytest
from test_a3_integration import accept, api, process

from upraise_ai.core.errors import IngestionError, LeaseLost

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def test_finalized_text_and_exact_ssml_are_saved_before_synthesis(integration, lesson):
    target, doc, _ = accept(integration, lesson, options={"target_chunk_words": 30})
    lesson[5].reject = True

    def revise(section, draft, report, language):
        lesson[5].revisions += 1
        for paragraph in draft.paragraphs:
            paragraph.text = "Final edited teaching text. " * 20
        return draft

    lesson[5].revise = revise
    original = lesson[2].speech.synthesize
    requests = []

    def synthesize(parts, voice, language):
        # A separate connection proves the content was committed before TTS.
        saved = lesson[1].prepared_chunks(target["job_id"])
        assert saved and all("Final edited teaching text." in row["text"] for row in saved)
        row = saved[len(requests)]
        assert row["ssml_parts"] == parts and row["ssml"]
        assert row["sources"][0]["document_id"] == doc["document_id"]
        assert row["revisions"] == 1
        assert not row["reviewer_report"]["approved"]
        requests.append(parts)
        return original(parts, voice, language)

    lesson[2].speech.synthesize = synthesize
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    result = lesson[3].result(row)
    saved = lesson[1].prepared_chunks(target["job_id"])
    assert len(saved) == len(result.chunks) == len(requests)
    for record, chunk in zip(saved, result.chunks, strict=True):
        assert record["text"] == chunk.text
        assert record["ssml"] == chunk.ssml and record["ssml_parts"] == chunk.ssml_parts
        assert record["versions"]["pipeline"] == row["pipeline_version"]
    with lesson[1].connect() as conn:
        assert conn.execute("SELECT result FROM lesson_jobs WHERE job_id=%s", (target["job_id"],)).fetchone()["result"] == row["result"]
    assert "text" not in row["result"]  # Status/audit summaries remain content-free.


@pytest.mark.parametrize("reason", ["RECEIVED", "TTL"])
def test_text_survives_receipt_or_expiry_while_a5_is_revoked(integration, lesson, reason):
    target, _, _ = accept(integration, lesson)
    process(lesson)
    saved = lesson[1].prepared_chunks(target["job_id"])
    assert saved
    path = "/v1/jobs/" + target["job_id"]
    with api(integration, lesson) as client:
        if reason == "RECEIVED":
            assert client.delete(path, params={"reason": reason}).status_code == 204
        else:
            with lesson[1].connect() as conn:
                conn.execute("UPDATE lesson_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (target["job_id"],))
            for row in lesson[1].maintenance():
                lesson[3].complete_cleanup(str(row["job_id"]))
        assert client.get(path + "/result").status_code == 410
        assert client.get(path + "/audio/" + str(saved[0]["chunk_id"])).status_code == 410
    assert not lesson[2].staging.job_dir(target["job_id"]).exists()
    assert lesson[1].prepared_chunks(target["job_id"]) == saved
    # Additive, repeated migrations preserve retained content.
    lesson[1].migrate()
    lesson[1].migrate()
    assert lesson[1].prepared_chunks(target["job_id"]) == saved


def test_speech_failure_retry_keeps_text_without_duplicate_rows(integration, lesson):
    target, _, _ = accept(integration, lesson)
    speech = lesson[2].speech
    original = speech.synthesize

    def fail(*args):
        assert lesson[1].prepared_chunks(target["job_id"])[0]["ssml_parts"] == args[0]
        raise IngestionError("TEMPORARY_SPEECH", retryable=True)

    speech.synthesize = fail
    assert process(lesson)["status"] == "QUEUED"
    saved = lesson[1].prepared_chunks(target["job_id"])
    assert saved and lesson[5].drafts == 1
    speech.synthesize = original
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    assert lesson[5].drafts == 1
    after = lesson[1].prepared_chunks(target["job_id"])
    assert [(r["chunk_id"], r["text"], r["created_at"]) for r in after] == [
        (r["chunk_id"], r["text"], r["created_at"]) for r in saved]


def test_database_failure_prevents_tts_and_retry_uses_finalized_checkpoint(integration, lesson, monkeypatch):
    target, _, _ = accept(integration, lesson)
    original = lesson[1].save_prepared_text
    synthesized = []
    speech = lesson[2].speech.synthesize
    lesson[2].speech.synthesize = lambda *args: synthesized.append(True) or speech(*args)

    def fail(*args):
        raise psycopg.OperationalError("test storage unavailable")

    monkeypatch.setattr(lesson[1], "save_prepared_text", fail)
    row = process(lesson)
    assert row["status"] == "QUEUED" and row["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert not synthesized and not lesson[1].prepared_chunks(target["job_id"])
    monkeypatch.setattr(lesson[1], "save_prepared_text", original)
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    assert process(lesson)["status"] == "DONE"
    assert lesson[5].drafts == 1 and synthesized


def test_cancellation_fences_text_writes_but_retains_already_finalized_text(integration, lesson):
    target, _, _ = accept(integration, lesson)
    claimed = lesson[1].claim(lesson[0].lease_seconds, lesson[0].lesson_signature)
    worker = lesson[2]
    original = worker.speech.synthesize

    def cancel(*args):
        assert lesson[1].prepared_chunks(target["job_id"])
        with pytest.raises(IngestionError, match="JOB_CLEANUP_PENDING"):
            lesson[3].dispose(target["job_id"], "CANCEL", lesson[4])
        with pytest.raises(LeaseLost), lesson[1].owned_transaction(claimed["job_id"], claimed["lease_token"]):
            pytest.fail("Cancelled worker must not own a content-write transaction")
        return original(*args)

    worker.speech.synthesize = cancel
    worker.process(claimed)
    row = lesson[1].get(target["job_id"])
    assert row["status"] == "CANCELLED" and row["cleanup_completed_at"]
    assert lesson[1].prepared_chunks(target["job_id"])
    assert not worker.staging.job_dir(target["job_id"]).exists()

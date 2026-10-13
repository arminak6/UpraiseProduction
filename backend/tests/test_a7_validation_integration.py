"""A7 correction, reason delivery, atomic publication and cleanup with fake providers."""

import hashlib

import pytest
from test_a3_integration import api, process
from test_a7_all_levels import body, setup_revision, submit

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedReview
from upraise_ai.services.revision_models import RevisionModelInvalidOutput

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def test_a7_invalid_final_output_reports_safe_reason_preserves_lesson_and_cleans_up(integration, lesson, monkeypatch):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    before = lesson[1].get(row["job_id"])["result"]

    def invalid(*args, learner_level, **kwargs):
        raise RevisionModelInvalidOutput(ValueError("The final lecture cites pages outside the supplied source."),
            stage="revision_finalize", learner_level=learner_level, correction_rounds=2)

    monkeypatch.setattr(lesson[2].models, "revise_chunk", invalid)
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        failed = process(lesson)
        assert failed["status"] == "FAILED" and failed["attempts"] == 1
        error = client.get(f"/v1/jobs/{work}").json()["error"]
        assert error["code"] == "REVISION_MODEL_INVALID_OUTPUT"
        assert error["reason_code"] == "CITATION_OUTSIDE_SOURCE" and error["reason"]
        assert error["stage"] == "revision_finalize" and error["learner_level"] == "beginner"
        assert error["chunk_index"] == 1 and error["correction_rounds"] == 2
        assert lesson[1].get(row["job_id"])["result"] == before
        assert lesson[3].result(row).model_dump() == original.model_dump()
        assert lesson[1].prepared_chunks(work) == []
        assert client.delete(f"/v1/jobs/{work}?reason=RECEIVED").status_code == 204
        assert lesson[1].get(work)["result"] is None
        assert client.get(f"/v1/jobs/{work}").json()["error"] == {"code": "REVISION_MODEL_INVALID_OUTPUT"}
        assert client.get(f"/v1/jobs/{row['job_id']}/result").status_code == 200


def review(units, accepted=False):
    return AlignedReview(units=[{"content_unit_id": unit.content_unit_id, "material_preserved": accepted,
        "level_suitable": True, "in_language": True,
        "issues": [] if accepted else ["A required fact from the teaching topic is missing."],
        "revision_instructions": [] if accepted else ["Restore that fact while omitting lecturer attribution."]}
        for unit in units])


def configure(integration, lesson, monkeypatch, *, always_reject=False):
    row, original, generation = setup_revision(integration, lesson, monkeypatch)
    reviews, corrections = [], []

    def reviewer(models, section, units, texts, language, level, profiles):
        candidate = texts.units[0].text
        reviews.append((level, candidate, section["revision_context"]))
        return review(units, not always_reject and candidate.startswith("Corrected"))

    def correct(models, section, candidate, feedback, neighbours, report, language, level):
        corrections.append((level, feedback, report))
        assert report.units[0].revision_instructions and language == original.language
        page = section["alignment_unit"]["source"]["pages"][0]
        return f"Corrected {level} explanation preserving the complete teaching topic and every required fact. [p. {page}]"

    monkeypatch.setattr("upraise_ai.services.revision_pipeline.reviewed", reviewer)
    monkeypatch.setattr("upraise_ai.services.revision_validation.correct_revision", correct)
    return row, original, generation, reviews, corrections


@pytest.mark.parametrize("all_levels", [False, True])
def test_a7_rejected_candidate_is_corrected_before_any_replacement(integration, lesson, monkeypatch, all_levels):
    row, original, _, reviews, corrections = configure(integration, lesson, monkeypatch)
    with api(integration, lesson) as client:
        payload = body(row, original, all_levels=all_levels)
        payload["teacher_review"] = "Remove the professor's name, preserving the teaching facts."
        work = submit(client, payload)
        done = process(lesson)
        assert done["status"] == "DONE", done["error_code"]
        expected = list(original.variants) if all_levels else ["advanced"]
        assert [level for level, _, _ in corrections] == expected
        assert all(feedback == payload["teacher_review"] for _, feedback, _ in corrections)
        assert len(reviews) == 2 * len(expected)
        assert all(context["teacher_feedback"] == payload["teacher_review"] for _, _, context in reviews)
        current = lesson[3].result(row)
        assert current.job_id == original.job_id and lesson[1].prepared_chunks(work) == []
        for level, variant in current.variants.items():
            for index, chunk in enumerate(variant.chunks):
                previous = original.variants[level].chunks[index]
                if level in expected and index == 1:
                    assert chunk.text.startswith("Corrected")
                    assert variant.versions["revision_correction_rounds"] == "1"
                    assert lesson[1].prepared_chunk(row["job_id"], chunk.chunk_id)["text"] == chunk.text
                else:
                    assert chunk.model_dump() == previous.model_dump()
                audio = client.get(chunk.audio_url)
                assert hashlib.sha256(audio.content).hexdigest() == chunk.audio_sha256


def test_a7_exhausted_correction_reports_reason_and_receipt_disposes_findings(integration, lesson, monkeypatch):
    row, original, _, reviews, corrections = configure(integration, lesson, monkeypatch, always_reject=True)
    before = lesson[1].get(row["job_id"])["result"]
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        failed = process(lesson)
        assert failed["status"] == "FAILED" and failed["error_code"] == "REVISION_ALIGNMENT_REJECTED"
        assert failed["attempts"] == 1 and len(corrections) == 2
        assert {level for level, _, _ in corrections} == {"beginner"}
        error = client.get(f"/v1/jobs/{work}").json()["error"]
        assert error["learner_level"] == "beginner" and error["chunk_index"] == 1
        assert error["correction_rounds"] == 2 and error["max_correction_rounds"] == 2
        assert "required fact" in error["reason"] and error["review"]["material_preserved"] is False
        assert error["review"]["revision_instructions"]
        assert len(reviews) <= 3
        assert lesson[1].get(row["job_id"])["result"] == before
        assert lesson[3].result(row).model_dump() == original.model_dump()
        assert lesson[1].prepared_chunks(work) == []
        assert client.delete(f"/v1/jobs/{work}?reason=RECEIVED").status_code == 204
        assert lesson[1].get(work)["result"] is None
        assert client.get(f"/v1/jobs/{work}").json()["error"] == {"code": "REVISION_ALIGNMENT_REJECTED"}
        assert client.get(f"/v1/jobs/{row['job_id']}/result").status_code == 200


def test_a7_accepted_corrected_text_and_review_are_reused_after_tts_outage(integration, lesson, monkeypatch):
    row, original, generation, reviews, corrections = configure(integration, lesson, monkeypatch)
    synthesize = lesson[2].speech.synthesize
    failed_once = False

    def outage(*args):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise IngestionError("INVENTED_TTS_OUTAGE", retryable=True)
        return synthesize(*args)

    monkeypatch.setattr(lesson[2].speech, "synthesize", outage)
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        assert process(lesson)["status"] == "QUEUED"
        assert [call[0] for call in generation] == ["beginner"]
        assert [level for level, _, _ in corrections] == ["beginner"]
        assert len(reviews) == 2
        with lesson[1].connect() as conn:
            conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (work,))
        done = process(lesson)
        assert done["status"] == "DONE", done["error_code"]
        assert [call[0] for call in generation] == ["beginner", "intermediate", "advanced"]
        assert [level for level, _, _ in corrections] == ["beginner", "intermediate", "advanced"]
        assert len(reviews) == 6


def test_a7_failure_in_later_level_preserves_entire_bundle_and_reports_that_level(integration, lesson, monkeypatch):
    row, original, _, reviews, _ = configure(integration, lesson, monkeypatch)

    def reject_advanced(models, section, units, texts, language, level, profiles):
        reviews.append((level, texts.units[0].text, section["revision_context"]))
        return review(units, level != "advanced")

    monkeypatch.setattr("upraise_ai.services.revision_pipeline.reviewed", reject_advanced)
    before = lesson[1].get(row["job_id"])["result"]
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        assert process(lesson)["status"] == "FAILED"
        error = client.get(f"/v1/jobs/{work}").json()["error"]
        assert error["learner_level"] == "advanced" and error["correction_rounds"] == 2
        assert lesson[1].get(row["job_id"])["result"] == before
        assert lesson[3].result(row).model_dump() == original.model_dump()
        assert len(lesson[1].prepared_chunks(work)) == 2 * len(original.variants["beginner"].chunks)
        assert client.delete(f"/v1/jobs/{work}?reason=RECEIVED").status_code == 204
        assert lesson[1].prepared_chunks(work) == [] and lesson[1].get(work)["result"] is None

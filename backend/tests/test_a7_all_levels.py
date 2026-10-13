"""Atomic teaching-unit revision across aligned levels with isolated fake providers."""

import hashlib
from types import SimpleNamespace

import pytest
from test_a3_integration import api, process
from test_a3_v2_integration import accept_bundle
from test_a7_integration import RevisionPolly, parent

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedReview
from upraise_ai.schemas.lessons import ReviewReport

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


def setup_revision(integration, lesson, monkeypatch, **options):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch, **options)
    original = lesson[3].result(row)
    calls = []
    lesson[5].transport = SimpleNamespace(language=original.language, teaching_prompt=None)
    if original.language == "en":
        lesson[2].speech.client = RevisionPolly()
    else:
        from test_a3_translation import TranslationClient

        from upraise_ai.services.lesson_translation import LessonTranslator

        def translated(result, payload):
            for paragraph in result["paragraphs"]:
                paragraph["text"] = paragraph["text"].replace("A clearer", "Una spiegazione piu chiara").replace(
                    "source-based teaching explanation", "basata sul materiale didattico")
            return result

        translator = LessonTranslator(lesson[0], TranslationClient(transform=translated))
        monkeypatch.setattr(lesson[5], "translate_section", lambda draft, source, target, *, learner_level:
                            translator.translate(draft, source, target, learner_level))

    def revise(section, text, feedback, neighbours, *, learner_level, notify):
        calls.append((learner_level, text, feedback, section["alignment_unit"]["content_unit_id"], neighbours))
        notify("REVIEW")
        notify("FINALIZE")
        revised_text = (text + f" Clearer {learner_level} explanation." if original.language == "en"
                        else f"A clearer {learner_level} source-based teaching explanation. [p. 1]")
        return (revised_text,
                ReviewReport(approved=True, summary="Invented revision checked."), 0)

    monkeypatch.setattr(lesson[5], "revise_chunk", revise, raising=False)
    monkeypatch.setattr("upraise_ai.services.revision_pipeline.reviewed", lambda models, section, units, *args:
        AlignedReview(units=[{"content_unit_id": unit.content_unit_id, "material_preserved": True,
                             "level_suitable": True, "in_language": True} for unit in units]))
    return row, original, calls


def body(row, original, *, level=None, index=1, all_levels=True):
    level = level or list(original.variants)[-1]
    return {"lesson_job_id": str(row["job_id"]), "chunk_id": original.variants[level].chunks[index].chunk_id,
            "teacher_review": "Clarify this teaching topic.", "apply_to_all_levels": all_levels}


def submit(client, payload):
    response = client.post("/v1/audio:regenerate", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


@pytest.mark.parametrize("language,profiles", [("en", None), ("it", None),
    ("en", {"foundation": "Plain steps.", "expert": "Formal reasoning."})])
def test_a7_all_levels_replaces_only_matching_unit_and_preserves_stable_lesson(
        integration, lesson, monkeypatch, language, profiles):
    row, original, calls = setup_revision(integration, lesson, monkeypatch, language=language, profiles=profiles)
    events = []
    lesson[2].notify = lambda *event: events.append(event)
    source_plan = lesson[1].get_plan(row["job_id"])
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        done = process(lesson)
        assert done["status"] == "DONE", done["error_code"]
        current = lesson[3].result(row)
        summary = client.get(f"/v1/jobs/{work}").json()["result"]
        assert summary["replacement_target_job_id"] == str(row["job_id"])
        assert summary["apply_to_all_levels"] is True
        assert set(summary["revised_levels"]) == set(original.variants)
        assert summary["learner_level"] == list(original.variants)[-1]
        assert [call[0] for call in calls] == list(original.variants)
        assert all(call[2] == body(row, original)["teacher_review"] for call in calls)
        assert all(call[3] == str(original.content_unit_ids[1]) for call in calls)
        assert current.job_id == original.job_id and current.content_unit_ids == original.content_unit_ids
        for level, variant in current.variants.items():
            assert calls[list(original.variants).index(level)][1] == original.variants[level].chunks[1].text
            assert variant.language == language and variant.learner_level == level and variant.voice == original.voice
            assert summary["chunk_ids"][level] == variant.chunks[1].chunk_id
            for index, chunk in enumerate(variant.chunks):
                before = original.variants[level].chunks[index]
                assert chunk.chunk_id == before.chunk_id and chunk.content_unit_id == before.content_unit_id
                if index == 1:
                    assert chunk.text != before.text and chunk.ssml != before.ssml
                else:
                    assert chunk.model_dump() == before.model_dump()
                audio = client.get(chunk.audio_url)
                assert audio.status_code == 200 and hashlib.sha256(audio.content).hexdigest() == chunk.audio_sha256
                saved = lesson[1].prepared_chunk(row["job_id"], chunk.chunk_id)
                assert saved["text"] == chunk.text and saved["ssml"] == chunk.ssml
        assert lesson[1].prepared_chunks(work) == []
        assert lesson[1].get_plan(row["job_id"]) == source_plan
        assert {item["job_id"] for item in client.get("/v1/lesson-jobs").json()} == {original.job_id}
        assert client.get(f"/v1/jobs/{work}/result").json()["job_id"] == original.job_id
        assert ("workload", "lesson", 5 * len(original.variants)) in events
        assert ("workload", "speech", len(original.variants)) in events
        fractions = [event[2] for event in events if event[0] == "progress"]
        assert fractions == sorted(fractions)
        folder = lesson[3].staging.job_dir(work)
        assert {path.name for path in folder.iterdir()} == {"current-result.json", *{f"{chunk.chunk_id}.wav" for chunk in current.chunks}}


@pytest.mark.parametrize("failure_stage", ["last_level", "publication"])
def test_a7_all_levels_failure_preserves_every_current_level(integration, lesson, monkeypatch, failure_stage):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    before = lesson[1].get(row["job_id"])["result"]
    if failure_stage == "last_level":
        revise = lesson[5].revise_chunk

        def fail_last(*args, **kwargs):
            if kwargs["learner_level"] == "advanced":
                raise IngestionError("INVENTED_LAST_LEVEL_FAILURE")
            return revise(*args, **kwargs)

        monkeypatch.setattr(lesson[5], "revise_chunk", fail_last)
    else:
        from upraise_ai.workers import lessons as workers
        publish = workers.publish_replacement

        def fail_publication(*args, **kwargs):
            publish(*args, **kwargs)
            raise IngestionError("INVENTED_PUBLICATION_FAILURE")

        monkeypatch.setattr(workers, "publish_replacement", fail_publication)
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        failed = process(lesson)
        assert failed["status"] == "FAILED", failed
        assert lesson[1].get(row["job_id"])["result"] == before
        assert lesson[3].result(row).model_dump() == original.model_dump()
        assert client.get(f"/v1/jobs/{work}/result").status_code == 409
        for chunk in original.chunks:
            assert lesson[1].prepared_chunk(row["job_id"], chunk.chunk_id)["text"] == chunk.text
            assert hashlib.sha256(client.get(chunk.audio_url).content).hexdigest() == chunk.audio_sha256


def test_a7_all_levels_retry_reuses_completed_level_checkpoints(integration, lesson, monkeypatch):
    row, original, calls = setup_revision(integration, lesson, monkeypatch)
    before = lesson[1].get(row["job_id"])["result"]
    revise = lesson[5].revise_chunk
    interrupted = False

    def fail_once(*args, **kwargs):
        nonlocal interrupted
        if kwargs["learner_level"] == "intermediate" and not interrupted:
            interrupted = True
            raise IngestionError("INVENTED_PROVIDER_OUTAGE", retryable=True)
        return revise(*args, **kwargs)

    monkeypatch.setattr(lesson[5], "revise_chunk", fail_once)
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))
        assert process(lesson)["status"] == "QUEUED"
        assert lesson[1].get(row["job_id"])["result"] == before
        with lesson[1].connect() as conn:
            conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (work,))
        done = process(lesson)
        assert done["status"] == "DONE", done["error_code"]
        assert [call[0] for call in calls] == ["beginner", "intermediate", "advanced"]


def test_a7_all_levels_fails_if_any_target_level_changed_while_queued(integration, lesson, monkeypatch):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    with api(integration, lesson) as client:
        submit(client, body(row, original, all_levels=False))
        work = submit(client, body(row, original))
        assert process(lesson)["status"] == "DONE"
        latest = lesson[1].get(row["job_id"])["result"]
        failed = process(lesson)
        assert failed["status"] == "FAILED" and failed["error_code"] == "REVISION_TARGET_CHANGED"
        assert lesson[1].get(row["job_id"])["result"] == latest
        assert client.get(f"/v1/jobs/{work}/result").status_code == 409


def test_a7_all_levels_merges_unrelated_chunk_changes(integration, lesson, monkeypatch):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    with api(integration, lesson) as client:
        submit(client, body(row, original, index=0, all_levels=False))
        submit(client, body(row, original))
        assert process(lesson)["status"] == "DONE"
        assert process(lesson)["status"] == "DONE"
        current = lesson[3].result(row)
        assert current.variants["advanced"].chunks[0].text != original.variants["advanced"].chunks[0].text
        for level in current.variants:
            assert current.variants[level].chunks[1].text != original.variants[level].chunks[1].text
            if level != "advanced":
                assert current.variants[level].chunks[0].text == original.variants[level].chunks[0].text


def test_a7_all_levels_cancel_after_first_level_preserves_current_lesson(integration, lesson, monkeypatch):
    row, original, _ = setup_revision(integration, lesson, monkeypatch)
    before = lesson[1].get(row["job_id"])["result"]
    revise = lesson[5].revise_chunk
    with api(integration, lesson) as client:
        work = submit(client, body(row, original))

        def cancel_second(*args, **kwargs):
            if kwargs["learner_level"] == "intermediate":
                assert client.delete(f"/v1/jobs/{work}?reason=CANCEL").status_code == 503
            return revise(*args, **kwargs)

        monkeypatch.setattr(lesson[5], "revise_chunk", cancel_second)
        assert process(lesson)["status"] == "CANCELLED"
        assert lesson[1].get(row["job_id"])["result"] == before
        assert lesson[3].result(row).model_dump() == original.model_dump()
        assert lesson[1].prepared_chunks(work) == []
        assert client.delete(f"/v1/jobs/{work}?reason=CANCEL").status_code == 204


def test_a7_all_levels_requires_aligned_lesson_and_real_boolean(integration, lesson):
    job_id, original, _ = parent(integration, lesson)
    payload = {"lesson_job_id": job_id, "chunk_id": original.chunks[0].chunk_id, "teacher_review": "Clarify."}
    with api(integration, lesson) as client:
        for value in ("true", 1, None):
            assert client.post("/v1/audio:regenerate", json={**payload, "apply_to_all_levels": value}).status_code == 422
        response = client.post("/v1/audio:regenerate", json={**payload, "apply_to_all_levels": True})
        assert response.status_code == 409 and response.json()["detail"] == "REVISION_ALL_LEVELS_UNAVAILABLE"

"""V3 API/worker/course-preflight/A7 behavior with fake providers and real storage."""

import json
import re

import pytest
from test_a2_integration import submit
from test_a3 import Polly, request_data
from test_a3_integration import api
from test_teaching_v2 import PASS
from test_teaching_v2_integration import V2Client

from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.teaching_v3 import TeachingModelsV3, TeachingSpeechV3

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


class V3Client(V2Client):
    def __init__(self, *, inconsistent=False, reject_spoken=False):
        super().__init__()
        self.inconsistent, self.reject_spoken = inconsistent, reject_spoken

    def converse(self, **request):
        name = request.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {}).get("name", "")
        if not name.startswith("teaching_v3_"):
            return super().converse(**request)
        payload, _ = json.JSONDecoder().raw_decode(request["messages"][0]["content"][0]["text"])
        if name == "teaching_v3_section_plan":
            value = {"title": "Observed feature", "concepts": [{"source_index": 0, "symbol": ""}], "join_next": False}
        elif name == "teaching_v3_section":
            text = "The supported feature has value three. " * 6 + "[p. 1]"
            value = {"paragraphs": [{"text": text, "pages": [1]} for _ in range(3)]}
        elif name in {"teaching_v3_narration", "teaching_v3_narration_correction"}:
            value = {"text": re.sub(r"\[p\. \d+\]", "", payload["current_written"]).strip()}
        elif name == "teaching_v3_narration_review":
            value = {**PASS, "correct": not self.reject_spoken,
                     "issues": ["Changed mathematical meaning."] if self.reject_spoken else []}
        elif name == "teaching_v3_course_review":
            value = {"consistent": not self.inconsistent, "connected": True, "complete": True,
                     "issues": ["The symbol changes meaning."] if self.inconsistent else []}
        elif name in {"teaching_v3_coverage", "teaching_v3_spoken_coverage"}:
            value = {"omitted_available": [], "language_errors": [], "warnings": []}
        elif name == "teaching_v3_conflict_verification":
            value = {"kind": "confirmed_conflict" if self.inconsistent else "not_found",
                     "reason": "Inspect the actual variable definitions.",
                     "evidence_indices": [0] if self.inconsistent else []}
        elif name == "teaching_v3_conflict_binding":
            value = {"blocking": self.inconsistent, "reason": "Check the actual selected assertions."}
        elif name == "teaching_v3_continuity_correction":
            value = {"text": payload["current"]}
        else:
            return super().converse(**request)
        self.calls.append(request)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps(value)}]}}}


def generate_v3(integration, lesson, monkeypatch, *, legacy=False, inconsistent=False, reject_spoken=False, levels=None,
                invalid_evidence=False):
    document = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    settings, jobs, worker, service, principal, _ = lesson
    client = V3Client(inconsistent=inconsistent, reject_spoken=reject_spoken)
    client.invalid_evidence = invalid_evidence
    expected = 3 * len(levels or ["intermediate"])

    class TrackedPolly(Polly):
        def synthesize_speech(self, **request):
            names = [c.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {}).get("name") for c in client.calls]
            # Identical reviewed text/context may share a valid script cache
            # across levels, but every selected chunk must be preflighted before
            # the first synthesis request.
            assert len(speech.scripts) == expected
            assert names.count("teaching_v3_narration_review") >= 3
            assert any('"kind": "spoken"' in c["messages"][0]["content"][0]["text"] and
                       '"part_index": 2' in c["messages"][0]["content"][0]["text"] for c in client.calls)
            return super().synthesize_speech(**request)

    models = TeachingModelsV3(settings, client)
    polly = TrackedPolly()
    speech = TeachingSpeechV3(settings, models, client=polly)
    monkeypatch.setattr("upraise_ai.services.teaching_v3.TeachingModelsV3", lambda *a, **k: (setattr(models, "diagnostic_dir", k["diagnostic_dir"]) or models))
    monkeypatch.setattr("upraise_ai.services.teaching_v3.TeachingSpeechV3", lambda *a, **k: speech)
    worker.models = worker.speech = None
    payload = request_data(document_ids=[document["document_id"]], teaching_version="v3",
                           options={"target_chunk_words": 30})
    if not legacy:
        payload.update(levels=levels or ["intermediate"], options={"target_chunk_words": 30, "max_chunk_words": 100})
    data = (LessonInput if legacy else AlignedLessonInput).model_validate(payload)
    accepted = service.submit(data, principal, "continuous-v3")
    worker.process(jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature]))
    return jobs.get(accepted["job_id"]), client, polly, models, speech


@pytest.mark.parametrize("legacy", [False, True])
def test_teaching_v3_worker_prepares_all_scripts_before_tts_and_exposes_version(integration, lesson, monkeypatch, legacy):
    row, client, polly, models, speech = generate_v3(integration, lesson, monkeypatch, legacy=legacy)
    assert row["status"] == "DONE", row["error_code"]
    assert polly.calls and row["metadata"]["teaching_version"] == "v3"
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        assert review["teaching_version"] == "v3"
        chunks = review["variants"]["intermediate"]["chunks"]
        assert len(chunks) == 3 and all(c["narration_text"] for c in chunks)
    before = len(client.calls)
    speech.prepare_course({"intermediate": [(str(c["chunk_id"]), c["text"], None, None, c["section_index"], 1)
                                           for c in lesson[1].prepared_chunks(row["job_id"])]},
                          "en", "different-signature", lambda *a: None)
    # A changed full-course signature must not reuse a prior paid-review decision.
    assert len(client.calls) > before
    before = len(client.calls)
    speech.prepare_course({"intermediate": [(str(c["chunk_id"]), c["text"], None, None, c["section_index"], 1)
                                           for c in lesson[1].prepared_chunks(row["job_id"])]},
                          "en", "different-signature", lambda *a: None)
    assert len(client.calls) == before
    models.teaching_versions["spoken_sequence_review"] = "changed-sequence-only-policy"
    speech.prepare_course({"intermediate": [(str(c["chunk_id"]), c["text"], None, None, c["section_index"], 1)
                                           for c in lesson[1].prepared_chunks(row["job_id"])]},
                          "en", "different-signature", lambda *a: None)
    assert len(client.calls) > before
    assert models.teaching_versions["course_order"] == "document-order"


def test_teaching_v3_inconsistent_course_finishes_with_persistent_review_flags(integration, lesson, monkeypatch):
    row, _, polly, _, _ = generate_v3(integration, lesson, monkeypatch, inconsistent=True)
    assert row["status"] == "DONE" and polly.calls
    assert row["result"]["lesson_status"] == "DRAFT" and not row["result"]["review_approved"]
    saved = lesson[1].prepared_chunks(row["job_id"])
    assert all(any("TEACHING_V3_COURSE_INCONSISTENT" in w["message"] for w in c["reviewer_report"]["issues"]) for c in saved)


def test_teaching_v3_wrong_spoken_meaning_finishes_with_persistent_review_flags(integration, lesson, monkeypatch):
    row, _, polly, _, _ = generate_v3(integration, lesson, monkeypatch, reject_spoken=True)
    assert row["status"] == "DONE" and polly.calls
    assert not row["result"]["review_approved"]
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        chunks = review["variants"]["intermediate"]["chunks"]
        assert len(chunks) == 3 and all(not c["reviewer_report"]["approved"] and c["narration_text"] for c in chunks)
        assert all(any("TEACHING_V3_NARRATION_REJECTED" in w["message"] for w in c["reviewer_report"]["issues"]) for c in chunks)


def test_teaching_v3_unreadable_written_and_spoken_reviews_complete_all_parts(integration, lesson, monkeypatch):
    row, _, polly, _, _ = generate_v3(integration, lesson, monkeypatch, invalid_evidence=True)
    assert row["status"] == "DONE" and row["attempts"] == 1 and polly.calls, row["error_code"]
    chunks = lesson[1].prepared_chunks(row["job_id"])
    assert len(chunks) == 3 and all(not c["reviewer_report"]["approved"] for c in chunks)
    assert all(any("written material review" in w["message"] for w in c["reviewer_report"]["issues"]) for c in chunks)
    assert all(any("narration review" in w["message"] for w in c["reviewer_report"]["issues"]) for c in chunks)


def test_teaching_v3_every_selected_level_is_preflighted_before_any_audio(integration, lesson, monkeypatch):
    row, _, polly, _, _ = generate_v3(integration, lesson, monkeypatch, levels=["intermediate", "advanced"])
    assert row["status"] == "DONE", row["error_code"]
    assert polly.calls and set(lesson[3].result(row).variants) == {"intermediate", "advanced"}


def test_teaching_v3_teacher_revision_keeps_version_and_other_chunks(integration, lesson, monkeypatch):
    row, client, polly, _, _ = generate_v3(integration, lesson, monkeypatch)
    before = lesson[3].result(row).variants["intermediate"]
    other = {c.chunk_id: (c.text, c.audio_sha256) for c in before.chunks[1:]}
    with api(integration, lesson) as http:
        response = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": before.chunks[0].chunk_id, "teacher_review": "Keep the measured value and continue the same course."})
        assert response.status_code == 202, response.text
    job = lesson[1].claim(lesson[0].lease_seconds, [lesson[0].lesson_signature, lesson[0].lesson_v2_signature])
    assert str(job["job_id"]) == response.json()["job_id"] and job["metadata"]["teaching_version"] == "v3"
    count = len(polly.calls)
    lesson[2].process(job)
    done = lesson[1].get(job["job_id"])
    assert done["status"] == "DONE", done["error_code"]
    after = lesson[3].result(lesson[1].get(row["job_id"])).variants["intermediate"]
    assert after.versions["teaching_version"] == "v3" and len(polly.calls) > count
    assert {c.chunk_id: (c.text, c.audio_sha256) for c in after.chunks[1:]} == other
    assert any('"kind": "spoken_revision"' in c["messages"][0]["content"][0]["text"] for c in client.calls)

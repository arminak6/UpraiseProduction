"""V3.1 API, real storage, worker dispatch, narration preflight and revision isolation."""

import json
import re

import pytest
from test_a2_integration import submit
from test_a3 import Polly, request_data
from test_a3_integration import api
from test_teaching_v3_integration import V3Client

from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.teaching_v31 import TeachingModelsV31, TeachingSpeechV31

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


class V31Client(V3Client):
    def __init__(self, repair_failures=0, reject_repairs=False):
        super().__init__()
        self.repair_failures = repair_failures
        self.reject_repairs = reject_repairs
        self.needs_repair = repair_failures > 0 or reject_repairs

    def converse(self, **request):
        name = request.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {}).get("name", "")
        if not name and self.repair_failures:
            payload, _ = json.JSONDecoder().raw_decode(request["messages"][0]["content"][0]["text"])
            if "target" in payload:
                name = "teaching_v31_target_repair"
        if not name.startswith("teaching_v31_"):
            return super().converse(**request)
        self.calls.append(request)
        payload, _ = json.JSONDecoder().raw_decode(request["messages"][0]["content"][0]["text"])
        if name == "teaching_v31_section":
            value = {"paragraphs": [{"text": "The supported feature has value three. " * 6,
                                    "evidence_ids": [payload["evidence"][0]["chunk_id"]]} for _ in range(3)]}
            if self.needs_repair:
                value["paragraphs"][1]["text"] = "An invented claim is here. " * 8
        elif name == "teaching_v31_revision":
            value = {"paragraphs": [{"text": re.sub(r"\[(?:p|pp)\.[^\]]*\]", "", payload["current_written"]).strip(),
                                    "evidence_ids": [payload["evidence"][0]["chunk_id"]]}]}
        elif name == "teaching_v31_adaptation":
            value = {"units": [{"content_unit_id": u["content_unit_id"],
                "text": re.sub(r"\[(?:p|pp)\.[^\]]*\]", "", u["material_text"]).strip(),
                "evidence_ids": u["source"]["source_chunk_ids"]} for u in payload["fixed_units"]]}
        elif name.startswith("teaching_v31_support"):
            value = {"supported": "invented" not in payload["candidate"], "reason": "Check the supplied records."}
        elif name == "teaching_v31_target_repair":
            value = {"text": "The supported feature has value three. " * 6,
                     "evidence_ids": [payload["evidence"][0]["chunk_id"]]}
            if self.reject_repairs:
                value["text"] = "An invented claim is still here. " * 6
            if self.repair_failures:
                self.repair_failures -= 1
                value = {"patches": [{"candidate_index": 999, **value}]}
        elif name.startswith("teaching_v31_coverage"):
            value = {"omitted_available": [], "language_errors": [], "warnings": []}
        elif name == "teaching_v31_level":
            value = {"suitable": True, "reason": "This fixture meets its learning task."}
        elif name == "teaching_v31_speech_task":
            value = {"preserved": True, "reason": "The task and subject are preserved."}
        else:
            raise AssertionError(name)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps(value)}]}}}


def generate(integration, lesson, monkeypatch, legacy=False, repair_failures=0, reject_repairs=False):
    document = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    settings, jobs, worker, service, principal, _ = lesson
    client = V31Client(repair_failures=repair_failures, reject_repairs=reject_repairs)
    models = TeachingModelsV31(settings, client)

    class PreflightPolly(Polly):
        def synthesize_speech(self, **request):
            assert len(speech.scripts) == len(jobs.prepared_chunks(accepted["job_id"])) > 0
            return super().synthesize_speech(**request)

    polly = PreflightPolly()
    speech = TeachingSpeechV31(settings, models, client=polly)
    monkeypatch.setattr("upraise_ai.services.teaching_v31.TeachingModelsV31",
        lambda *a, **k: (setattr(models, "diagnostic_dir", k["diagnostic_dir"]) or models))
    monkeypatch.setattr("upraise_ai.services.teaching_v31.TeachingSpeechV31", lambda *a, **k: speech)
    worker.models = worker.speech = None
    payload = request_data(document_ids=[document["document_id"]], teaching_version="v3.1",
                           options={"target_chunk_words": 30})
    if not legacy:
        payload.update(levels=["beginner", "intermediate", "advanced"],
                       options={"target_chunk_words": 30, "max_chunk_words": 100})
    data = (LessonInput if legacy else AlignedLessonInput).model_validate(payload)
    accepted = service.submit(data, principal, "source-preserving-v31")
    worker.process(jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature]))
    return jobs.get(accepted["job_id"]), polly


@pytest.mark.parametrize("legacy", [False, True])
def test_teaching_v31_dispatch_api_catalog_narration_and_strict_revision(integration, lesson, monkeypatch, legacy):
    row, polly = generate(integration, lesson, monkeypatch, legacy)
    assert row["status"] == "DONE", row["error_code"]
    assert row["metadata"]["teaching_version"] == "v3.1" and polly.calls
    with api(integration, lesson) as http:
        response = http.get(f"/v1/lesson-reviews/{row['job_id']}")
        assert response.status_code == 200, response.text
        review = response.json()
        assert review["teaching_version"] == "v3.1"
        assert all(c["narration_text"] for variant in review["variants"].values() for c in variant["chunks"])
        catalog = http.get("/v1/lesson-jobs").json()
        assert "v3.1" in json.dumps(catalog)
        if legacy:
            return
        before = lesson[3].result(row)
        selected = before.variants["intermediate"].chunks[0]
        other = {c.chunk_id: (c.text, c.audio_sha256) for c in before.chunks if c.chunk_id != selected.chunk_id}
        response = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": selected.chunk_id, "teacher_review": "Preserve the source value and explain it clearly."})
        assert response.status_code == 202, response.text
    job = lesson[1].claim(lesson[0].lease_seconds, [lesson[0].lesson_signature, lesson[0].lesson_v2_signature])
    assert job["metadata"]["teaching_version"] == "v3.1"
    lesson[2].process(job)
    revised = lesson[1].get(job["job_id"])
    assert revised["status"] == "DONE", revised["error_code"]
    after = lesson[3].result(lesson[1].get(row["job_id"]))
    assert {c.chunk_id: (c.text, c.audio_sha256) for c in after.chunks if c.chunk_id != selected.chunk_id} == other


def test_teaching_v31_worker_recovers_malformed_repair_and_completes_all_levels(integration, lesson, monkeypatch):
    row, polly = generate(integration, lesson, monkeypatch, repair_failures=2)
    assert row["status"] == "DONE" and row["attempts"] == 1, row["error_code"]
    result = lesson[3].result(row)
    assert set(result.variants) == {"beginner", "intermediate", "advanced"} and polly.calls
    assert all("invented" not in c.text for c in result.chunks)
    assert all(v.versions["paragraph_repair"] == "host-owned-single-target-v2" for v in result.variants.values())


def test_teaching_v31_worker_exhausted_factual_edits_use_flagged_sources_for_all_levels(integration, lesson, monkeypatch):
    row, polly = generate(integration, lesson, monkeypatch, reject_repairs=True)
    assert row["status"] == "DONE" and row["attempts"] == 1, row["error_code"]
    result = lesson[3].result(row)
    assert set(result.variants) == {"beginner", "intermediate", "advanced"} and polly.calls
    assert result.status == "DRAFT" and not result.reviewer_report.approved
    assert all("invented" not in c.text and "[Word block" not in c.text for c in result.chunks)
    assert any("Teacher editing required" in issue.message for issue in result.reviewer_report.issues)

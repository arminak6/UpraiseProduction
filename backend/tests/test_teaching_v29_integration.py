"""Real API/worker/storage/TTS boundaries with disposable data and fake models."""

import json

import pytest
from test_a2_integration import submit
from test_a3 import Polly, request_data
from test_a3_integration import api
from test_teaching_v2_integration import V2Client

from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.teaching_v29 import TeachingModelsV29, TeachingSpeechV29

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


class Client(V2Client):
    def __init__(self, final_pass=True, unavailable_final=False):
        super().__init__()
        self.final_pass, self.unavailable_final = final_pass, unavailable_final

    def converse(self, **request):
        schema = request.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {})
        name = schema.get("name", "")
        message = request["messages"][0]["content"][0]["text"]
        payload, _ = json.JSONDecoder().raw_decode(message)
        if name in {"teaching_v29_initial_review", "teaching_v29_final_review"} or "candidates" in payload:
            self.calls.append(request)
            assert "original_candidate" not in payload and "reviewer_comments" not in payload
            if self.unavailable_final and (name == "teaching_v29_final_review" or not name):
                raise ValueError("PRIVATE reviewer response")
            passed = self.final_pass and name == "teaching_v29_final_review"
            value = {"assessments": [{"candidate_index": index, "correct": passed, "complete": True,
                "in_language": True, "natural": True, "level_suitable": True,
                "issues": [] if passed else ["A source-based content concern remains."]}
                for index in range(len(payload["candidates"]))]}
        elif name == "teaching_v29_judge" or "reviewer_comments" in payload:
            self.calls.append(request)
            value = payload["original_candidate"]
            text = "The judge's final teaching explains the supported value three. [p. 1]"
            for item in value.get("paragraphs", value.get("units", [])):
                item["text"] = text
            if "text" in value:
                value["text"] = "The judge's final spoken explanation gives the supported value three."
        else:
            return super().converse(**request)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps(value)}]}}}


def generate(integration, lesson, monkeypatch, *, aligned=True, **kwargs):
    doc = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    settings, jobs, worker, service, principal, _ = lesson
    client, polly = Client(**kwargs), Polly()
    models = TeachingModelsV29(settings, client, diagnostic_dir=settings.data_dir / "v29-test/models")
    voice = TeachingSpeechV29(settings, models, client=polly)
    def factory(*args, **options):
        models.diagnostic_dir = options["diagnostic_dir"]
        return models
    monkeypatch.setattr("upraise_ai.services.teaching_v29.TeachingModelsV29", factory)
    monkeypatch.setattr("upraise_ai.services.teaching_v29.TeachingSpeechV29", lambda *a, **k: voice)
    worker.models = worker.speech = None
    payload = request_data(document_ids=[doc["document_id"]], teaching_version="v2.9",
        options={"target_chunk_words": 30, **({"max_chunk_words": 100} if aligned else {})})
    if aligned:
        payload["levels"] = ["beginner", "intermediate", "advanced"]
    data = (AlignedLessonInput if aligned else LessonInput).model_validate(payload)
    accepted = service.submit(data, principal, "v29-only")
    worker.process(jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature]))
    return jobs.get(accepted["job_id"]), client, polly


@pytest.mark.parametrize("final_pass,unavailable", [(True, False), (False, False), (False, True)])
def test_v29_keeps_judge_and_finishes_with_durable_final_status(integration, lesson, monkeypatch, final_pass, unavailable):
    row, client, polly = generate(integration, lesson, monkeypatch, final_pass=final_pass, unavailable_final=unavailable)
    assert row["status"] == "DONE", row["error_code"]
    assert polly.calls and row["metadata"]["teaching_version"] == "v2.9"
    bundle = lesson[3].result(row)
    assert bundle.versions["teaching_policy"] == "teaching-narration-v2.9"
    with api(integration, lesson) as http:
        catalog = http.get("/v1/lesson-jobs").json()
        assert any(item["teaching_version"] == "v2.9" and item["teaching_policy"] == "teaching-narration-v2.9" for item in catalog)
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        assert review["teaching_version"] == "v2.9"
        for variant in review["variants"].values():
            for chunk in variant["chunks"]:
                assert "judge's final teaching" in chunk["text"]
                assert "judge's final spoken" in chunk["narration_text"]
                flags = [i for i in chunk["reviewer_report"]["issues"] if i["message"].startswith("Human review required:")]
                assert bool(flags) is not final_pass
                assert "PRIVATE" not in json.dumps(flags)
    names = [call.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {}).get("name")
             for call in client.calls]
    assert "teaching_v29_judge" in names and "aligned_unit_final" not in names
    assert "teaching_v2_material_correction" not in names
    for call in client.calls:
        name = call.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {}).get("name", "")
        if name == "teaching_v29_judge":
            assert call["modelId"] == lesson[0].lesson_generation_model_id
        elif name in {"teaching_v29_initial_review", "teaching_v29_final_review"}:
            assert call["modelId"] == lesson[0].lesson_review_model_id


def test_v29_legacy_generation_api_also_uses_judge_and_nonblocking_final_review(integration, lesson, monkeypatch):
    row, _, polly = generate(integration, lesson, monkeypatch, aligned=False, final_pass=False)
    assert row["status"] == "DONE", row["error_code"]
    result = lesson[3].result(row)
    assert result.versions["teaching_policy"] == "teaching-narration-v2.9" and polly.calls
    assert all("judge's final teaching" in chunk.text for chunk in result.chunks)
    assert any(i.message.startswith("Human review required:") for i in result.reviewer_report.issues)


def test_v29_teacher_update_inherits_version_and_keeps_judge_after_failed_final_review(integration, lesson, monkeypatch):
    row, client, _ = generate(integration, lesson, monkeypatch)
    parent = lesson[3].result(row)
    selected = parent.variants["intermediate"].chunks[0]
    client.final_pass = False
    with api(integration, lesson) as http:
        accepted = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": selected.chunk_id, "teacher_review": "Explain the supported feature clearly."})
        assert accepted.status_code == 202, accepted.text
    revision = lesson[1].get(accepted.json()["job_id"])
    assert revision["metadata"]["teaching_version"] == "v2.9"
    lesson[2].process(lesson[1].claim(lesson[0].lease_seconds, [lesson[0].lesson_signature, lesson[0].lesson_v2_signature]))
    done = lesson[1].get(accepted.json()["job_id"])
    assert done["status"] == "DONE", done["error_code"]
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
    updated = review["variants"]["intermediate"]["chunks"][0]
    assert "judge's final" in updated["text"]
    assert any(i["message"].startswith("Human review required:") for i in updated["reviewer_report"]["issues"])
    assert all(chunk.text == parent.variants[level].chunks[0].text for level, variant in
               lesson[3].result(lesson[1].get(row["job_id"])).variants.items() if level != "intermediate" for chunk in variant.chunks)

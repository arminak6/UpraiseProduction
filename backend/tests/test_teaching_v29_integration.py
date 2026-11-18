"""Real API/worker/storage/TTS boundaries with disposable data and fake models."""

import json

import pytest
from test_a2_integration import submit
from test_a3 import Polly, request_data
from test_a3_integration import api
from test_teaching_v2_integration import V2Client

from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.teaching_v29 import (
    TeachingModelsV29,
    TeachingModelsV29Light,
    TeachingSpeechV29,
    TeachingSpeechV29Light,
)

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


class BothEnginePolly(Polly):
    def __init__(self):
        super().__init__()
        self.voice_calls = []

    def describe_voices(self, **kwargs):
        self.voice_calls.append(kwargs)
        return {"Voices": [{"Id": "Joanna", "LanguageCode": "en-US",
                            "SupportedEngines": ["neural", "standard"]}]}


@pytest.mark.parametrize("endpoint", ["/v1/lesson-jobs", "/v2/lesson-jobs"])
def test_light_api_identity_is_distinct_from_regular_v29(integration, lesson, endpoint):
    doc = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    payload = request_data(document_ids=[doc["document_id"]], teaching_version="v2.9-light")
    headers = {"Idempotency-Key": "light-version-identity"}
    with api(integration, lesson) as http:
        accepted = http.post(endpoint, json=payload, headers=headers)
        assert accepted.status_code == 202, accepted.text
        assert http.post(endpoint, json=payload, headers=headers).json() == accepted.json()
        payload["teaching_version"] = "v2.9"
        assert http.post(endpoint, json=payload, headers=headers).status_code == 409
        regular = http.post(endpoint, json=payload, headers={"Idempotency-Key": "regular-version-identity"})
        assert regular.status_code == 202 and regular.json()["job_id"] != accepted.json()["job_id"]


def generate(integration, lesson, monkeypatch, *, aligned=True, teaching_version="v2.9", **kwargs):
    doc = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    settings, jobs, worker, service, principal, _ = lesson
    client, polly = Client(**kwargs), BothEnginePolly()
    models_class, speech_class = ((TeachingModelsV29Light, TeachingSpeechV29Light)
        if teaching_version == "v2.9-light" else (TeachingModelsV29, TeachingSpeechV29))
    models = models_class(settings, client, diagnostic_dir=settings.data_dir / "v29-test/models")
    voice = speech_class(settings, models, client=polly)
    def factory(*args, **options):
        models.diagnostic_dir = options["diagnostic_dir"]
        return models
    monkeypatch.setattr("upraise_ai.services.teaching_v29." + models_class.__name__, factory)
    monkeypatch.setattr("upraise_ai.services.teaching_v29." + speech_class.__name__, lambda *a, **k: voice)
    worker.models = worker.speech = None
    payload = request_data(document_ids=[doc["document_id"]], teaching_version=teaching_version,
        options={"target_chunk_words": 30, **({"max_chunk_words": 100} if aligned else {})})
    if aligned:
        payload["levels"] = ["beginner", "intermediate", "advanced"]
    data = (AlignedLessonInput if aligned else LessonInput).model_validate(payload)
    accepted = service.submit(data, principal, "v29-only")
    worker.process(jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature]))
    return jobs.get(accepted["job_id"]), client, polly


@pytest.mark.parametrize("final_pass,unavailable", [(True, False), (False, False), (False, True)])
@pytest.mark.parametrize("teaching_version", ["v2.9", "v2.9-light"])
def test_v29_keeps_judge_and_finishes_with_durable_final_status(
        integration, lesson, monkeypatch, final_pass, unavailable, teaching_version):
    row, client, polly = generate(integration, lesson, monkeypatch, final_pass=final_pass,
        unavailable_final=unavailable, teaching_version=teaching_version)
    assert row["status"] == "DONE", row["error_code"]
    assert polly.calls and row["metadata"]["teaching_version"] == teaching_version
    expected_engine = "standard" if teaching_version == "v2.9-light" else "neural"
    assert {call["Engine"] for call in [*polly.calls, *polly.voice_calls]} == {expected_engine}
    assert lesson[0].polly_engine == "neural"
    for audio, marks in zip(polly.calls[::2], polly.calls[1::2], strict=True):
        assert audio["OutputFormat"] == "pcm" and marks["SpeechMarkTypes"] == ["viseme"]
        assert all(audio[key] == marks[key] for key in ("Text", "TextType", "VoiceId", "LanguageCode", "Engine"))
    bundle = lesson[3].result(row)
    assert bundle.versions["teaching_policy"] == "teaching-narration-v2.9"
    assert bundle.versions["teaching_version"] == teaching_version
    assert all(variant.versions["polly_engine"] == expected_engine for variant in bundle.variants.values())
    with api(integration, lesson) as http:
        catalog = http.get("/v1/lesson-jobs").json()
        assert any(item["teaching_version"] == teaching_version and item["teaching_policy"] == "teaching-narration-v2.9" for item in catalog)
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        assert review["teaching_version"] == teaching_version
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


@pytest.mark.parametrize("teaching_version", ["v2.9", "v2.9-light"])
def test_v29_legacy_generation_api_also_uses_judge_and_nonblocking_final_review(
        integration, lesson, monkeypatch, teaching_version):
    row, _, polly = generate(integration, lesson, monkeypatch, aligned=False, final_pass=False,
                            teaching_version=teaching_version)
    assert row["status"] == "DONE", row["error_code"]
    result = lesson[3].result(row)
    assert result.versions["teaching_policy"] == "teaching-narration-v2.9" and polly.calls
    assert result.versions["teaching_version"] == teaching_version
    assert {call["Engine"] for call in polly.calls} == {"standard" if teaching_version == "v2.9-light" else "neural"}
    assert all("judge's final teaching" in chunk.text for chunk in result.chunks)
    assert any(i.message.startswith("Human review required:") for i in result.reviewer_report.issues)


@pytest.mark.parametrize("teaching_version", ["v2.9", "v2.9-light"])
def test_v29_teacher_update_inherits_version_and_keeps_judge_after_failed_final_review(
        integration, lesson, monkeypatch, teaching_version):
    row, client, polly = generate(integration, lesson, monkeypatch, teaching_version=teaching_version)
    parent = lesson[3].result(row)
    selected = parent.variants["intermediate"].chunks[0]
    client.final_pass = False
    polly.calls.clear()
    with api(integration, lesson) as http:
        accepted = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": selected.chunk_id, "teacher_review": "Explain the supported feature clearly."})
        assert accepted.status_code == 202, accepted.text
    revision = lesson[1].get(accepted.json()["job_id"])
    assert revision["metadata"]["teaching_version"] == teaching_version
    lesson[2].process(lesson[1].claim(lesson[0].lease_seconds, [lesson[0].lesson_signature, lesson[0].lesson_v2_signature]))
    done = lesson[1].get(accepted.json()["job_id"])
    assert done["status"] == "DONE", done["error_code"]
    assert polly.calls and {call["Engine"] for call in polly.calls} == {
        "standard" if teaching_version == "v2.9-light" else "neural"}
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
    updated = review["variants"]["intermediate"]["chunks"][0]
    assert "judge's final" in updated["text"]
    assert any(i["message"].startswith("Human review required:") for i in updated["reviewer_report"]["issues"])
    assert all(chunk.text == parent.variants[level].chunks[0].text for level, variant in
               lesson[3].result(lesson[1].get(row["job_id"])).variants.items() if level != "intermediate" for chunk in variant.chunks)

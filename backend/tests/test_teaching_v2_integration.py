"""V2 API/worker/PG/audio/revision isolation, using invented data and fake providers."""

import json
from dataclasses import replace

import psycopg
from test_a2_integration import submit
from test_a3 import Polly, request_data
from test_a3_integration import api
from test_a3_model_compat import review_data
from test_teaching_v2 import MATERIAL_PASS, PASS

from upraise_ai.guards.ingress import ingress
from upraise_ai.main import app
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.services.teaching_v2 import TeachingModelsV2, TeachingSpeechV2

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


class V2Client:
    def __init__(self, reject_narration=False, material_failures=0, material_warnings=False, reject_entailment=False, invalid_evidence=False,
                 malformed_written=False, long_adaptation=False):
        self.calls = []
        self.reject_narration = reject_narration
        self.material_failures = material_failures
        self.material_warnings = material_warnings
        self.reject_entailment = reject_entailment
        self.invalid_evidence = invalid_evidence
        self.malformed_written = malformed_written
        self.long_adaptation = long_adaptation

    def converse(self, **request):
        self.calls.append(request)
        schema = request.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {})
        name = schema.get("name", "")
        properties = json.loads(schema.get("schema", "{}")).get("properties", {})
        message = request["messages"][0]["content"][0]["text"]
        value = "The supported feature has value three. [p. 1]"
        if self.material_warnings == "fragmented":
            value += " The source does not define the calibration procedure."
        if "items" in properties:
            payload, _ = json.JSONDecoder().raw_decode(message)
            value = {"items": [{"source_index": index, "disposition": "teach", "teaching_quotes": [chunk["text"]], "note": ""}
                                for index, chunk in enumerate(payload["source_context"])]}
        elif "assessments" in properties:
            payload, _ = json.JSONDecoder().raw_decode(message)
            value = {"assessments": [{"finding_index": item["finding_index"], "disposition": "candidate_error",
                                      "reason": "Retain the fixture's actual candidate error."} for item in payload["findings"]]}
        elif name == "teaching_v2_paragraph_entailment":
            value = {"supported": "invalid boolean" if self.invalid_evidence is True else not self.reject_entailment,
                     "reason": "The extra claim is absent from the source." if self.reject_entailment else "Fixture evidence check."}
        elif "checks" in properties:
            payload, _ = json.JSONDecoder().raw_decode(message)
            value = {"checks": [{"candidate_index": index, "supported": not (self.reject_entailment or self.invalid_evidence),
                                 "unsupported_quote": "Not in the candidate." if self.invalid_evidence else text[:100] if self.reject_entailment else "",
                                 "reason": "The extra claim is absent from the source." if self.reject_entailment else "Matches the fixture."}
                                for index, text in enumerate(payload["candidates"])]}
        elif "correct" in properties:
            value = {**PASS, "correct": False, "issues": ["Wrong formula meaning."]} if (
                self.reject_narration and name == "teaching_v2_narration_review") else PASS
        elif "findings" in properties:
            payload, _ = json.JSONDecoder().raw_decode(message)
            value = {**MATERIAL_PASS}
            if self.material_failures:
                self.material_failures -= 1
                value = {**MATERIAL_PASS, "findings": [{"category": "changed_fact", "candidate_index": 0,
                    "candidate_quote": "The supported feature has value three.", "required_index": 0,
                    "required_quote": payload["required_material"][0][:100],
                    "message": "Retain the supported feature value of three."}]}
            elif self.material_warnings:
                value = {**MATERIAL_PASS, "natural": False, "warnings": [{"category": "source_gap",
                    "message": "The source does not define the calibration procedure."}]}
        elif "paragraphs" in properties:
            if self.malformed_written:
                value = r"The supported feature has value three.\nThis is malformed generated formatting."
            value = {"paragraphs": [{"text": value, "pages": [1]}]}
        elif "text" in properties:
            value = {"text": "Three is the observed value of the supported feature."}
        elif "units" in properties:
            payload, _ = json.JSONDecoder().raw_decode(message)
            if "review" in name:
                value = {"units": [{"content_unit_id": unit["content_unit_id"], "material_preserved": True,
                    "level_suitable": True, "in_language": True, "issues": [], "revision_instructions": []}
                    for unit in payload["units"]]}
            else:
                value = {"units": [{"content_unit_id": unit["content_unit_id"], "text": unit["material_text"]}
                                   for unit in payload["units"]]}
                if self.long_adaptation:
                    for item in value["units"]:
                        item["text"] += " The supported value is three." * 30
        elif "approved" in properties:
            value = review_data()
        elif "lecture_text" in properties:
            if "current_chunk" in message:
                payload, _ = json.JSONDecoder().raw_decode(message[message.index("{"):])
                value = payload["current_chunk"]
            value = {"lecture_text": value}
        if not isinstance(value, str):
            value = json.dumps(value)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": value}]}}}


def generate(integration, lesson, monkeypatch, *, reject_narration=False, material_failures=0, material_warnings=False, reject_entailment=False, invalid_evidence=False,
             malformed_written=False, long_adaptation=False, levels=None):
    doc = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    settings, jobs, worker, service, principal, _ = lesson
    client, polly = V2Client(reject_narration, material_failures, material_warnings, reject_entailment, invalid_evidence,
                             malformed_written, long_adaptation), Polly()
    models = TeachingModelsV2(settings, client, diagnostic_dir=settings.data_dir / "v2-test/models")
    voice = TeachingSpeechV2(settings, models, client=polly)
    factories = []

    def model_factory(*args, **kwargs):
        factories.append("models-v2")
        models.diagnostic_dir = kwargs["diagnostic_dir"]
        return models

    def speech_factory(*args, **kwargs):
        factories.append("speech-v2")
        return voice

    monkeypatch.setattr("upraise_ai.services.teaching_v2.TeachingModelsV2", model_factory)
    monkeypatch.setattr("upraise_ai.services.teaching_v2.TeachingSpeechV2", speech_factory)
    worker.models = worker.speech = None
    data = AlignedLessonInput.model_validate(request_data(document_ids=[doc["document_id"]], teaching_version="v2",
        levels=levels or ["intermediate"], options={"target_chunk_words": 30, "max_chunk_words": 100}))
    result = service.submit(data, principal, "v2-only")
    job = jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature])
    worker.process(job)
    client.context_documents = models.document_context.documents
    return jobs.get(result["job_id"]), data, client, polly, factories


def test_teaching_v2_api_worker_saves_written_and_spoken_versions_separately_and_revision_stays_v2(integration, lesson, monkeypatch):
    row, data, client, polly, factories = generate(integration, lesson, monkeypatch)
    assert row["status"] == "DONE", row["error_code"]
    assert factories == ["models-v2", "speech-v2"]
    assert row["metadata"]["teaching_version"] == "v2"
    assert client.context_documents
    assert {key[0] for key in client.context_documents} == {str(doc) for doc in data.document_ids}
    bundle = lesson[3].result(row)
    variant = bundle.variants["intermediate"]
    assert not any("aligned_unit_draft" in json.dumps(call.get("outputConfig")) for call in client.calls)
    assert bundle.versions["teaching_version"] == variant.versions["teaching_version"] == "v2"
    saved = lesson[1].prepared_chunks(row["job_id"])[0]
    canonical = json.loads((lesson[2].staging.job_dir(str(row["job_id"])) / "canonical-section-0.json").read_text())
    assert saved["text"] == canonical["draft"]["paragraphs"][0]["text"]
    assert saved["text"] != "Three is the observed value of the supported feature."
    assert "Three is the observed value" in saved["ssml"]
    assert polly.calls and any("independently review" in block["text"].lower()
                               for call in client.calls for block in call["system"])
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        assert review["teaching_version"] == "v2"
        chunk = review["variants"]["intermediate"]["chunks"][0]
        assert "Three is the observed value" in chunk["narration_text"]
        assert chunk["text"] == saved["text"]
        same = http.post("/v2/lesson-jobs", json=data.model_dump(mode="json"), headers={"Idempotency-Key": "v2-only"})
        assert same.json()["job_id"] == str(row["job_id"])
        v1 = data.model_dump(mode="json")
        v1["teaching_version"] = "v1"
        assert http.post("/v2/lesson-jobs", json=v1, headers={"Idempotency-Key": "v2-only"}).status_code == 409
        reverted = http.post("/v2/lesson-jobs", json=v1, headers={"Idempotency-Key": "back-to-v1"})
        assert reverted.status_code == 202
        assert "teaching_version" not in lesson[1].get(reverted.json()["job_id"])["metadata"]
        revised = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": chunk["chunk_id"], "teacher_review": "Keep the supported feature and explain it clearly."})
        assert revised.status_code == 202, revised.text
    revision = lesson[1].get(revised.json()["job_id"])
    assert revision["metadata"]["teaching_version"] == "v2"
    # Claim the revision, leaving the new V1 course queued.
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp()+interval '1 hour' WHERE job_id=%s",
                     (reverted.json()["job_id"],))
    claimed = lesson[1].claim(lesson[0].lease_seconds, [lesson[0].lesson_signature, lesson[0].lesson_v2_signature])
    assert str(claimed["job_id"]) == revised.json()["job_id"]
    lesson[2].process(claimed)
    done = lesson[1].get(claimed["job_id"])
    assert done["status"] == "DONE", done["error_code"]
    assert factories == ["models-v2", "speech-v2"] * 2
    assert lesson[3].result(lesson[1].get(row["job_id"])).variants["intermediate"].versions["teaching_version"] == "v2"


def test_teaching_v2_malformed_written_format_keeps_first_text_with_visible_findings(integration, lesson, monkeypatch):
    row, _, _, polly, _ = generate(integration, lesson, monkeypatch, malformed_written=True)
    assert row["status"] == "DONE" and row["attempts"] == 1 and polly.calls, row["error_code"]
    saved = lesson[1].prepared_chunks(row["job_id"])
    assert saved and all("malformed generated formatting" in c["text"] and r"\n" in c["text"] for c in saved)
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        report = review["variants"]["intermediate"]["chunks"][0]["reviewer_report"]
        assert not report["approved"]
        assert any("TEACHING_V2_TEXT_INVALID" in w["message"] and "LITERAL_NEWLINE_ESCAPE" in w["message"]
                   for w in report["issues"])


def test_teaching_v2_failed_meaning_review_finishes_with_durable_human_review_flag(integration, lesson, monkeypatch):
    row, _, _, polly, _ = generate(integration, lesson, monkeypatch, reject_narration=True)
    assert row["status"] == "DONE" and row["error_code"] is None and polly.calls
    assert row["result"]["lesson_status"] == "DRAFT" and not row["result"]["review_approved"]
    saved = lesson[1].prepared_chunks(row["job_id"])
    assert all(any("TEACHING_V2_NARRATION_REJECTED" in w["message"] for w in c["reviewer_report"]["issues"]) for c in saved)
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        report = review["variants"]["intermediate"]["chunks"][0]["reviewer_report"]
        assert not report["approved"] and any("Wrong formula meaning" in w["message"] for w in report["issues"])


def test_teaching_v2_oversized_first_adaptation_is_flagged_without_rewriting_or_changing_shared_units(integration, lesson, monkeypatch):
    row, _, client, polly, _ = generate(integration, lesson, monkeypatch, long_adaptation=True,
                                       levels=["beginner", "intermediate"])
    assert row["status"] == "DONE" and row["attempts"] == 1 and polly.calls, row["error_code"]
    bundle = lesson[3].result(row)
    beginner, intermediate = bundle.variants["beginner"], bundle.variants["intermediate"]
    assert beginner.chunks[0].content_unit_id == intermediate.chunks[0].content_unit_id
    assert len(beginner.chunks[0].text.split()) > 100
    assert any("LESSON_UNIT_TOO_LARGE" in w.message for w in beginner.chunks[0].reviewer_report.issues)
    assert not any("aligned_unit_shorten" in json.dumps(c.get("outputConfig")) for c in client.calls)


def test_teaching_v2_human_revision_keeps_candidate_when_final_reviewer_is_unreadable(integration, lesson, monkeypatch):
    row, _, client, _, _ = generate(integration, lesson, monkeypatch)
    chunk = lesson[1].prepared_chunks(row["job_id"])[0]
    client.invalid_evidence = True
    with api(integration, lesson) as http:
        accepted = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": str(chunk["chunk_id"]), "teacher_review": "Clarify the supported value."})
        assert accepted.status_code == 202, accepted.text
    settings, jobs, worker, service, _, _ = lesson
    revision = jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature])
    worker.process(revision)
    completed = jobs.get(revision["job_id"])
    assert completed["status"] == "DONE", completed["error_code"]
    report = service.result(jobs.get(row["job_id"])).variants["intermediate"].reviewer_report
    assert not report.approved and any("PARAGRAPH_EVIDENCE_INVALID" in w.message for w in report.issues)


def test_teaching_v2_human_revision_retains_first_update_on_alignment_rejection_without_editor_rewrites(integration, lesson, monkeypatch):
    from upraise_ai.schemas.aligned_lessons import AlignedReview
    row, _, client, _, _ = generate(integration, lesson, monkeypatch)
    chunk = lesson[1].prepared_chunks(row["job_id"])[0]
    def reject(models, section, units, *args):
        return AlignedReview(units=[{"content_unit_id": units[0].content_unit_id,
            "material_preserved": False, "level_suitable": True, "in_language": True,
            "issues": ["A human must check the retained value."], "revision_instructions": []}])
    monkeypatch.setattr("upraise_ai.services.revision_pipeline.reviewed", reject)
    with api(integration, lesson) as http:
        accepted = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": str(chunk["chunk_id"]), "teacher_review": "Clarify the supported value."})
        assert accepted.status_code == 202, accepted.text
    settings, jobs, worker, service, _, _ = lesson
    revision = jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature])
    worker.process(revision)
    completed = jobs.get(revision["job_id"])
    assert completed["status"] == "DONE", completed["error_code"]
    report = service.result(jobs.get(row["job_id"])).variants["intermediate"].reviewer_report
    assert not report.approved and any("A human must check the retained value." in w.message for w in report.issues)
    assert not any("chunk_revision_alignment_correction" in json.dumps(c.get("outputConfig")) or
                   "chunk_revision_finalize" in json.dumps(c.get("outputConfig")) for c in client.calls)


def test_teaching_v2_entailment_failure_retains_first_prose_and_flags_for_human_editing(integration, lesson, monkeypatch):
    row, _, client, polly, _ = generate(integration, lesson, monkeypatch, reject_entailment=True)
    assert row["status"] == "DONE", row["error_code"]
    assert polly.calls and row["result"]["lesson_status"] == "DRAFT"
    repairs = [call for call in client.calls if "teaching_v2_material_correction" in json.dumps(call.get("outputConfig"))]
    assert len(repairs) == 0
    saved = lesson[1].prepared_chunks(row["job_id"])
    assert saved and all("The supported feature has value three." in part["text"] for part in saved)
    assert all(not part["reviewer_report"]["approved"] and "human review required" in part["reviewer_report"]["summary"]
               for part in saved)


def test_teaching_v2_source_excerpt_keeps_unresolved_spoken_meaning_flags(integration, lesson, monkeypatch):
    row, _, _, polly, _ = generate(integration, lesson, monkeypatch, reject_entailment=True, reject_narration=True)
    assert row["status"] == "DONE" and polly.calls
    report = lesson[3].result(row).variants["intermediate"].reviewer_report
    assert not report.approved and any("TEACHING_V2_NARRATION_REJECTED" in w.message for w in report.issues)


def test_teaching_v2_invalid_quote_recovery_completes_actual_worker_flow(integration, lesson, monkeypatch):
    row, _, client, polly, _ = generate(integration, lesson, monkeypatch, invalid_evidence="recover")
    assert row["status"] == "DONE", row["error_code"]
    assert polly.calls and any("teaching_v2_paragraph_entailment" in json.dumps(call.get("outputConfig")) for call in client.calls)


def test_teaching_v2_invalid_references_are_teacher_visible_without_failing_job(integration, lesson, monkeypatch):
    row, _, _, polly, _ = generate(integration, lesson, monkeypatch, invalid_evidence=True)
    assert row["status"] == "DONE" and row["attempts"] == 1 and polly.calls
    assert row["error_code"] is None and not row["result"]["review_approved"]
    with api(integration, lesson) as http:
        status = http.get(f"/v1/jobs/{row['job_id']}").json()
        assert status["error"] is None
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        report = review["variants"]["intermediate"]["chunks"][0]["reviewer_report"]
        assert not report["approved"] and any("PARAGRAPH_EVIDENCE_INVALID" in w["message"] and w["pages"] == [1]
                                             for w in report["issues"])
        app.dependency_overrides[ingress] = lambda: replace(lesson[4], client_id="another-client")
        assert http.get(f"/v1/jobs/{row['job_id']}").status_code == 404
        app.dependency_overrides[ingress] = lambda: lesson[4]
        assert http.delete(f"/v1/jobs/{row['job_id']}?reason=RECEIVED").status_code == 204
        assert http.get(f"/v1/jobs/{row['job_id']}").json()["error"] is None
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        records = conn.execute("SELECT details FROM lesson_audit WHERE job_id=%s", (row['job_id'],)).fetchall()
    assert "PARAGRAPH_EVIDENCE_INVALID" not in json.dumps(records)


def test_teaching_v2_factual_finding_is_saved_without_rewriting_the_first_candidate(integration, lesson, monkeypatch):
    row, _, client, polly, _ = generate(integration, lesson, monkeypatch, material_failures=1)
    assert row["status"] == "DONE", row["error_code"]
    repairs = [call for call in client.calls if "teaching_v2_material_correction" in json.dumps(call.get("outputConfig"))]
    assert len(repairs) == 0 and polly.calls
    saved = lesson[1].prepared_chunks(row["job_id"])
    assert all("Retain the supported feature value of three." in json.dumps(c["reviewer_report"]) for c in saved)


def test_teaching_v2_source_gap_warnings_finish_as_draft_and_are_visible_to_teacher(integration, lesson, monkeypatch):
    row, _, client, polly, _ = generate(integration, lesson, monkeypatch, material_warnings=True)
    assert row["status"] == "DONE", row["error_code"]
    assert row["attempts"] == 1 and polly.calls
    assert not any("teaching_v2_material_correction" in json.dumps(call.get("outputConfig")) for call in client.calls)
    saved = lesson[1].prepared_chunks(row["job_id"])
    assert saved and any(issue["category"] == "source" and "calibration procedure" in issue["message"]
                         for chunk in saved for issue in chunk["reviewer_report"]["issues"])
    with api(integration, lesson) as http:
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        assert review["variants"]["intermediate"]["chunks"][0]["reviewer_report"]["approved"] is False
        accepted = http.post("/v1/audio:regenerate", json={"lesson_job_id": str(row["job_id"]),
            "chunk_id": str(saved[0]["chunk_id"]), "teacher_review": "Keep the measured value and acknowledge missing calibration."})
        assert accepted.status_code == 202, accepted.text
    assert row["result"]["lesson_status"] == "DRAFT"
    settings, jobs, worker, service, _, _ = lesson
    revision = jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature])
    assert str(revision["job_id"]) == accepted.json()["job_id"]
    worker.process(revision)
    done = jobs.get(revision["job_id"])
    assert done["status"] == "DONE", done["error_code"]
    revised = service.result(jobs.get(row["job_id"])).variants["intermediate"]
    assert revised.versions["revision_material_review"] == "grounded-candidate-findings-v4"
    assert revised.versions["revision_material_finding_verification"] == "source-presentation-only-v2"
    assert any("calibration procedure" in issue.message for issue in revised.chunks[0].reviewer_report.issues)


def test_teaching_v2_fragmented_source_gap_review_finishes_without_invented_requirements(integration, lesson, monkeypatch):
    row, _, client, polly, _ = generate(integration, lesson, monkeypatch, material_warnings="fragmented")
    assert row["status"] == "DONE", row["error_code"]
    assert row["attempts"] == 1 and polly.calls
    assert not any("teaching_v2_material_correction" in json.dumps(call.get("outputConfig")) for call in client.calls)
    assert row["result"]["lesson_status"] == "DRAFT"
    saved = lesson[1].prepared_chunks(row["job_id"])
    assert any("calibration procedure" in issue["message"] for item in saved for issue in item["reviewer_report"]["issues"])


def test_teaching_v2_exhausted_material_review_finishes_flagged_draft_and_disposes_diagnostics(integration, lesson, monkeypatch):
    row, _, client, polly, _ = generate(integration, lesson, monkeypatch, material_failures=100)
    assert row["status"] == "DONE" and row["attempts"] == 1 and polly.calls, row["error_code"]
    assert row["result"]["lesson_status"] == "DRAFT"
    assert len([call for call in client.calls if "teaching_v2_material_correction" in json.dumps(call.get("outputConfig"))]) == 0
    assert (lesson[2].staging.job_dir(str(row["job_id"])) / "teaching-v2-material").is_dir()
    with api(integration, lesson) as http:
        status = http.get(f"/v1/jobs/{row['job_id']}")
        assert status.status_code == 200
        review = http.get(f"/v1/lesson-reviews/{row['job_id']}").json()
        report = review["variants"]["intermediate"]["chunks"][0]["reviewer_report"]
        assert not report["approved"] and any("Human review required" in issue["message"] for issue in report["issues"])
        app.dependency_overrides[ingress] = lambda: replace(lesson[4], client_id="another-client")
        assert http.get(f"/v1/jobs/{row['job_id']}").status_code == 404
        app.dependency_overrides[ingress] = lambda: lesson[4]
        assert http.delete(f"/v1/jobs/{row['job_id']}?reason=RECEIVED").status_code == 204
        after = http.get(f"/v1/jobs/{row['job_id']}").json()
        assert "review" not in (after.get("error") or {})
    assert not (lesson[2].staging.job_dir(str(row["job_id"])) / "teaching-v2-material").exists()
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        records = conn.execute("SELECT details FROM lesson_audit WHERE job_id=%s", (row["job_id"],)).fetchall()
    assert records and "Retain the supported feature" not in json.dumps(records)

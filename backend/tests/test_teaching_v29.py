"""Bounded A/B/C/B workflow: keep C, recheck independently, and isolate V2.8."""

import json
from types import SimpleNamespace

import pytest
from test_a3 import Polly, request_data
from test_a3_v2 import unit
from test_teaching_review_recovery import draft
from test_teaching_v3 import source

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import LeaseLost
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput, AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import ReviewIssue, ReviewReport
from upraise_ai.services.aligned_pipeline import AlignedLessonPipeline
from upraise_ai.services.teaching_v2 import TeachingModelsV2
from upraise_ai.services.teaching_v29 import TeachingModelsV29, TeachingSpeechV29, workflow
from upraise_ai.services.teaching_v29.workflow import Review


def model(tmp_path=None):
    result = TeachingModelsV29(Settings(), client=object(), diagnostic_dir=tmp_path / "models" if tmp_path else None)
    result.draft_review_recovery = True
    return result


def verdict(payload, *, passed=True, issue="The final claim is unsupported."):
    return Review(assessments=[{"candidate_index": index, "correct": passed, "complete": True,
        "in_language": True, "natural": True, "level_suitable": True, "issues": [] if passed else [issue]}
        for index in range(len(payload["candidates"]))])


def context():
    return {"kind": "written", "source_context": [{"text": "There are twelve nodes."}],
            "clarifying_context": [], "citation_numbers": [1], "learner_level": "intermediate"}


@pytest.mark.parametrize("final_pass", [True, False])
def test_judge_sees_source_writer_and_findings_then_fresh_review_keeps_judge(monkeypatch, tmp_path, final_pass):
    calls = []
    def request(models, policy, payload, schema, name, **kwargs):
        calls.append((name, payload, kwargs))
        if name == "teaching_v29_judge":
            assert payload["source_context"] == context()["source_context"]
            assert "eleven" in payload["original_candidate"]["paragraphs"][0]["text"]
            assert "Initial problem" in json.dumps(payload["reviewer_comments"])
            return draft("There are twelve nodes.")
        assert kwargs["reviewer"]
        assert "reviewer_comments" not in payload and "original_candidate" not in payload
        if "initial" in name:
            return verdict(payload, passed=False, issue="Initial problem: eleven should be twelve.")
        assert payload["candidates"] == ["There are twelve nodes."]
        return verdict(payload, passed=final_pass)
    monkeypatch.setattr(workflow, "structured", request)
    result, reports, generated = workflow.adjudicate(model(tmp_path), draft("There are eleven nodes."),
        context(), "en", stage="written")
    assert result.paragraphs[0].text == "There are twelve nodes." and generated
    assert reports[0].approved is final_pass
    assert "Initial problem" not in json.dumps(reports[0].model_dump())
    assert [name for name, _, _ in calls] == ["teaching_v29_initial_review", "teaching_v29_judge", "teaching_v29_final_review"]
    if not final_pass:
        assert "Human review required:" in reports[0].issues[0].message
    # A completed retry reuses the final candidate and findings without model calls.
    assert workflow.adjudicate(model(tmp_path), draft("There are eleven nodes."), context(), "en", stage="written") == (
        result, reports, generated)
    assert len(calls) == 3


def test_passing_initial_review_still_runs_one_judge(monkeypatch):
    calls = []
    def request(models, policy, payload, schema, name, **kwargs):
        calls.append(name)
        return draft("Judge's final explanation.") if name.endswith("judge") else verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    final, reports, _ = workflow.adjudicate(model(), draft(), context(), "en", stage="written")
    assert final.paragraphs[0].text == "Judge's final explanation." and reports[0].approved
    assert calls.count("teaching_v29_judge") == 1


@pytest.mark.parametrize("unavailable_stage", ["initial", "final", "judge"])
def test_unavailable_quality_stage_continues_without_disclosing_provider_error(monkeypatch, unavailable_stage):
    from botocore.exceptions import ClientError
    def request(models, policy, payload, schema, name, **kwargs):
        if unavailable_stage in name:
            raise ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "PRIVATE provider error"}}, "Converse")
        return draft("Judge's retained text.") if name.endswith("judge") else verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    final, reports, generated = workflow.adjudicate(model(), draft("Writer's text."), context(), "en", stage="written")
    assert final.paragraphs[0].text == ("Writer's text." if unavailable_stage == "judge" else "Judge's retained text.")
    assert generated is (unavailable_stage != "judge")
    assert reports[0].approved is (unavailable_stage == "initial")
    assert "PRIVATE" not in json.dumps([r.model_dump() for r in reports])


def test_interrupted_final_review_reuses_judge_without_repeating_initial_review(monkeypatch, tmp_path):
    calls = []
    interrupt = True
    def request(models, policy, payload, schema, name, **kwargs):
        calls.append(name)
        if name.endswith("judge"):
            return draft("The checkpointed judge output.")
        if "final" in name and interrupt:
            raise LeaseLost()
        return verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    with pytest.raises(LeaseLost):
        workflow.adjudicate(model(tmp_path), draft(), context(), "en", stage="written")
    interrupt = False
    final, reports, _ = workflow.adjudicate(model(tmp_path), draft(), context(), "en", stage="written")
    assert final.paragraphs[0].text == "The checkpointed judge output." and reports[0].approved
    assert calls.count("teaching_v29_judge") == calls.count("teaching_v29_initial_review") == 1
    assert calls.count("teaching_v29_final_review") == 2


@pytest.mark.parametrize("stage", ["initial", "judge", "final"])
def test_storage_and_ownership_failures_are_not_quality_findings(monkeypatch, stage):
    def request(models, policy, payload, schema, name, **kwargs):
        if stage in name:
            raise OSError("Storage unavailable")
        return draft() if name.endswith("judge") else verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    with pytest.raises(OSError):
        workflow.adjudicate(model(), draft(), context(), "en", stage="written")


def test_judge_fixes_formatting_and_final_report_drops_initial_format_flags(monkeypatch):
    def request(models, policy, payload, schema, name, **kwargs):
        if name.endswith("judge"):
            assert "LITERAL_NEWLINE_ESCAPE" in json.dumps(payload["reviewer_comments"])
            return draft("Readable teaching.\nAnother sentence.")
        return verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    final, reports, _ = workflow.adjudicate(model(), draft(r"Readable teaching.\nAnother sentence."), context(), "en", stage="written")
    assert "\\n" not in final.paragraphs[0].text and reports[0].approved


def test_unfixed_format_and_citations_remain_visible_with_flags(monkeypatch):
    def request(models, policy, payload, schema, name, **kwargs):
        return draft(r"Judge text\n [p. 999]") if name.endswith("judge") else verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    final, reports, _ = workflow.adjudicate(model(), draft(), context(), "en", stage="written")
    assert final.paragraphs[0].text == r"Judge text\n [p. 999]"
    assert not reports[0].approved
    assert {i.category for i in reports[0].issues} == {"clarity", "citations"}


def test_wrong_judge_unit_identity_keeps_writer_and_flags_judge(monkeypatch):
    item = unit()
    candidate = AlignedTexts(units=[AlignedText(content_unit_id=item.content_unit_id, text="Original topic.")])
    def request(models, policy, payload, schema, name, **kwargs):
        if name.endswith("judge"):
            return candidate.model_copy(update={"units": [AlignedText(content_unit_id="00000000-0000-0000-0000-000000000000", text="Wrong unit.")]})
        return verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    final, reports, generated = workflow.adjudicate(model(), candidate, context(), "en", stage="difficulty", units=[item])
    assert final == candidate and not generated and not reports[0].approved


def test_review_index_mismatch_is_flagged_without_losing_judge(monkeypatch):
    def request(models, policy, payload, schema, name, **kwargs):
        if name.endswith("judge"):
            return draft("Final text retained.")
        result = verdict(payload)
        result.assessments[0].candidate_index = 8
        return result
    monkeypatch.setattr(workflow, "structured", request)
    final, reports, _ = workflow.adjudicate(model(), draft(), context(), "en", stage="written")
    assert final.paragraphs[0].text == "Final text retained." and not reports[0].approved


def test_final_findings_belong_only_to_the_affected_unit(monkeypatch):
    from uuid import uuid4
    first = unit()
    second = first.model_copy(update={"content_unit_id": uuid4(), "index": 1})
    candidate = AlignedTexts(units=[AlignedText(content_unit_id=item.content_unit_id, text="A source-based topic.")
                                  for item in [first, second]])
    def request(models, policy, payload, schema, name, **kwargs):
        if name.endswith("judge"):
            return candidate
        result = verdict(payload)
        if "final" in name:
            result.assessments[1].correct = False
            result.assessments[1].issues = ["Only the second topic needs checking."]
        return result
    monkeypatch.setattr(workflow, "structured", request)
    _, reports, _ = workflow.adjudicate(model(), candidate, context(), "en", stage="difficulty", units=[first, second])
    assert reports[0].approved and not reports[1].approved
    assert "second topic" in reports[1].issues[0].message


def test_judge_checkpoint_is_invalidated_when_source_changes(monkeypatch, tmp_path):
    calls = []
    def request(models, policy, payload, schema, name, **kwargs):
        calls.append(name)
        return draft("The judge uses the current source.") if name.endswith("judge") else verdict(payload)
    monkeypatch.setattr(workflow, "structured", request)
    workflow.adjudicate(model(tmp_path), draft(), context(), "en", stage="written")
    changed = {**context(), "source_context": [{"text": "The source has changed."}]}
    workflow.adjudicate(model(tmp_path), draft(), changed, "en", stage="written")
    assert calls.count("teaching_v29_judge") == 2


def test_whole_narration_runs_same_flow_and_keeps_judge_on_failed_review(monkeypatch):
    from upraise_ai.services.teaching_v2.contracts import Narration
    monkeypatch.setattr("upraise_ai.services.teaching_v29.speech.structured", lambda *a, **k: Narration(text="Writer narration."))
    def request(models, policy, payload, schema, name, **kwargs):
        assert payload["written_reference"] == "The source condition applies only to connected networks."
        if name.endswith("judge"):
            return Narration(text="Judge narration for connected networks.")
        return verdict(payload, passed=False, issue="The condition needs human checking.")
    monkeypatch.setattr(workflow, "structured", request)
    models = model()
    voice = TeachingSpeechV29(models.settings, models, client=Polly())
    narration, issues = voice.narration("The source condition applies only to connected networks.", "en")
    assert narration.text == "Judge narration for connected networks." and issues[0].category == "speech"


def test_passing_adaptation_does_not_inherit_rejected_canonical_findings(monkeypatch, tmp_path):
    models = model()
    item = unit()
    item.canonical_review = ReviewReport(approved=False, summary="Old draft.",
        issues=[ReviewIssue(category="accuracy", message="OLD canonical issue", pages=[1])])
    monkeypatch.setattr(models, "adapt_units", lambda *a, **k: (
        AlignedTexts(units=[AlignedText(content_unit_id=item.content_unit_id, text="Corrected final topic.")]),
        {str(item.content_unit_id): ReviewReport(approved=True, summary="Final review passed.")}))
    pipeline = AlignedLessonPipeline(models.settings, None, None, None, models, None)
    pipeline._variant_folder = tmp_path
    data = SimpleNamespace(level_profiles={"beginner": "A profile"}, plan_level="intermediate", language="en",
        options=SimpleNamespace(target_chunk_words=30, max_chunk_words=50))
    result = pipeline.generate_variants(data, SimpleNamespace(units=[item]), [source(1, item.material_text)], "test", lambda *args: None)
    assert "OLD canonical issue" not in json.dumps(result)


def test_v29_has_distinct_request_and_provenance_without_changing_v28():
    assert AlignedLessonInput.model_validate(request_data(teaching_version="v2.9")).teaching_version == "v2.9"
    assert TeachingModelsV2.teaching_versions["teaching_policy"] == "teaching-narration-v2.8"
    assert model().teaching_versions["teaching_policy"] == "teaching-narration-v2.9"
    assert model().teaching_versions["judge_rounds"] == "1"

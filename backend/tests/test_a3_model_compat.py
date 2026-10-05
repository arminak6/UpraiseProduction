"""Native V3 parity and A3 boundary regressions; no live AWS requests."""

import hashlib
import io
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError
from test_a3 import MathModels, Polly

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.lesson_compat import legacy_review_report, turn_to_draft
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.lesson_speech import LessonSpeech, align_visemes
from upraise_ai.services.v3 import bedrock_teacher
from upraise_ai.services.v3.bedrock_teacher import BedrockTeacherV1
from upraise_ai.services.v3.models import LectureReview, TeacherTurn


class Converse:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def converse(self, **request):
        self.calls.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            return response
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": response}]}}}


def section():
    return {"document_id": "source-document", "document_version_id": "source-version",
            "page_count": 55, "window": [1, 5], "pages": [1, 2, 5], "chunks": [
                {"chunk_id": "source-chunk", "chunk_index": 7,
                 "text": "The source defines a tape and transition rule.",
                 "page_numbers": [1, 2, 5], "heading_path": ["Machines"], "document_title": "Course.pdf"}]}


def review_data(**updates):
    return {"approved": True, "summary": "Source checked.", "contradictions": [],
            "unsupported_claims": [], "missing_source_points": [], "citation_issues": [],
            "clarity_issues": [], "teaching_quality_issues": [], "revision_instructions": [], **updates}


def test_copied_v3_bundle_matches_recorded_original_hashes():
    folder = Path(bedrock_teacher.__file__).parent
    origin = json.loads((folder / "origin.json").read_text())
    for name, record in origin["files"].items():
        digest = hashlib.sha256((folder / name).read_text(encoding="utf-8").encode()).hexdigest()
        assert digest == record["local_sha256"], name
        if not record["relocations"]:
            assert digest == record["source_sha256"]


@pytest.mark.parametrize("approved", [True, False])
def test_english_provider_requests_equal_original_teacher_requests(approved):
    replies = ["A source explanation [p. 1].", json.dumps(review_data(**({} if approved else {
        "approved": False, "clarity_issues": ["Clarify."], "revision_instructions": ["Explain notation."]})))]
    if not approved:
        replies.append(json.dumps({"lecture_text": "A clearer source explanation [p. 1]."}))
    wrapped, direct = Converse(replies), Converse(replies)
    models = LessonModels(Settings(), wrapped)
    notifications = []
    final, report, revisions = models.generate_section(section(), "en", notify=notifications.append)
    native = BedrockTeacherV1(session=None, client=direct,
        model_id=models.settings.lesson_generation_model_id,
        reviewer_model_id=models.settings.lesson_review_model_id)
    document, args = models.source(section())
    draft = native.explain(document, **args)
    review = native.review_explanation(document, draft=draft, **args)
    turn = native.finalize_explanation(document, draft=draft, review=review, **args)
    assert wrapped.calls == direct.calls
    assert final == turn_to_draft(turn) and report == legacy_review_report(review, turn.source_pages)
    assert revisions == int(not approved) and notifications == ["REVIEW", "FINALIZE"]


def test_bad_draft_citation_reaches_reviewer_then_original_editor_repairs_it():
    client = Converse(["Wrong page [p. 999].", json.dumps(review_data(approved=False,
        citation_issues=["Wrong page."], revision_instructions=["Cite page 1."])),
        json.dumps({"lecture_text": "Corrected citation [p. 1]."})])
    final, report, revisions = LessonModels(Settings(), client).generate_section(section(), "en")
    assert "[p. 1]" in final.paragraphs[0].text and revisions == 1 and not report.approved
    assert len(client.calls) == 3
    assert "[p. 999]" in client.calls[1]["messages"][0]["content"][0]["text"]


def test_approved_draft_still_passes_original_final_citation_validation():
    client = Converse(["Invalid final page [p. 999].", json.dumps(review_data())])
    with pytest.raises(IngestionError, match="LESSON_MODEL_INVALID_OUTPUT"):
        LessonModels(Settings(), client).generate_section(section(), "en")
    assert len(client.calls) == 2


def test_original_structured_fallback_preserves_damaged_editor_latex():
    formula = r"\(\delta(q_0,a)=q_1\)"
    client = Converse(["A draft [p. 1].", RuntimeError("Structured review unavailable"),
        "Response:\n" + json.dumps(review_data(approved=False, clarity_issues=["Clarify."],
                                               revision_instructions=["Explain notation."])),
        RuntimeError("Structured editor unavailable"),
        '{"lecture_text":"' + formula + '\nA transition [p. 1]."}'])
    final, _, _ = LessonModels(Settings(), client).generate_section(section(), "en")
    assert formula in final.paragraphs[0].text
    assert ["outputConfig" in call for call in client.calls] == [False, True, False, True, False]


def test_inconsistent_original_review_is_rejected_after_its_bounded_fallback():
    raw = json.dumps(review_data(clarity_issues=["Unresolved issue."]))
    client = Converse(["A draft [p. 1].", raw, raw])
    with pytest.raises(IngestionError, match="LESSON_MODEL_INVALID_OUTPUT"):
        LessonModels(Settings(), client).generate_section(section(), "en")
    assert len(client.calls) == 3


def test_non_english_language_is_the_only_prompt_extension():
    english = Converse(["A lecture [p. 1].", json.dumps(review_data())])
    italian = Converse(["A lecture [p. 1].", json.dumps(review_data())])
    LessonModels(Settings(), english).generate_section(section(), "en")
    LessonModels(Settings(), italian).generate_section(section(), "it")
    for en, it in zip(english.calls, italian.calls, strict=True):
        assert it["system"][:-1] == en["system"] and "in it" in it["system"][-1]["text"]
        assert {k: v for k, v in it.items() if k != "system"} == {k: v for k, v in en.items() if k != "system"}


def test_api_paragraphs_preserve_v3_window_provenance_and_multiline_formulas():
    text = "A source equation:\n" + r"\[" + "\nx + y\n\n= z\n" + r"\]" + " [p. 1].\n\nRecap."
    draft = turn_to_draft(TeacherTurn(text=text, source_pages=[1, 2, 5], source_chunk_ids=["source-chunk"]))
    assert len(draft.paragraphs) == 2 and "x + y\n\n= z" in draft.paragraphs[0].text
    assert all(paragraph.pages == [1, 2, 5] for paragraph in draft.paragraphs)


def test_legacy_review_findings_keep_public_categories_and_provenance():
    parsed = LectureReview(**review_data(approved=False, contradictions=["Wrong transition."],
        teaching_quality_issues=["Explain notation."], revision_instructions=["Correct and explain."]))
    report = legacy_review_report(parsed, [1, 2])
    assert [issue.category for issue in report.issues] == ["accuracy", "clarity"]
    assert all(issue.pages == [1, 2] for issue in report.issues) and not report.approved


def test_validation_logs_exclude_generated_content_and_capture_is_opt_in(tmp_path, caplog):
    raw = '{"approved":true,"summary":"PRIVATE_GENERATED_CONTENT"}'
    replies = ["A draft [p. 1].", raw, raw]
    for enabled in [False, True]:
        settings = Settings(environment="development", local_test_mode=True, lesson_capture_model_outputs=enabled)
        folder = tmp_path / str(enabled)
        with pytest.raises(IngestionError):
            LessonModels(settings, Converse(replies), diagnostic_dir=folder).generate_section(section(), "en")
        assert folder.exists() == enabled
        if enabled:
            retained = list(folder.glob("*.json"))
            assert len(retained) == 1
            diagnostic = json.loads(retained[0].read_text())
            assert diagnostic["phase"] == "review" and diagnostic["raw_output"] == raw
            assert all("input" not in error for error in diagnostic["errors"])
    assert "PRIVATE_GENERATED_CONTENT" not in caplog.text


def test_raw_capture_refuses_production_and_nonlocal_development():
    for environment in ["production", "development"]:
        with pytest.raises(ValidationError, match="requires development local_test_mode"):
            Settings(environment=environment, local_test_mode=False, lesson_capture_model_outputs=True)


def test_viseme_timeline_adapter_preserves_marks_without_failing_on_pcm_overshoot():
    raw = b'\n{"type":"viseme","time":1100,"value":"p"}\n{"type":"viseme","time":100,"value":"a"}\n'
    marks, adjusted = align_visemes(raw, 1000, 2000)
    assert adjusted and [mark.value for mark in marks] == ["a", "p"]
    assert 2000 <= marks[0].time_ms < marks[1].time_ms == 3000

    class LongMarks(Polly):
        def synthesize_speech(self, **request):
            return {"AudioStream": io.BytesIO(raw)} if request["OutputFormat"] == "json" else super().synthesize_speech(**request)

    speech = LessonSpeech(Settings(), MathModels(), client=LongMarks())
    _, parts, _ = speech.prepare("A supported sentence.", "en")
    audio, marks, duration, _ = speech.synthesize(parts, "Joanna", "en-US")
    assert audio.startswith(b"RIFF") and marks[-1].time_ms == duration * 1000
    assert speech.last_timing_issues[0].category == "speech"


@pytest.mark.parametrize("time", [-1, True, "100", float("nan"), float("inf")])
def test_viseme_adapter_rejects_invalid_times(time):
    with pytest.raises(IngestionError, match="LESSON_VISEMES_INVALID"):
        align_visemes(json.dumps({"type": "viseme", "time": time, "value": "a"}), 1000, 0)


def test_polly_engine_fallback_keeps_audio_and_marks_on_the_same_engine():
    class FallbackPolly(Polly):
        def synthesize_speech(self, **request):
            if request["Engine"] == "neural":
                self.calls.append(request)
                raise ClientError({"Error": {"Code": "EngineNotSupportedException", "Message": "Unsupported"}}, "SynthesizeSpeech")
            return super().synthesize_speech(**request)

    polly = FallbackPolly()
    speech = LessonSpeech(Settings(), MathModels(), client=polly)
    _, parts, _ = speech.prepare("Source explanation.", "en")
    audio, marks, _, _ = speech.synthesize(parts, "Joanna", "en-US")
    assert audio.startswith(b"RIFF") and len(marks) == 2 and speech.last_engine == "standard"
    assert [call["Engine"] for call in polly.calls] == ["neural", "standard", "standard"]


def test_default_math_converter_preserves_original_batching(monkeypatch):
    batches = []
    monkeypatch.setattr("upraise_ai.services.lesson_speech.MathJaxSpeechRuleConverter.convert",
        lambda self, formulas: batches.append(formulas) or ["x plus one", "y plus two"])
    speech = LessonSpeech(Settings(), MathModels(), client=Polly())
    full, _, issues = speech.prepare(r"Use \(x+1\), then \(y+2\).", "en")
    assert batches == [["x+1", "y+2"]] and "x plus one" in full and not issues


"""A7 agent routing, context, feedback, level and final structural validation."""

import json

import pytest

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.revision_models import RevisionModelInvalidOutput, validate_revision_text


def context():
    return {"document_id": "invented-doc", "document_version_id": "invented-version", "source_language": "en",
            "pages": [1], "window": [1, 1], "chunks": [
                {"chunk_id": "invented-source", "text": "A fictional network has a path length of seven.", "page_numbers": [1]}]}


class Client:
    def __init__(self, *, reject=False, final="A simpler explanation of the fictional network. [p. 1]"):
        review = {"approved": not reject, "summary": "Needs simpler wording." if reject else "Draft checked.",
                  **{name: [] for name in ("contradictions", "unsupported_claims", "missing_source_points",
                                           "citation_issues", "clarity_issues", "teaching_quality_issues", "revision_instructions")}}
        if reject:
            review["clarity_issues"] = ["Use simpler wording."]
            review["revision_instructions"] = ["Use simpler wording."]
        self.responses = [json.dumps({"lecture_text": final}), json.dumps(review),
                          *[json.dumps({"lecture_text": final}) for _ in range(3)]]
        self.calls = []

    def converse(self, **request):
        self.calls.append(request)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": self.responses.pop(0)}]}}}


@pytest.mark.parametrize("reject", [False, True])
def test_a7_uses_generator_reviewer_editor_models_and_teacher_context(reject):
    client = Client(reject=reject)
    settings = Settings()
    models = LessonModels(settings, client)
    phases = []
    final, report, edits = models.revise_chunk(context(), "Original explanation. [p. 1]",
        "Explain in simpler words and do not add a question.", [{"text": "Previous chunk."}],
        learner_level="beginner", notify=phases.append)
    assert final.endswith("[p. 1]") and edits == int(reject)
    assert report.approved == (not reject)
    assert phases == ["REVIEW", "FINALIZE"]
    assert [call["modelId"] for call in client.calls] == [settings.lesson_generation_model_id,
        settings.lesson_review_model_id, *([settings.lesson_generation_model_id] if reject else [])]
    for call in client.calls:
        prompt = call["messages"][0]["content"][0]["text"]
        assert "Original explanation." in prompt and "Previous chunk." in prompt
        assert "Explain in simpler words" in prompt and "invented-source" in prompt
        assert "Requested explanation level: beginner" in json.dumps(call["system"])
    assert models.transport.teaching_prompt is None


def test_a7_rejects_final_citations_outside_pinned_source():
    client = Client(final="An explanation. [p. 2]")
    models = LessonModels(Settings(), client)
    with pytest.raises(RevisionModelInvalidOutput) as caught:
        models.revise_chunk(context(), "Original. [p. 1]", "Simplify.", [], learner_level="advanced", notify=lambda *a: None)
    error = caught.value
    assert error.code == "REVISION_MODEL_INVALID_OUTPUT" and not error.retryable
    assert error.details["reason_code"] == "CITATION_OUTSIDE_SOURCE"
    assert error.details["correction_rounds"] == error.details["max_correction_rounds"] == 2
    assert len(client.calls) == 4  # generator/reviewer plus two bounded corrections


@pytest.mark.parametrize("invalid,reason_code", [
    ("A supported explanation without a citation.", "CITATIONS_MISSING"),
    ("A supported explanation. [p. 2]", "CITATION_OUTSIDE_SOURCE"),
    ("A supported explanation. [pp. 2-1]", "CITATION_RANGE_REVERSED"),
])
def test_a7_repairs_final_validation_with_specific_finding_before_rechecking(invalid, reason_code):
    client = Client(final=invalid)
    client.responses[2] = json.dumps({"lecture_text": "Corrected explanation without professor attribution. [p. 1]"})
    models = LessonModels(Settings(), client)
    final, report, edits = models.revise_chunk(context(), "Original. [p. 1]", "Remove lecturer attribution.", [],
                                             learner_level="beginner", notify=lambda *a: None)
    assert final.endswith("[p. 1]") and edits == 1 and report.approved is False
    assert len(client.calls) == 3
    prompt = client.calls[-1]["messages"][0]["content"][0]["text"]
    assert "VALIDATION_FINDING" in prompt and "Remove lecturer attribution." in prompt
    assert invalid in prompt and "invented-source" in prompt
    assert models.transport.teaching_prompt is None


def test_a7_invalid_final_json_is_regenerated_without_loose_string_repair():
    client = Client(reject=True)
    client.responses[2:4] = ['{"lecture_text": "broken\\zPRIVATE_RESPONSE"}'] * 2
    client.responses[4] = json.dumps({"lecture_text": "Corrected grounded text. [p. 1]"})
    final, report, edits = LessonModels(Settings(), client).revise_chunk(
        context(), "Original. [p. 1]", "Simplify.", [], learner_level="beginner", notify=lambda *a: None)
    assert final == "Corrected grounded text. [p. 1]" and edits == 1 and not report.approved
    prompt = client.calls[-1]["messages"][0]["content"][0]["text"]
    assert "required final-text JSON structure" in prompt
    assert "PRIVATE_RESPONSE" not in prompt and "A simpler explanation" in prompt


def test_a7_exhausted_final_json_repairs_return_safe_terminal_details():
    client = Client(reject=True)
    client.responses[2:] = ['{"lecture_text": "broken\\zPRIVATE_RESPONSE"}'] * 6
    with pytest.raises(RevisionModelInvalidOutput) as caught:
        LessonModels(Settings(), client).revise_chunk(context(), "Original. [p. 1]", "Simplify.", [],
                                                     learner_level="beginner", notify=lambda *a: None)
    assert len(client.calls) == 8  # two normal roles, three pairs of structured calls
    assert not caught.value.retryable and caught.value.details["correction_rounds"] == 2
    assert caught.value.details["reason_code"] == "OUTPUT_SCHEMA_INVALID"
    assert "PRIVATE_RESPONSE" not in json.dumps(caught.value.details)


def test_a7_failed_initial_generation_keeps_phase_without_private_exception_details():
    client = Client()
    client.responses = ["PRIVATE_RESPONSE"] * 2
    with pytest.raises(RevisionModelInvalidOutput) as caught:
        LessonModels(Settings(), client).revise_chunk(context(), "Original. [p. 1]", "Simplify.", [],
                                                     learner_level="beginner", notify=lambda *a: None)
    assert caught.value.retryable and caught.value.details["stage"] == "revision_generate"
    assert caught.value.details["max_correction_rounds"] == 0
    assert "PRIVATE_RESPONSE" not in json.dumps(caught.value.details)


@pytest.mark.parametrize("text", ["", " ", "invalid\x08text", "x" * 16001, "bad\ud800text"])
def test_a7_invalid_text_cannot_reach_ssml_or_polly(text):
    with pytest.raises(IngestionError, match="REVISION_TEXT_INVALID"):
        validate_revision_text(text)

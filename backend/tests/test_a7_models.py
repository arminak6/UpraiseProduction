"""A7 agent routing, context, feedback, level and final structural validation."""

import json

import pytest

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.revision_models import validate_revision_text


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
        self.responses = [json.dumps({"lecture_text": final}), json.dumps(review), json.dumps({"lecture_text": final})]
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
    models = LessonModels(Settings(), Client(final="An explanation. [p. 2]"))
    with pytest.raises(IngestionError, match="REVISION_MODEL_INVALID_OUTPUT"):
        models.revise_chunk(context(), "Original. [p. 1]", "Simplify.", [], learner_level="advanced", notify=lambda *a: None)


@pytest.mark.parametrize("text", ["", " ", "invalid\x08text", "x" * 16001, "bad\ud800text"])
def test_a7_invalid_text_cannot_reach_ssml_or_polly(text):
    with pytest.raises(IngestionError, match="REVISION_TEXT_INVALID"):
        validate_revision_text(text)

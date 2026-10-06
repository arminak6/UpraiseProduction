"""Explanation-level policy reaches Bedrock roles without leaking across jobs or speech."""

import json

import pytest
from test_a3_model_compat import Converse, review_data, section

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.lesson_models import LessonModels


@pytest.mark.parametrize("level, guidance", [
    ("beginner", "Use plain language"),
    ("intermediate", "Assume basic familiarity"),
    ("advanced", "Assume a strong foundation"),
])
def test_selected_explanation_level_guides_generator_reviewer_and_editor(level, guidance):
    client = Converse(["A source explanation [p. 1].", json.dumps(review_data(approved=False,
        teaching_quality_issues=["The explanation does not suit the requested level."],
        revision_instructions=["Adapt the explanation using source facts."])),
        json.dumps({"lecture_text": "A revised source explanation [p. 1]."})])
    models = LessonModels(Settings(), client)
    final, report, revisions = models.generate_section(section(), "en", learner_level=level)
    assert final.paragraphs and not report.approved and revisions == 1
    assert len(client.calls) == 3
    for call in client.calls:
        policy = call["system"][-1]["text"]
        assert f"Requested explanation level: {level}." in policy and guidance in policy
        assert "preserve all substantive source points" in policy
        assert "explicitly acknowledge source gaps" in policy
    assert "Write the guided lesson" in client.calls[0]["system"][-1]["text"]
    assert "Do not approve a draft with an unresolved level mismatch" in client.calls[1]["system"][-1]["text"]
    assert "preserving the requested explanation level" in client.calls[2]["system"][-1]["text"]


@pytest.mark.parametrize("failed", [False, True])
def test_level_context_is_cleared_for_speech_and_next_generation(failed):
    first_replies = ["Invalid final page [p. 999].", json.dumps(review_data())] if failed else [
        "A source explanation [p. 1].", json.dumps(review_data())]
    client = Converse([*first_replies,
        json.dumps({"formulas": [{"formula_id": 0, "narration": "x plus one"}]}),
        json.dumps({"formulas": [{"formula_id": 0, "narration": "x plus one", "verified": True,
                                   "unresolved_issues": []}]}),
        "Another source explanation [p. 1].", json.dumps(review_data())])
    models = LessonModels(Settings(), client)
    if failed:
        with pytest.raises(IngestionError):
            models.generate_section(section(), "en", learner_level="advanced")
    else:
        models.generate_section(section(), "en", learner_level="advanced")
    assert models.math_narration("x+1", "A source formula.", "en") == "x plus one"
    assert all("Requested explanation level" not in block["text"]
               for call in client.calls[2:4] for block in call["system"])
    models.generate_section(section(), "en", learner_level="beginner")
    assert all("Requested explanation level: beginner." in call["system"][-1]["text"]
               for call in client.calls[4:])


def test_invalid_level_never_reaches_provider():
    client = Converse([])
    with pytest.raises(ValueError, match="Unsupported learner level"):
        LessonModels(Settings(), client).generate_section(section(), "en", learner_level="expert")
    assert not client.calls

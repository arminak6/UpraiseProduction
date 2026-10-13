"""A7 final-review context, corrective feedback and crash-safe bounded attempts."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_a3_model_compat import Converse
from test_a7_models import context

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedReview, AlignedText, AlignedTexts, ContentUnit
from upraise_ai.services.aligned_models import reviewed
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.revision_models import correct_revision
from upraise_ai.services.revision_validation import RevisionAlignmentRejected, validate_aligned_revision


def unit():
    return ContentUnit(content_unit_id=uuid4(), index=0, section_index=0, source_language="en",
        material_text="Professor Example introduces a network with path length seven.",
        source={"document_id": "invented-doc", "document_version_id": "invented-version", "pages": [1],
                "source_chunk_ids": ["invented-source"], "quality_status": "AUTO_CHECKED"})


def findings(teaching_unit, passed=False):
    return AlignedReview(units=[{"content_unit_id": teaching_unit.content_unit_id,
        "material_preserved": passed, "level_suitable": True, "in_language": True,
        "issues": [] if passed else ["The required path length seven is missing."],
        "revision_instructions": [] if passed else ["Restore the path length seven while omitting lecturer attribution."]}])


@pytest.mark.parametrize("is_revision", [False, True])
def test_review_payload_and_policy_include_teacher_context_only_for_a7(is_revision):
    teaching_unit = unit()
    client = Converse([findings(teaching_unit, True).model_dump_json()])
    models = LessonModels(Settings(), client)
    section = context()
    if is_revision:
        section["revision_context"] = {"teacher_feedback": "Remove the professor's name.",
                                       "original_chunk": "Professor Example explains path length seven."}
    reviewed(models, section, [teaching_unit], AlignedTexts(units=[AlignedText(
        content_unit_id=teaching_unit.content_unit_id, text="The network has path length seven. [p. 1]")]),
        "en", "beginner", None)
    payload = json.loads(client.calls[0]["messages"][0]["content"][0]["text"])
    system = json.dumps(client.calls[0]["system"])
    assert ("revision_context" in payload) is is_revision
    assert ("These presentation details are not required teaching material" in system) is is_revision
    assert "Reject changed values, operators or assumptions" in system
    if is_revision:
        assert payload["revision_context"] == section["revision_context"]


def test_corrector_uses_editor_target_language_specific_findings_and_source_scope():
    teaching_unit = unit()
    client = Converse([json.dumps({"lecture_text": "Il percorso ha lunghezza sette. [p. 1]"})])
    models = LessonModels(Settings(), client)
    section = {**context(), "alignment_unit": teaching_unit.model_dump(mode="json")}
    result = correct_revision(models, section, "Il percorso manca di una misura.", "Remove lecturer attribution.",
                              [{"index": 1, "text": "Next topic."}], findings(teaching_unit), "it", "beginner")
    assert "lunghezza sette" in result
    request = client.calls[0]
    prompt = request["messages"][0]["content"][0]["text"]
    assert "Restore the path length seven" in prompt and "Remove lecturer attribution" in prompt
    assert "invented-source" in prompt and "Next topic." in prompt
    assert "Italian" in json.dumps(request["system"])
    assert "Requested explanation level: beginner" in json.dumps(request["system"])
    assert request["modelId"] == models.settings.lesson_generation_model_id
    assert models.transport.teaching_prompt is None


def inputs(tmp_path):
    teaching_unit = unit()
    section = {**context(), "alignment_unit": teaching_unit.model_dump(mode="json")}
    data = SimpleNamespace(revision_request=SimpleNamespace(aligned_unit=section["alignment_unit"],
        teacher_review="Remove lecturer attribution."), options=SimpleNamespace(max_chunk_words=50, learner_level="beginner"),
        language="en", level_profiles=None)
    selected = SimpleNamespace(text="Professor Example teaches a path of length seven.", chunk_id=str(uuid4()), index=0)
    models = SimpleNamespace(transport=SimpleNamespace(language="en", teaching_prompt=None))
    saved = {"signature": "invented", "text": "The network has a path. [p. 1]", "edits": 0, "correction_rounds": 0}
    path = tmp_path / "candidate.json"
    return teaching_unit, section, data, selected, models, saved, path


def test_rejection_feedback_corrects_candidate_then_reviews_new_text(tmp_path, monkeypatch):
    teaching_unit, section, data, selected, models, saved, path = inputs(tmp_path)
    reviewed_texts, corrected_texts = [], []

    def reviewer(models, section, units, texts, *args):
        text = texts.units[0].text
        reviewed_texts.append(text)
        assert section["revision_context"]["teacher_feedback"] == data.revision_request.teacher_review
        return findings(teaching_unit, "seven" in text)

    def correct(models, section, text, feedback, neighbours, report, language, level):
        corrected_texts.append(text)
        assert report.units[0].revision_instructions and feedback == data.revision_request.teacher_review
        return "The network has path length seven. [p. 1]"

    monkeypatch.setattr("upraise_ai.services.revision_validation.correct_revision", correct)
    text, rounds = validate_aligned_revision(models, section, data, selected, [], saved, path, lambda *args: None, reviewer)
    assert "seven" in text and rounds == 1 and len(corrected_texts) == 1
    assert reviewed_texts == ["The network has a path. [p. 1]", text]
    cached = json.loads(path.read_text())
    assert cached["alignment_review"]["units"][0]["material_preserved"] is True
    validate_aligned_revision(models, section, data, selected, [], cached, path, lambda *args: None, reviewer)
    assert len(reviewed_texts) == 2 and len(corrected_texts) == 1


@pytest.mark.parametrize("unchanged", [False, True])
def test_exhausted_corrections_fail_once_with_retained_reason_and_no_blind_retry(tmp_path, monkeypatch, unchanged):
    teaching_unit, section, data, selected, models, saved, path = inputs(tmp_path)
    corrections, reviews = [], []

    def reviewer(*args):
        reviews.append(True)
        return findings(teaching_unit)

    def correct(models, section, text, *args):
        corrections.append(True)
        return text if unchanged else f"Another incomplete explanation {len(corrections)}. [p. 1]"

    monkeypatch.setattr("upraise_ai.services.revision_validation.correct_revision", correct)
    with pytest.raises(RevisionAlignmentRejected) as caught:
        validate_aligned_revision(models, section, data, selected, [], saved, path, lambda *args: None, reviewer)
    assert len(corrections) == 2 and len(reviews) == (1 if unchanged else 3)
    assert caught.value.retryable is False and caught.value.details["correction_rounds"] == 2
    assert "length seven" in caught.value.details["reason"]
    with pytest.raises(RevisionAlignmentRejected):
        validate_aligned_revision(models, section, data, selected, [], json.loads(path.read_text()), path,
                                  lambda *args: None, reviewer)
    assert len(corrections) == 2 and len(reviews) == (1 if unchanged else 3)


def test_provider_failure_during_correction_does_not_reset_attempt_budget(tmp_path, monkeypatch):
    teaching_unit, section, data, selected, models, saved, path = inputs(tmp_path)
    calls = []

    def unavailable(*args):
        calls.append(True)
        raise IngestionError("INVENTED_PROVIDER_OUTAGE", retryable=True)

    monkeypatch.setattr("upraise_ai.services.revision_validation.correct_revision", unavailable)
    def reviewer(*args):
        return findings(teaching_unit)
    for expected in (1, 2):
        with pytest.raises(IngestionError, match="INVENTED_PROVIDER_OUTAGE"):
            validate_aligned_revision(models, section, data, selected, [], saved, path, lambda *args: None, reviewer)
        saved = json.loads(path.read_text())
        assert saved["correction_rounds"] == expected
    with pytest.raises(RevisionAlignmentRejected):
        validate_aligned_revision(models, section, data, selected, [], saved, path, lambda *args: None, reviewer)
    assert len(calls) == 2


def test_wrong_cached_unit_identity_cannot_pass_review(tmp_path):
    _, section, data, selected, models, saved, path = inputs(tmp_path)
    saved.update(alignment_review=findings(unit(), True).model_dump(mode="json"),
                 alignment_text_sha256=hashlib.sha256(saved["text"].encode()).hexdigest())
    with pytest.raises(IngestionError, match="REVISION_REVIEW_INTEGRITY_FAILURE"):
        validate_aligned_revision(models, section, data, selected, [], saved, path, lambda *args: None,
                                  lambda *args: pytest.fail("Do not ignore corrupt cached review."))

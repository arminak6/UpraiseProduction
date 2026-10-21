"""Unusable reviews produce explicit, durable DRAFT flags, never silent approval."""

import json
from uuid import uuid4

import pytest
from test_a3 import Polly
from test_a3_model_compat import Converse
from test_a3_v2 import unit
from test_teaching_v3 import source

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import SectionDraft
from upraise_ai.services.teaching_v2 import TeachingModelsV2, TeachingSpeechV2
from upraise_ai.services.teaching_v2.contracts import (
    MaterialDecision,
    Narration,
    TeachingMaterialRejected,
    TeachingReviewEvidenceInvalid,
    validate_written,
)
from upraise_ai.services.teaching_v2.first_draft import POLICY as FIRST_DRAFT_POLICY
from upraise_ai.services.teaching_v2.first_draft import storable_text
from upraise_ai.services.teaching_v2.review_recovery import FLAG_PREFIX, POLICY, gap_text
from upraise_ai.services.teaching_v3 import TeachingModelsV3, TeachingSpeechV3
from upraise_ai.services.teaching_v3.review import review_sequence


def enabled(model_type, replies=(), folder=None):
    client = Converse([json.dumps(r) for r in replies])
    model = model_type(Settings(), client, diagnostic_dir=folder / "models" if folder else None)
    assert model.teaching_versions["draft_review_recovery"] == (FIRST_DRAFT_POLICY if model_type is TeachingModelsV2 else POLICY)
    model.draft_review_recovery = True
    return model, client


def draft(text="The supported value is three."):
    return SectionDraft(paragraphs=[{"text": text, "pages": [1]}])


@pytest.mark.parametrize("text,problem", [
    (r"Broken\ntext", "LITERAL_NEWLINE_ESCAPE"),
    (r"Broken \u0041 text", "LITERAL_UNICODE_ESCAPE"),
    (r"Broken \\(x=3\\)", "DOUBLE_ESCAPED_MATH_DELIMITER"),
    ("Broken\x08text", "CONTROL_CHARACTER"),
])
@pytest.mark.parametrize("usable_source", [True, False])
def test_first_draft_format_findings_keep_teaching_text_and_remove_only_unstorable_characters(text, problem, usable_source):
    model, client = enabled(TeachingModelsV2)
    section = source(1, "The source value is three." if usable_source else text)
    result, _, warnings = model.checked_material(section, draft(text), "en", "intermediate", allow_source_excerpt=True)
    assert not client.calls
    assert result.paragraphs[0].text == storable_text(text)
    assert warnings[0].category == "clarity" and warnings[0].pages == [1]
    assert "TEACHING_V2_TEXT_INVALID" in warnings[0].message and problem in warnings[0].message
    with pytest.raises(IngestionError, match="TEACHING_V2_TEXT_INVALID"):
        validate_written(text)


def test_actual_v28_quote_and_indexed_repair_failure_retains_flagged_draft(monkeypatch):
    invalid = {"findings": [{"category": "changed_fact", "candidate_index": 0,
        "candidate_quote": "Words absent from this candidate", "required_index": 0,
        "required_quote": "Words absent from the source", "message": "An alleged error."}],
        "warnings": [], "natural": True}
    indexed = {"findings": [{"category": "changed_fact", "candidate_index": 0,
        "candidate_span_index": 999, "required_index": 0, "required_span_index": 0,
        "message": "An alleged error."}], "warnings": [], "natural": True}
    model, client = enabled(TeachingModelsV2, [draft().model_dump(), invalid, invalid, indexed])
    monkeypatch.setattr(model, "select_source", lambda s: (s, []))
    result, report, _ = model.generate_section(source(1, "The supported value is three."), "en")
    assert result == draft() and len(client.calls) == 4
    assert not report.approved and report.issues[0].pages == [1]
    assert "INDEXED_EVIDENCE_INVALID" in report.issues[0].message
    assert report.issues[0].message.startswith(FLAG_PREFIX)


@pytest.mark.parametrize("model_type", [TeachingModelsV2, TeachingModelsV3])
def test_review_failure_keeps_current_corrected_candidate_and_strict_revision_gate(monkeypatch, model_type):
    model, _ = enabled(model_type)
    def broken(*args, **kwargs):
        raise TeachingReviewEvidenceInvalid("material_review", "INDEXED_EVIDENCE_INVALID")
    monkeypatch.setattr(model, "check_written", broken)
    result, _, warnings = model.checked_material(source(1, "The source."), draft(), "en", "intermediate",
                                                allow_source_excerpt=True)
    assert result == draft() and warnings[0].message.startswith(FLAG_PREFIX)
    with pytest.raises(TeachingReviewEvidenceInvalid):
        model.checked_material(source(1, "The source."), draft(), "en", "intermediate")


@pytest.mark.parametrize("model_type,module", [(TeachingModelsV2, "teaching_v2"), (TeachingModelsV3, "teaching_v3")])
def test_review_failure_after_a_correction_retains_the_current_candidate(monkeypatch, model_type, module):
    model, _ = enabled(model_type)
    corrected = draft("The corrected source value is three.")
    calls = []
    def check(section, texts, language, level, *args, **kwargs):
        calls.append(texts)
        if len(calls) == 1:
            raise TeachingMaterialRejected(MaterialDecision(correct=False, complete=True, in_language=True,
                natural=True, issues=["Restore the source value."]), section=section, learner_level=level, correction_rounds=0)
        raise TeachingReviewEvidenceInvalid("material_review", "INDEXED_EVIDENCE_INVALID")
    monkeypatch.setattr(model, "check_written", check)
    monkeypatch.setattr("upraise_ai.services." + module + ".models.structured", lambda *a, **k: corrected)
    result, rounds, warnings = model.checked_material(source(1, "The source value is three."), draft(), "en",
                                                       "intermediate", allow_source_excerpt=True)
    if model_type is TeachingModelsV2:
        assert result == draft() and rounds == 0 and len(calls) == 1
        assert "Restore the source value." in warnings[0].message
    else:
        assert result == corrected and rounds == 1 and len(calls) == 2
        assert "INDEXED_EVIDENCE_INVALID" in warnings[0].message


@pytest.mark.parametrize("model_type", [TeachingModelsV2, TeachingModelsV3])
def test_known_rejection_uses_visible_gap_when_source_language_is_unsuitable(monkeypatch, model_type):
    model, _ = enabled(model_type)
    def rejected(section, texts, language, level, *args, **kwargs):
        raise TeachingMaterialRejected(MaterialDecision(correct=False, complete=True, in_language=True,
            natural=True, issues=["The value was changed."]), section=section, learner_level=level, correction_rounds=0)
    monkeypatch.setattr(model, "check_written", rejected)
    module = "teaching_v3" if model_type is TeachingModelsV3 else "teaching_v2"
    monkeypatch.setattr("upraise_ai.services." + module + ".models.structured", lambda *a, **k: draft())
    result, rounds, warnings = model.checked_material(source(1, "The original source."), draft(), "it", "intermediate",
                                                       allow_source_excerpt=True)
    assert result.paragraphs[0].text == (draft().paragraphs[0].text if model_type is TeachingModelsV2 else gap_text("it"))
    assert rounds == (0 if model_type is TeachingModelsV2 else 2)
    assert "The value was changed." in warnings[0].message


@pytest.mark.parametrize("model_type", [TeachingModelsV2, TeachingModelsV3])
def test_reference_identity_and_operational_errors_remain_real_failures(monkeypatch, model_type):
    model, _ = enabled(model_type)
    def unavailable(*args, **kwargs):
        raise IngestionError("OPERATIONS_STORAGE_UNAVAILABLE")
    monkeypatch.setattr(model, "check_written", unavailable)
    with pytest.raises(IngestionError, match="OPERATIONS_STORAGE_UNAVAILABLE"):
        model.checked_material(source(1, "The source."), draft(), "en", "intermediate", allow_source_excerpt=True)
    expected, other = unit(), unit().model_copy(update={"content_unit_id": uuid4()})
    wrong = AlignedTexts(units=[AlignedText(content_unit_id=other.content_unit_id, text="A fact.")])
    with pytest.raises(IngestionError, match="LESSON_ALIGNMENT_IDS_INVALID"):
        model.checked_material(source(1, "The source."), wrong, "en", "intermediate", units=[expected], allow_source_excerpt=True)


@pytest.mark.parametrize("model_type,speech_type,module", [
    (TeachingModelsV2, TeachingSpeechV2, "teaching_v2"),
    (TeachingModelsV3, TeachingSpeechV3, "teaching_v3"),
])
def test_unreadable_narration_review_is_flagged_cached_and_never_reused_as_strict_approval(
        monkeypatch, tmp_path, model_type, speech_type, module):
    model, _ = enabled(model_type, folder=tmp_path)
    def request(models, policy, payload, schema, name, **kwargs):
        if name.endswith("narration_review"):
            raise IngestionError("TEACHING_V2_MODEL_OUTPUT_INVALID")
        return Narration(text="The supported value is three.")
    monkeypatch.setattr("upraise_ai.services." + module + ".speech.structured", request)
    voice = speech_type(model.settings, model, client=Polly())
    if module == "teaching_v2":
        _, warnings = voice.narration("The supported value is three.", "en")
    else:
        assert voice.script("The supported value is three.", "en", {}, [], []) == "The supported value is three."
        warnings = next(iter(voice.source_warnings.values()))
    assert warnings and warnings[0].category == "speech" and warnings[0].message.startswith(FLAG_PREFIX)
    def no_calls(*args, **kwargs):
        pytest.fail("The flagged checkpoint should retain both the script and its warning.")
    monkeypatch.setattr("upraise_ai.services." + module + ".speech.structured", no_calls)
    retry_model, _ = enabled(model_type, folder=tmp_path)
    retry = speech_type(retry_model.settings, retry_model, client=Polly())
    if module == "teaching_v2":
        assert retry.narration("The supported value is three.", "en")[1] == warnings
    else:
        retry.script("The supported value is three.", "en", {}, [], [])
        assert next(iter(retry.source_warnings.values())) == warnings
    retry_model.draft_review_recovery = False
    monkeypatch.setattr("upraise_ai.services." + module + ".speech.structured", request)
    with pytest.raises(IngestionError, match="TEACHING_V2_MODEL_OUTPUT_INVALID"):
        if module == "teaching_v2":
            retry.narration("The supported value is three.", "en")
        else:
            retry.script("The supported value is three.", "en", {}, [], [])


def test_unreadable_course_position_does_not_skip_review_of_later_parts(monkeypatch):
    from upraise_ai.services.teaching_v3.contracts import ContinuityCheck
    model, _ = enabled(TeachingModelsV3)
    seen = []
    def request(models, policy, payload, *args, **kwargs):
        seen.append(payload["part_index"])
        if payload["part_index"] == 0:
            raise IngestionError("TEACHING_V2_MODEL_OUTPUT_INVALID")
        return ContinuityCheck(consistent=True, connected=True, complete=True, issues=[])
    monkeypatch.setattr("upraise_ai.services.teaching_v3.review.structured", request)
    texts = ["First definition.", "Second definition."]
    result, warnings = review_sequence(model, texts, [{}, {}], language="en", kind="written", repair=lambda *a: None)
    assert result == texts and seen == [0, 1] and warnings[0] and not warnings[1]


def test_translation_review_evidence_failure_keeps_structurally_valid_translation(monkeypatch):
    translated = draft("Il valore è tre.")
    model, client = enabled(TeachingModelsV2, [
        {"paragraphs": [{"id": 0, "text": "Il valore è tre."}]},
        {"target_language": True, "faithful": False, "level_preserved": True,
         "issues": [{"category": "faithful", "paragraph_id": 99, "quote": "Absent quote",
                     "reason": "An alleged omission.", "instruction": "Restore the meaning."}]},
    ])
    assert model.translate_section(draft(), "en", "it", learner_level="intermediate") == translated
    assert len(client.calls) == 2
    assert model.translation_review_warnings[0].pages == [1]
    assert model.translation_review_warnings[0].message.startswith(FLAG_PREFIX)


@pytest.mark.parametrize("model_type,speech_type,module", [
    (TeachingModelsV2, TeachingSpeechV2, "teaching_v2"),
    (TeachingModelsV3, TeachingSpeechV3, "teaching_v3"),
])
def test_unusable_narration_uses_review_notice_without_speaking_broken_markup(monkeypatch, model_type, speech_type, module):
    model, _ = enabled(model_type)
    monkeypatch.setattr("upraise_ai.services." + module + ".speech.structured",
                        lambda *a, **k: Narration(text=r"Broken\nmathematical markup."))
    voice = speech_type(model.settings, model, client=Polly())
    if module == "teaching_v2":
        narration, warnings = voice.narration("The written value is 0.004.", "en")
        assert narration.text == r"Broken\nmathematical markup." and warnings
    else:
        assert voice.script("The written value is 0.004.", "en", {}, [], []) == gap_text("en")
        assert next(iter(voice.source_warnings.values()))


def test_unavailable_difficulty_reviewer_preserves_unit_identity_and_flag(monkeypatch):
    from upraise_ai.services.aligned_models import AlignmentModelError
    model, _ = enabled(TeachingModelsV2)
    item = unit()
    expected = AlignedTexts(units=[AlignedText(content_unit_id=item.content_unit_id, text=item.material_text)])
    def request(models, policy, payload, schema, name, **kwargs):
        if name == "aligned_unit_review":
            raise AlignmentModelError(name, "UNIT_IDS_INVALID")
        return expected
    monkeypatch.setattr("upraise_ai.services.aligned_models.structured", request)
    monkeypatch.setattr(model, "check_written", lambda *a, **k: [])
    monkeypatch.setattr(model, "select_source", lambda s: (s, []))
    texts, reports = model.adapt_units(source(1, item.material_text), [item], "en", learner_level="intermediate",
        profiles=model.settings.lesson_level_profiles, target_words=30, max_words=100)
    assert texts == expected and not reports[str(item.content_unit_id)].approved
    assert reports[str(item.content_unit_id)].issues[0].message.startswith(FLAG_PREFIX)


def test_first_written_candidate_is_checkpointed_before_review_and_reused_after_interruption(monkeypatch, tmp_path):
    original = draft("The very first explanation has value three.")
    model, client = enabled(TeachingModelsV2, [original.model_dump()], tmp_path)
    monkeypatch.setattr(model, "select_source", lambda s: (s, []))
    def interrupted(*args, **kwargs):
        raise IngestionError("OPERATIONS_STORAGE_UNAVAILABLE")
    monkeypatch.setattr(model, "check_written", interrupted)
    with pytest.raises(IngestionError, match="OPERATIONS_STORAGE_UNAVAILABLE"):
        model.generate_section(source(1, "The source value is three."), "en")
    assert len(client.calls) == 1
    assert len(list((tmp_path / "teaching-v2-first-drafts").glob("*.json"))) == 1
    retry, retry_client = enabled(TeachingModelsV2, folder=tmp_path)
    monkeypatch.setattr(retry, "select_source", lambda s: (s, []))
    monkeypatch.setattr(retry, "check_written", lambda *a, **k: [])
    result, _, _ = retry.generate_section(source(1, "The source value is three."), "en")
    assert result == original and not retry_client.calls


def test_first_draft_keeps_facts_and_unavailable_citation_tags_for_human_review(monkeypatch):
    model, _ = enabled(TeachingModelsV2)
    monkeypatch.setattr(model, "check_written", lambda *a, **k: [])
    original = SectionDraft(paragraphs=[{"text": "The value remains 0.004. [p. 999]", "pages": [999]}])
    retained, _, warnings = model.checked_material(source(1, "The source."), original, "en", "intermediate",
                                                    allow_source_excerpt=True)
    assert retained.paragraphs[0].text == original.paragraphs[0].text
    assert retained.paragraphs[0].pages == [1]
    assert original.paragraphs[0].pages == [999] and "999" in original.paragraphs[0].text
    assert warnings and all(w.category == "citations" for w in warnings)


def test_failed_narrator_reuses_first_written_text_instead_of_replacing_it_with_a_gap(monkeypatch):
    model, _ = enabled(TeachingModelsV2)
    def unavailable(*args, **kwargs):
        raise IngestionError("TEACHING_V2_MODEL_OUTPUT_INVALID")
    monkeypatch.setattr("upraise_ai.services.teaching_v2.speech.structured", unavailable)
    written = "The first written explanation has value three."
    voice = TeachingSpeechV2(model.settings, model, client=Polly())
    narration, warnings = voice.narration(written, "en")
    assert narration.text == written and warnings[0].category == "speech"


def test_failed_translation_review_keeps_first_translation_without_a_second_rewrite():
    model, client = enabled(TeachingModelsV2, [
        {"paragraphs": [{"id": 0, "text": "Il valore è tre."}]},
        {"target_language": True, "faithful": False, "level_preserved": True,
         "issues": [{"category": "faithful", "paragraph_id": 0, "quote": "tre",
                     "reason": "A suspected value change.", "instruction": "Check the source value."}]},
    ])
    translated = model.translate_section(draft(), "en", "it", learner_level="intermediate")
    assert translated.paragraphs[0].text == "Il valore è tre." and len(client.calls) == 2
    assert model.translation_review_warnings and "LESSON_TRANSLATION_REJECTED" in model.translation_review_warnings[0].message

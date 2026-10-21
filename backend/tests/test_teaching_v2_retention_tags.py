"""V2 original output survives unavailable reviewers, validation and retries."""

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from test_a3 import Polly
from test_a3_v2 import unit
from test_teaching_review_recovery import draft, enabled
from test_teaching_v3 import source

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.schemas.aligned_lessons import AlignedReview, AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import ReviewReport
from upraise_ai.services import aligned_models
from upraise_ai.services.aligned_pipeline import AlignedLessonPipeline
from upraise_ai.services.lesson_pipeline import validate_draft
from upraise_ai.services.lesson_speech import LessonSpeech
from upraise_ai.services.lesson_translation import _Translated, _TranslationCheck
from upraise_ai.services.source_references import display_citations
from upraise_ai.services.teaching_v2 import TeachingModelsV2, TeachingSpeechV2
from upraise_ai.services.teaching_v2.contracts import Decision, Narration
from upraise_ai.services.teaching_v2.review_recovery import FLAG_PREFIX
from upraise_ai.services.teaching_v3 import TeachingModelsV3


def provider_failure():
    return ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "PRIVATE provider detail"}}, "Converse")


def accepted(item):
    return AlignedReview(units=[{"content_unit_id": item.content_unit_id,
        "material_preserved": True, "level_suitable": True, "in_language": True}])


@pytest.mark.parametrize("failure", [provider_failure(), ValueError("PRIVATE response"), RuntimeError("PRIVATE response")])
def test_material_reviewer_failures_keep_original_text_and_safe_tag(monkeypatch, failure):
    model, _ = enabled(TeachingModelsV2)
    def unavailable(*args, **kwargs):
        raise failure
    monkeypatch.setattr(model, "_check_written", unavailable)
    original = draft("The exact generator output. [p. 1]")
    result, _, warnings = model.checked_material(source(1, "A source."), original, "en", "intermediate", allow_source_excerpt=True)
    assert result == original
    assert warnings and warnings[0].message.startswith(FLAG_PREFIX)
    assert "TEACHING_V2_REVIEW_UNAVAILABLE" in warnings[0].message
    assert "PRIVATE" not in warnings[0].message


@pytest.mark.parametrize("failure", [OSError("disk unavailable"), MemoryError(), LeaseLost(), IngestionError("OPERATIONS_STORAGE_UNAVAILABLE")])
def test_actual_storage_ownership_and_resource_failures_remain_recoverable_by_worker(monkeypatch, failure):
    model, _ = enabled(TeachingModelsV2)
    def unavailable(*args, **kwargs):
        raise failure
    monkeypatch.setattr(model, "_check_written", unavailable)
    with pytest.raises(type(failure)):
        model.checked_material(source(1, "A source."), draft(), "en", "intermediate", allow_source_excerpt=True)


@pytest.mark.parametrize("model_type", [TeachingModelsV2, TeachingModelsV3])
def test_provider_failure_during_difficulty_review_only_changes_v2_policy(monkeypatch, model_type):
    model, _ = enabled(model_type)
    item = unit()
    original = AlignedTexts(units=[AlignedText(content_unit_id=item.content_unit_id, text=item.material_text)])
    monkeypatch.setattr(model, "select_source", lambda value: (value, []))
    monkeypatch.setattr(model, "check_written", lambda *args, **kwargs: [])
    monkeypatch.setattr(aligned_models, "structured", lambda *args, **kwargs: original)
    def unavailable(*args, **kwargs):
        raise provider_failure()
    monkeypatch.setattr(aligned_models, "reviewed", unavailable)
    args = (source(1, item.material_text), [item], "en")
    kwargs = dict(learner_level="intermediate", profiles=model.settings.lesson_level_profiles, target_words=30, max_words=100)
    if model_type is TeachingModelsV3:
        with pytest.raises(ClientError):
            model.adapt_units(*args, **kwargs)
    else:
        texts, reports = model.adapt_units(*args, **kwargs)
        assert texts == original
        assert not reports[str(item.content_unit_id)].approved
        assert "PROVIDER_ERROR" in reports[str(item.content_unit_id)].issues[0].message


def test_difficulty_first_candidate_survives_interrupted_review_without_generator_rewrite(monkeypatch, tmp_path):
    item = unit()
    original = AlignedTexts(units=[AlignedText(content_unit_id=item.content_unit_id, text="The first difficulty explanation.")])
    generated = []
    def generate(*args, **kwargs):
        generated.append(True)
        assert len(generated) == 1
        return original
    monkeypatch.setattr(aligned_models, "structured", generate)
    def interrupted(*args, **kwargs):
        raise LeaseLost()
    monkeypatch.setattr(aligned_models, "reviewed", interrupted)
    def model():
        result, _ = enabled(TeachingModelsV2, folder=tmp_path)
        monkeypatch.setattr(result, "select_source", lambda value: (value, []))
        monkeypatch.setattr(result, "check_written", lambda *args, **kwargs: [])
        return result
    first = model()
    kwargs = dict(learner_level="intermediate", profiles=first.settings.lesson_level_profiles, target_words=30, max_words=100)
    with pytest.raises(LeaseLost):
        first.adapt_units(source(1, item.material_text), [item], "en", **kwargs)
    assert list((tmp_path / "teaching-v2-first-drafts").glob("difficulty-*.json"))
    monkeypatch.setattr(aligned_models, "reviewed", lambda *args, **kwargs: accepted(item))
    texts, _ = model().adapt_units(source(1, item.material_text), [item], "en", **kwargs)
    assert texts == original and len(generated) == 1


def test_translation_first_candidate_survives_interrupted_verifier(monkeypatch, tmp_path):
    generated = []
    def request(*args, **kwargs):
        generated.append(True)
        assert len(generated) == 1
        return _Translated(paragraphs=[{"id": 0, "text": "Il valore è tre."}])
    first, _ = enabled(TeachingModelsV2, folder=tmp_path)
    monkeypatch.setattr(first.translator, "request", request)
    def interrupted(*args, **kwargs):
        raise LeaseLost()
    monkeypatch.setattr(first.translator, "verify", interrupted)
    with pytest.raises(LeaseLost):
        first.translate_section(draft(), "en", "it", learner_level="intermediate")
    retry, _ = enabled(TeachingModelsV2, folder=tmp_path)
    monkeypatch.setattr(retry.translator, "request", request)
    monkeypatch.setattr(retry.translator, "verify", lambda *args: _TranslationCheck(
        target_language=True, faithful=True, level_preserved=True, issues=[]))
    result = retry.translate_section(draft(), "en", "it", learner_level="intermediate")
    assert result.paragraphs[0].text == "Il valore è tre." and len(generated) == 1


def test_narration_first_candidate_survives_interrupted_reviewer(monkeypatch, tmp_path):
    generated = []
    interrupted = True
    def request(models, policy, payload, schema, name, **kwargs):
        if name == "teaching_v2_narration":
            generated.append(True)
            assert len(generated) == 1
            return Narration(text="The first narration stays exactly the same.")
        if interrupted:
            raise LeaseLost()
        return Decision(correct=True, complete=True, in_language=True, natural=True, issues=[])
    monkeypatch.setattr("upraise_ai.services.teaching_v2.speech.structured", request)
    first, _ = enabled(TeachingModelsV2, folder=tmp_path)
    with pytest.raises(LeaseLost):
        TeachingSpeechV2(first.settings, first, client=Polly()).narration("A first explanation.", "en")
    interrupted = False
    retry, _ = enabled(TeachingModelsV2, folder=tmp_path)
    narration, warnings = TeachingSpeechV2(retry.settings, retry, client=Polly()).narration("A first explanation.", "en")
    assert narration.text == "The first narration stays exactly the same." and not warnings
    assert len(generated) == 1


def test_citation_only_paragraph_remains_visible_instead_of_becoming_empty(monkeypatch):
    model, _ = enabled(TeachingModelsV2)
    monkeypatch.setattr(model, "check_written", lambda *args, **kwargs: [])
    original = draft("[p. 999]")
    result, _, warnings = model.checked_material(source(1, "A source."), original, "en", "intermediate", allow_source_excerpt=True)
    validate_draft(result, [1])
    assert result == original and warnings and warnings[0].category == "citations"


def test_unrenderable_narration_is_tagged_before_text_save_and_reads_existing_writing(monkeypatch):
    model, _ = enabled(TeachingModelsV2)
    written = "The exact original teaching remains available."
    def request(models, policy, payload, schema, name, **kwargs):
        if name == "teaching_v2_narration":
            return Narration(text="Unrenderable first narration")
        return Decision(correct=True, complete=True, in_language=True, natural=True, issues=[])
    monkeypatch.setattr("upraise_ai.services.teaching_v2.speech.structured", request)
    def render(self, text, language):
        if text == "Unrenderable first narration":
            raise IngestionError("LESSON_SPEECH_EMPTY")
        assert text == written
        return "<speak>" + text + "</speak>", ["<speak>" + text + "</speak>"], []
    monkeypatch.setattr(LessonSpeech, "prepare", render)
    voice = TeachingSpeechV2(model.settings, model, client=Polly())
    voice.prepare_course({"intermediate": [("one", written)]}, "en", "test", lambda *args: None)
    assert any("TEACHING_V2_NARRATION_INVALID" in issue.message for issue in voice.review_issues("one"))
    ssml, _, _ = voice.prepare(written, "en")
    assert written in ssml


@pytest.mark.parametrize("source_format", ["docx", "csv"])
def test_unavailable_citations_are_displayed_unchanged_only_when_requested(source_format):
    item = unit()
    reference = item.source.model_copy(update={"pages": [], "source_format": source_format,
        "source_blocks": [1], "row_ranges": [{"start": 2, "end": 4}] if source_format == "csv" else []})
    text = "The exact first explanation. [p. 999]"
    assert display_citations(text, reference, preserve_invalid=True) == text
    with pytest.raises(IngestionError, match="LESSON_CITATIONS_INVALID"):
        display_citations(text, reference)


def test_oversized_first_paragraph_is_split_without_losing_or_rewriting_characters(monkeypatch):
    model, _ = enabled(TeachingModelsV2)
    monkeypatch.setattr(model, "check_written", lambda *args, **kwargs: [])
    original = draft(r"\(" + "x" * 17000 + r"\)")
    retained, _, warnings = model.checked_material(source(1, "A source."), original, "en", "intermediate", allow_source_excerpt=True)
    parts = model.segment_section(retained, 200)
    assert "".join(text for text, _ in parts) == original.paragraphs[0].text
    assert len(parts) == 2 and all(0 < len(text) <= 16000 for text, _ in parts)
    assert any("TEXT_TOO_LONG" in finding.message for finding in warnings)


def test_canonical_first_text_is_not_regenerated_just_because_word_target_is_exceeded(monkeypatch, tmp_path):
    model, _ = enabled(TeachingModelsV2)
    item = unit().model_copy(update={"material_text": "First " * 80,
        "canonical_review": ReviewReport(approved=True, summary="First material review.")})
    def forbidden(*args, **kwargs):
        raise AssertionError("The generator must not rewrite its first same-level text")
    monkeypatch.setattr(model, "adapt_units", forbidden)
    pipeline = AlignedLessonPipeline(model.settings, None, None, None, model, None)
    pipeline._variant_folder = tmp_path
    data = SimpleNamespace(level_profiles={"intermediate": "A profile"}, plan_level="intermediate", language="en",
        options=SimpleNamespace(target_chunk_words=30, max_chunk_words=50))
    result = pipeline.generate_variants(data, SimpleNamespace(units=[item]), [source(1, item.material_text)], "test", lambda *args: None)
    assert result["intermediate"][0]["text"] == item.material_text

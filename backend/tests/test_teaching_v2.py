"""Reversible V2 teaching and reviewed natural narration; no provider calls."""

import json

import pytest
from pydantic import ValidationError
from test_a3 import Polly, request_data
from test_a3_model_compat import Converse, review_data, section
from test_a3_v2 import candidate, review, unit

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput, StoredLessonInput
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.lesson_speech import LessonSpeech
from upraise_ai.services.teaching_v2 import TeachingModelsV2, TeachingSpeechV2
from upraise_ai.services.teaching_v2.contracts import (
    TeachingMaterialRejected,
    validate_spoken,
    validate_written,
)

MATERIAL_PASS = {"findings": [], "warnings": [], "natural": True}
PASS = {"correct": True, "complete": True, "in_language": True, "natural": True, "issues": []}
FORMULA = r"The expected edge count is \(p\binom{n}{2}\). [p. 1]"
SPOKEN = "Each possible edge appears with probability p. Multiply n choose two by p to get the expected edge count."


@pytest.fixture(autouse=True)
def isolate_author_and_material_review(monkeypatch):
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_findings", lambda models, payload, findings: (findings, []))
    # Source selection has its own contract/negative tests and real-worker tests.
    monkeypatch.setattr(TeachingModelsV2, "select_source", lambda self, section: (section, []))
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_entailment", lambda *args: [])


def speech(replies, tmp_path=None):
    client = Converse([json.dumps(value) for value in replies])
    models = TeachingModelsV2(Settings(), client, diagnostic_dir=tmp_path / "models" if tmp_path else None)
    polly = Polly()
    return TeachingSpeechV2(models.settings, models, client=polly), client, polly



def authored(text="An explanation [p. 1]."):
    return json.dumps({"paragraphs": [{"text": text, "pages": [1]}]})


def test_same_level_canonical_reuse_keeps_exact_math_ids_and_teacher_warnings():
    from upraise_ai.schemas.lessons import ReviewIssue, ReviewReport
    item = unit()
    item.material_text = r"The rate is \(\nu=a/b\), not \(a b\). [p. 1]"
    item.canonical_review = ReviewReport(approved=False, summary="Source review.",
                                       issues=[ReviewIssue(category="source", message="Missing units.", pages=[1])])
    texts, reports = TeachingModelsV2.reuse_canonical_units([item])
    assert texts.units[0].content_unit_id == item.content_unit_id
    assert texts.units[0].text == item.material_text
    assert reports[str(item.content_unit_id)] == item.canonical_review
    assert not hasattr(LessonModels, "reuse_canonical_units")


def test_same_level_reuse_requires_a_recorded_canonical_review():
    with pytest.raises(IngestionError, match="TEACHING_V2_CANONICAL_REVIEW_MISSING"):
        TeachingModelsV2.reuse_canonical_units([unit()])


def material_error(message, *, category="changed_fact", candidate_quote="explanation", required_quote="source"):
    return {**MATERIAL_PASS, "findings": [{"category": category, "candidate_index": 0,
        "candidate_quote": candidate_quote, "required_index": 0, "required_quote": required_quote, "message": message}]}


@pytest.mark.parametrize("schema", [LessonInput, AlignedLessonInput, StoredLessonInput])
def test_teaching_v2_is_explicit_opt_in(schema):
    assert schema.model_validate(request_data()).teaching_version == "v1"
    assert schema.model_validate(request_data(teaching_version="v2")).teaching_version == "v2"
    for value in ["v4", 2, None, "V2"]:
        with pytest.raises(ValidationError):
            schema.model_validate(request_data(teaching_version=value))


def test_v1_generation_stays_on_original_prompts_without_extra_calls():
    client = Converse(["An explanation [p. 1].", json.dumps(review_data())])
    models = LessonModels(Settings(), client)
    models.generate_section(section(), "en", learner_level="intermediate")
    assert len(client.calls) == 2
    assert all("Teaching and narration version 2" not in block["text"] for call in client.calls for block in call["system"])
    assert not hasattr(models, "teaching_versions")


def test_teaching_v2_generation_has_own_concept_author_and_independent_grounded_review():
    client = Converse([authored(), json.dumps(MATERIAL_PASS)])
    draft, _, _ = TeachingModelsV2(Settings(), client).generate_section(section(), "en", learner_level="intermediate")
    assert draft.paragraphs[0].text == "An explanation [p. 1]."
    assert len(client.calls) == 2
    assert "Explain the actual subject directly" in client.calls[0]["system"][0]["text"]
    assert "incidental metadata" in client.calls[0]["system"][0]["text"]
    assert client.calls[0]["modelId"] == Settings().lesson_generation_model_id
    assert client.calls[1]["modelId"] == Settings().lesson_review_model_id


def test_teaching_v2_checks_final_aligned_text_against_only_its_required_units():
    item = unit()
    client = Converse([candidate(item), review(item), json.dumps(MATERIAL_PASS)])
    models = TeachingModelsV2(Settings(), client)
    models.adapt_units({"chunks": [{"text": item.material_text, "page_numbers": [1]}]}, [item], "en",
        learner_level="intermediate", profiles=models.settings.lesson_level_profiles, target_words=30, max_words=50)
    payload, _ = json.JSONDecoder().raw_decode(client.calls[-1]["messages"][0]["content"][0]["text"])
    assert payload["required_material"] == [item.material_text]
    assert models.teaching_versions["teaching_version"] == "v2"


def test_teaching_v2_material_correction_uses_findings_and_rechecks_current_candidate():
    src = section()
    src["chunks"][0]["text"] = "There are 12 nodes."
    rejected = material_error("Restore the source value.", candidate_quote="11", required_quote="12")
    client = Converse([authored("There are 11 nodes. [p. 1]"), json.dumps(rejected),
                       authored("There are 12 nodes. [p. 1]"), json.dumps(MATERIAL_PASS)])
    models = TeachingModelsV2(Settings(), client)
    draft, _, revisions = models.generate_section(src, "en")
    assert "12 nodes" in draft.paragraphs[0].text and revisions == 1
    repair, _ = json.JSONDecoder().raw_decode(client.calls[2]["messages"][0]["content"][0]["text"])
    assert repair["review"]["issues"] == ["Restore the source value."]
    checked, _ = json.JSONDecoder().raw_decode(client.calls[3]["messages"][0]["content"][0]["text"])
    assert checked["candidates"] == [draft.paragraphs[0].text]


def test_teaching_v2_strict_material_check_keeps_rejection_when_recovery_is_not_enabled(tmp_path):
    from upraise_ai.schemas.lessons import SectionDraft
    rejected = material_error("The formula changed meaning.")
    client = Converse([json.dumps(rejected), authored(), json.dumps(rejected), authored(), json.dumps(rejected)])
    models = TeachingModelsV2(Settings(), client, diagnostic_dir=tmp_path / "models")
    with pytest.raises(TeachingMaterialRejected) as failure:
        models.checked_material({**section(), "source_language": "en"},
                                SectionDraft.model_validate_json(authored()), "en", "intermediate")
    assert not failure.value.retryable and len(client.calls) == 5
    assert failure.value.details["correction_rounds"] == 2
    assert failure.value.details["review"]["issues"] == ["The formula changed meaning."]
    retained = [json.loads(p.read_text()) for p in (tmp_path / "teaching-v2-material").glob("*.json")]
    assert retained and all(not item["passed"] and item["evidence_checks"] == 1 for item in retained)


def test_teaching_v2_exhausted_new_section_discards_rejected_claims_and_flags_exact_source():
    src = {**section(), "source_language": "en"}
    rejected = material_error("The formula changed meaning.")
    client = Converse([authored(), json.dumps(rejected), authored(), json.dumps(rejected), authored(), json.dumps(rejected)])
    draft, report, revisions = TeachingModelsV2(Settings(), client).generate_section(src, "en")
    assert len(client.calls) == 6 and revisions == 1
    assert [p.text for p in draft.paragraphs] == [chunk["text"] for chunk in src["chunks"]]
    assert not report.approved and "Source-excerpt DRAFT" in report.summary
    assert any("Teacher editing required" in issue.message for issue in report.issues)
    assert not any("The formula changed meaning." in issue.message for issue in report.issues)


def test_teaching_v2_exhausted_wrong_language_still_fails_instead_of_copying_source():
    src = {**section(), "source_language": "en"}
    rejected = material_error("Wrong language.", category="wrong_language")
    client = Converse([authored(), json.dumps(rejected), authored(), json.dumps(rejected), authored(), json.dumps(rejected)])
    with pytest.raises(TeachingMaterialRejected):
        TeachingModelsV2(Settings(), client).generate_section(src, "en")


def test_teaching_v2_corrects_aligned_units_without_changing_ids_or_required_scope():
    item = unit()
    wrong = item.material_text.replace("12", "11")
    corrected = {"units": [{"content_unit_id": str(item.content_unit_id), "text": item.material_text}]}
    client = Converse([candidate(item, wrong), review(item), json.dumps(material_error(
        "Keep the table values.", candidate_quote="11", required_quote="12")), json.dumps(corrected), json.dumps(MATERIAL_PASS)])
    models = TeachingModelsV2(Settings(), client)
    texts, _ = models.adapt_units({"chunks": [{"text": item.material_text, "page_numbers": [1]}]}, [item], "en",
        learner_level="intermediate", profiles=models.settings.lesson_level_profiles, target_words=30, max_words=50)
    assert texts.units[0].content_unit_id == item.content_unit_id and texts.units[0].text == item.material_text
    payload, _ = json.JSONDecoder().raw_decode(client.calls[3]["messages"][0]["content"][0]["text"])
    assert payload["required_material"] == [item.material_text] and payload["max_words_per_unit"] == 50


def test_teaching_v2_exhausted_adaptation_retains_reviewed_material_and_flags_requested_level():
    from upraise_ai.schemas.lessons import ReviewReport
    item = unit()
    item.canonical_review = ReviewReport(approved=True, summary="Canonical material checked.")
    wrong = item.material_text.replace("12", "11")
    rejected = json.dumps(material_error("Keep the source value.", candidate_quote="11", required_quote="12"))
    client = Converse([candidate(item, wrong), review(item), rejected,
                       candidate(item, wrong), rejected, candidate(item, wrong), rejected])
    texts, reports = TeachingModelsV2(Settings(), client).adapt_units(
        {"chunks": [{"text": item.material_text, "page_numbers": [1]}]}, [item], "en",
        learner_level="beginner", profiles=Settings().lesson_level_profiles, target_words=30, max_words=50)
    assert texts.units[0].content_unit_id == item.content_unit_id and texts.units[0].text == item.material_text
    report = reports[str(item.content_unit_id)]
    assert not report.approved and "requested level" in report.summary
    assert any("difficulty adaptation was discarded" in issue.message for issue in report.issues)
    assert len(client.calls) == 7


def test_teaching_v2_review_and_correction_exclude_extraction_repair_suggestions():
    src = section()
    src["chunks"][0].update(quality={"status": "NEEDS_REVIEW", "issues": ["Invented replacement"]}, embedding_text="Private duplicate")
    client = Converse([authored(), json.dumps(material_error("Keep the source-supported fact.")), authored(), json.dumps(MATERIAL_PASS)])
    TeachingModelsV2(Settings(), client).generate_section(src, "en")
    for call in client.calls:
        payload, _ = json.JSONDecoder().raw_decode(call["messages"][0]["content"][0]["text"])
        assert payload["source_context"][0]["text"] == src["chunks"][0]["text"]
        assert "Invented replacement" not in json.dumps(payload) and "Private duplicate" not in json.dumps(payload)


def test_teaching_v2_material_review_receives_word_block_citation_rules():
    client = Converse([json.dumps(MATERIAL_PASS)])
    TeachingModelsV2(Settings(), client).check_written({**section(), "source_format": "docx"}, ["A fact [p. 1]."], "en", "intermediate")
    assert "internal WORD SOURCE BLOCK" in client.calls[0]["system"][0]["text"]


def test_teaching_v2_material_correction_cannot_change_unit_identity():
    item = unit()
    wrong = item.material_text.replace("12", "11")
    client = Converse([candidate(item, wrong), review(item), json.dumps(material_error("Fix coverage.", candidate_quote="11", required_quote="12")),
        json.dumps({"units": [{"content_unit_id": "00000000-0000-0000-0000-000000000001", "text": item.material_text}]})])
    with pytest.raises(IngestionError, match="LESSON_ALIGNMENT_IDS_INVALID"):
        TeachingModelsV2(Settings(), client).adapt_units({"chunks": [{"text": item.material_text, "page_numbers": [1]}]}, [item], "en",
            learner_level="intermediate", profiles=Settings().lesson_level_profiles, target_words=30, max_words=50)


def test_teaching_v2_material_correction_cannot_invent_source_numbers():
    client = Converse([authored(), json.dumps(material_error("Fix citation.")),
                       authored("A fact [p. 999]."), authored("A fact [p. 999].")])
    with pytest.raises(TeachingMaterialRejected):
        TeachingModelsV2(Settings(), client).generate_section(section(), "en")


def test_teaching_v2_repairs_outside_citations_before_publication():
    client = Converse([authored("A fact [p. 999]."), authored(), json.dumps(MATERIAL_PASS)])
    draft, _, revisions = TeachingModelsV2(Settings(), client).generate_section(section(), "en")
    assert "999" not in draft.paragraphs[0].text and revisions == 1


def test_teaching_v2_material_failure_bounds_findings_and_supplies_boolean_reasons():
    from upraise_ai.services.teaching_v2.contracts import Decision
    decision = Decision.model_validate({**PASS, "complete": False, "issues": []})
    assert "missing" in TeachingMaterialRejected(decision, section=section(), learner_level="intermediate", correction_rounds=2).details["reason"]
    decision.issues = ["x" * 4000] * 30
    details = TeachingMaterialRejected(decision, section=section(), learner_level="intermediate", correction_rounds=2).details
    assert len(details["review"]["issues"]) == 5
    assert max(map(len, details["review"]["issues"])) == 600 and len(details["reason"]) <= 1200


def test_teaching_v2_narrates_separately_without_symbolic_converter_and_same_ssml_for_audio():
    voice, client, polly = speech([{"text": SPOKEN}, PASS])
    voice.converter = lambda *args: pytest.fail("V2 spoken explanations must bypass the symbolic converter")
    full, parts, issues = voice.prepare(FORMULA, "en")
    assert SPOKEN in full and "column matrix" not in full and not issues
    assert not polly.calls  # No TTS before the independent meaning check.
    assert client.calls[-1]["modelId"] == Settings().lesson_review_model_id
    audio, _, _, _ = voice.synthesize(parts, "Joanna", "en-US")
    assert audio.startswith(b"RIFF")
    assert polly.calls and all(call["Text"] in parts for call in polly.calls)
    assert FORMULA in client.calls[0]["messages"][0]["content"][0]["text"].replace("\\\\", "\\")


def test_teaching_v2_corrects_wrong_math_before_producing_audio():
    voice, client, polly = speech([{"text": "The number of edges is a two by one matrix."},
        {**PASS, "correct": False, "issues": ["A binomial coefficient is not a matrix."]}, {"text": SPOKEN}, PASS])
    full, _, _ = voice.prepare(FORMULA, "en")
    assert SPOKEN in full and len(client.calls) == 4 and not polly.calls
    assert "A binomial coefficient is not a matrix" in client.calls[2]["messages"][0]["content"][0]["text"]


def test_teaching_v2_narration_language_is_independent_of_previous_source_language():
    voice, client, _ = speech([{"text": "Il valore osservato è tre."}, PASS])
    voice.models.transport.language = "en"
    assert "Il valore osservato" in voice.prepare("Il valore è tre.", "it")[0]
    for call in client.calls:
        payload = json.loads(call["messages"][0]["content"][0]["text"].split("\nReturn one JSON object.")[0])
        assert payload["language"] == "it"
        assert all("Retain the exact source notation" not in block["text"] for block in call["system"])


def test_teaching_v2_exhaustion_never_silently_uses_v1_or_calls_polly():
    voice, client, polly = speech([value for _ in range(3) for value in
        ({"text": "Wrong mathematical meaning."}, {**PASS, "complete": False, "issues": ["An assumption is missing."]})])
    with pytest.raises(IngestionError, match="TEACHING_V2_NARRATION_REJECTED"):
        voice.prepare(FORMULA, "en")
    assert len(client.calls) == 6 and not polly.calls and voice._prepared is None


def test_teaching_v2_formatting_debris_is_corrected_before_review():
    voice, client, _ = speech([{"text": "The value is open paren n close paren."}, {"text": SPOKEN}, PASS])
    assert SPOKEN in voice.prepare(FORMULA, "en")[0]
    assert len(client.calls) == 3


def test_teaching_v2_cached_verified_script_survives_tts_retries_and_binds_language_text(tmp_path):
    voice, client, _ = speech([{"text": SPOKEN}, PASS], tmp_path)
    first = voice.prepare(FORMULA, "en")
    retry, retry_client, _ = speech([], tmp_path)
    assert retry.prepare(FORMULA, "en") == first and not retry_client.calls
    assert len(list((tmp_path / "teaching-v2-narration").glob("*.json"))) == 1
    other, _, polly = speech([], tmp_path)
    with pytest.raises(IngestionError):
        other.prepare(FORMULA + " Different assumption.", "en")
    assert not polly.calls and len(client.calls) == 2


@pytest.mark.parametrize("text", [r"Broken\ntext", r"Erd\u2013os", r"\\(p=1\\)", "text\x08"])
def test_teaching_v2_rejects_escaped_markup(text):
    with pytest.raises(IngestionError, match="TEACHING_V2_TEXT_INVALID"):
        validate_written(text)


def test_teaching_v2_preserves_genuine_latex_commands_and_real_newlines():
    validate_written("A definition:\n" + r"\(\nu = 2\) and \(\underbrace{x}_{y}\)")
    validate_written(r"The code uses `\n` for a newline and `\u2013` for a Unicode escape.")
    validate_spoken("Assuming the null hypothesis is true, count outcomes at least as large as the observation.")


def test_v1_math_and_spoken_text_are_independent_of_v2():
    legacy = LessonSpeech(Settings(), object(), client=Polly(), converter=lambda *args: "unchanged formula speech")
    full, _, _ = legacy.prepare(FORMULA, "en")
    assert "unchanged formula speech" in full

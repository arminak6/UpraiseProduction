"""Cross-language lessons preserve notation and cannot archive an unverified translation."""

import json
import re

import pytest
from botocore.exceptions import ClientError

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import SectionDraft
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.lesson_pipeline import plan_sections
from upraise_ai.services.lesson_translation import LessonTranslator, needs_translation


def draft():
    return SectionDraft(paragraphs=[
        {"text": r"The probability of an edge is given by \(p=\frac{1}{2}\). See [p. 1].", "pages": [1]},
        {"text": r"An example uses $$n=2$$ and `graph`. See [pp. 2-3].", "pages": [2, 3]},
    ])


def review_check(field=None, *, paragraph_id=0, quote="La probabilità"):
    check = {"target_language": True, "faithful": True, "level_preserved": True, "issues": []}
    if field:
        check[field] = False
        check["issues"] = [{"category": field, "paragraph_id": paragraph_id, "quote": quote,
                            "reason": "The quoted text needs correction.",
                            "instruction": "Correct this passage while preserving the source meaning."}]
    return check


class TranslationClient:
    def __init__(self, transform=None, check=None, *, checks=None):
        self.calls = []
        self.transform = transform
        self.check = check if check is not None else review_check()
        self.checks = list(checks) if checks is not None else None

    def converse(self, **request):
        self.calls.append(request)
        payload = json.loads(request["messages"][0]["content"][0]["text"])
        if "paragraph_pairs" in payload:
            result = self.check if self.checks is None else self.checks.pop(0)
            if callable(result):
                result = result(payload)
        else:
            result = {"paragraphs": [{"id": p["id"], "text": p["text"].replace(
                "The probability of an edge is given by", "La probabilità di un arco è data da").replace(
                "An example uses", "Un esempio usa").replace(" and ", " e ").replace("See", "Vedi").replace(
                "A supported teaching sentence.", "Una frase didattica supportata.")}
                for p in payload["paragraphs"]]}
            if self.transform:
                result = self.transform(result, payload)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, ensure_ascii=False)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": text}]}}}


@pytest.mark.parametrize("source,target", [("en", "EN"), ("en", "en-US"), ("IT-it", "it")])
def test_same_language_has_no_translation_calls(source, target):
    client = TranslationClient()
    original = draft()
    assert not needs_translation(source, target)
    assert LessonTranslator(Settings(), client).translate(original, source, target, "beginner") is original
    assert not client.calls


@pytest.mark.parametrize("label", [None, "", " "])
def test_source_label_is_required_instead_of_assuming_english(label):
    with pytest.raises(IngestionError, match="LESSON_SOURCE_LANGUAGE_MISSING"):
        plan_sections([({"metadata": {"language": label}}, [])], 5)


def test_model_adapter_does_not_apply_source_language_or_generator_role_to_translation():
    client = TranslationClient()
    models = LessonModels(Settings(), client)
    models.transport.language = "fr"
    models.transport.teaching_prompt = "Generate a new French lesson."
    translated = models.translate_section(draft(), "en", "it", learner_level="advanced")
    assert "La probabilità" in translated.paragraphs[0].text
    assert all(len(call["system"]) == 1 for call in client.calls)
    assert all("French lesson" not in call["system"][0]["text"] for call in client.calls)


def test_translation_preserves_exact_math_citations_code_pages_and_level():
    original, client = draft(), TranslationClient()
    translated = LessonTranslator(Settings(), client).translate(original, "en", "it", "advanced")
    assert translated.paragraphs[0].text == r"La probabilità di un arco è data da \(p=\frac{1}{2}\). Vedi [p. 1]."
    assert translated.paragraphs[1].text == r"Un esempio usa $$n=2$$ e `graph`. Vedi [pp. 2-3]."
    assert [p.pages for p in translated.paragraphs] == [[1], [2, 3]]
    assert original == draft()  # Generation checkpoint remains in the source language.
    assert [c["modelId"] for c in client.calls] == [Settings().lesson_generation_model_id, Settings().lesson_review_model_id]
    assert all("Italian (it)" in c["system"][0]["text"] and "advanced" in c["system"][0]["text"] for c in client.calls)
    translation_input = client.calls[0]["messages"][0]["content"][0]["text"]
    assert "frac" not in translation_input and "[p. 1]" not in translation_input
    assert all("outputConfig" in c for c in client.calls)
    for call in client.calls:
        schema = call["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
        assert all(keyword not in schema for keyword in ("minimum", "maximum", "minLength", "maxLength", "maxItems"))


@pytest.mark.parametrize("wrapper", ["extra_brace", "fence", "preface"])
def test_real_provider_wrapper_damage_recovers_without_changing_content(wrapper):
    def wrap(result, payload):
        text = json.dumps(result, ensure_ascii=False)
        if wrapper == "extra_brace":
            return "{\n " + text  # Reproduced live Bedrock response shape.
        if wrapper == "fence":
            return "```json\n" + text + "\n```"
        return "JSON response:\n" + text

    original = draft()
    client = TranslationClient(wrap)
    translated = LessonTranslator(Settings(), client).translate(original, "en", "it", "intermediate")
    expected = LessonTranslator(Settings(), TranslationClient()).translate(original, "en", "it", "intermediate")
    assert translated == expected and original == draft()
    assert len(client.calls) == 2


def test_damaged_reviewer_wrapper_still_checks_all_review_fields():
    raw = "{\n " + json.dumps(review_check("faithful"))
    client = TranslationClient(check=raw)
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_REJECTED"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")


def test_ambiguous_json_payloads_are_rejected_before_verification():
    client = TranslationClient(lambda result, payload: json.dumps(result) + "\n" + json.dumps(result))
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INVALID"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert len(client.calls) == 1


@pytest.mark.parametrize("defect", ["missing_paragraph", "reorder", "duplicate_id", "drop_token", "duplicate_token",
                                   "new_citation", "new_formula", "control", "malformed_json"])
def test_structural_damage_is_rejected_before_verification(defect, caplog):
    def corrupt(result, payload):
        paragraphs = result["paragraphs"]
        if defect == "missing_paragraph":
            paragraphs.pop()
        elif defect == "reorder":
            paragraphs.reverse()
        elif defect == "duplicate_id":
            paragraphs[1]["id"] = 0
        elif defect in {"drop_token", "duplicate_token"}:
            token = paragraphs[0]["text"].split("__UPRAISE_KEEP_")[1].split("__")[0]
            token = "__UPRAISE_KEEP_" + token + "__"
            paragraphs[0]["text"] = paragraphs[0]["text"].replace(token, "" if defect == "drop_token" else token*2)
        elif defect == "new_citation":
            paragraphs[0]["text"] += " [p. 999]"
        elif defect == "new_formula":
            paragraphs[0]["text"] += r" \(x=999\)"
        elif defect == "control":
            paragraphs[0]["text"] += "\x08"
        else:
            return '{"paragraphs":'
        return result

    client = TranslationClient(corrupt)
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INVALID") as failure:
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert failure.value.retryable and len(client.calls) == 1
    reason = {"missing_paragraph": "PARAGRAPH_IDS", "reorder": "PARAGRAPH_IDS", "duplicate_id": "PARAGRAPH_IDS",
              "drop_token": "PLACEHOLDER_SEQUENCE", "duplicate_token": "PLACEHOLDER_SEQUENCE",
              "new_citation": "NOTATION_CHANGED", "new_formula": "NOTATION_CHANGED",
              "control": "OUTPUT_CONTROL_CHAR", "malformed_json": "JSON_SCHEMA"}[defect]
    assert "reason=" + reason in caplog.text
    assert "La probabilità" not in caplog.text and "__UPRAISE_KEEP_" not in caplog.text


def test_echoed_english_is_rejected_even_if_verifier_would_approve():
    client = TranslationClient(lambda result, payload: {"paragraphs": payload["paragraphs"]})
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_REJECTED"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "beginner")
    assert len(client.calls) == 1


@pytest.mark.parametrize("field", ["target_language", "faithful", "level_preserved"])
def test_independent_verifier_can_reject_language_meaning_or_difficulty(field):
    client = TranslationClient(check=review_check(field))
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_REJECTED"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "beginner")
    assert len(client.calls) == 4


def test_reviewer_judges_only_translated_prose_and_compares_explicit_paragraph_pairs():
    client = TranslationClient()
    original = draft()
    translated = LessonTranslator(Settings(), client).translate(original, "en", "it", "intermediate")
    review = client.calls[1]
    payload = json.loads(review["messages"][0]["content"][0]["text"])
    pairs = payload["paragraph_pairs"]
    assert [pair["id"] for pair in pairs] == [0, 1]
    assert pairs[0]["source_text"].startswith("The probability of an edge is given by")
    assert pairs[0]["translated_text"].startswith("La probabilità di un arco è data da")
    for pair in pairs:
        source_tokens = re.findall(r"__UPRAISE_KEEP_[a-f0-9]{16}_\d+__", pair["source_text"])
        target_tokens = re.findall(r"__UPRAISE_KEEP_[a-f0-9]{16}_\d+__", pair["translated_text"])
        assert source_tokens and source_tokens == target_tokens
        for text in (pair["source_text"], pair["translated_text"]):
            assert "frac" not in text and "[p." not in text and "[pp." not in text
            assert "`graph`" not in text and "$$" not in text
    assert translated.paragraphs[0].text.endswith(r"\(p=\frac{1}{2}\). Vedi [p. 1].")
    assert "original" not in payload and "translation" not in payload
    policy = review["system"][0]["text"]
    assert "translated_text" in policy and "ONLY" in policy
    assert "source_text" in policy
    assert "shared" in policy.lower() and "technical" in policy.lower()


def test_actionable_review_corrects_once_and_independently_rechecks():
    first_review = review_check("faithful", quote="La certezza")
    first_review["issues"][0]["reason"] = "Certainty changes the source concept of probability."
    first_review["issues"][0]["instruction"] = "Use probabilità instead of certezza."

    def mistranslate_then_correct(result, payload):
        if "previous_translation" not in payload:
            result["paragraphs"][0]["text"] = result["paragraphs"][0]["text"].replace(
                "La probabilità", "La certezza")
        else:
            assert "La certezza" in payload["previous_translation"]["paragraphs"][0]["text"]
            assert payload["reviewer_feedback"] == first_review["issues"]
        return result

    client = TranslationClient(mistranslate_then_correct, checks=[first_review, review_check()])
    translated = LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert "La probabilità" in translated.paragraphs[0].text
    assert len(client.calls) == 4
    correction = json.loads(client.calls[2]["messages"][0]["content"][0]["text"])
    assert correction["reviewer_feedback"] == first_review["issues"]
    assert correction["previous_translation"]["paragraphs"][0]["id"] == 0
    assert "La certezza" in correction["previous_translation"]["paragraphs"][0]["text"]
    assert "frac" not in correction["previous_translation"]["paragraphs"][0]["text"]
    assert "[p. 1]" not in correction["previous_translation"]["paragraphs"][0]["text"]
    assert "__UPRAISE_KEEP_" in correction["previous_translation"]["paragraphs"][0]["text"]
    assert all("paragraph_pairs" in json.loads(client.calls[index]["messages"][0]["content"][0]["text"])
               for index in (1, 3))
    first_pair = json.loads(client.calls[1]["messages"][0]["content"][0]["text"])["paragraph_pairs"][0]
    corrected_pair = json.loads(client.calls[3]["messages"][0]["content"][0]["text"])["paragraph_pairs"][0]
    assert "La certezza" in first_pair["translated_text"]
    assert "La probabilità" in corrected_pair["translated_text"]
    assert "La certezza" not in translated.paragraphs[0].text
    assert first_pair["source_text"] == corrected_pair["source_text"]
    assert first_pair["source_text"].startswith("The probability of an edge is given by")


def test_english_prose_inside_immutable_formula_is_not_sent_for_language_review():
    formula = r"\(\text{expected number of edges}=np\)"
    original = SectionDraft(paragraphs=[
        {"text": "The probability of an edge is given by " + formula + ". See [p. 1].", "pages": [1]}])
    client = TranslationClient()
    translated = LessonTranslator(Settings(), client).translate(original, "en", "it", "intermediate")
    assert formula in translated.paragraphs[0].text
    review_payload = json.loads(client.calls[1]["messages"][0]["content"][0]["text"])
    assert "expected number of edges" not in json.dumps(review_payload)
    assert "__UPRAISE_KEEP_" in review_payload["paragraph_pairs"][0]["translated_text"]


def test_issue_quoting_only_immutable_formula_is_not_actionable_review_evidence(caplog):
    client = TranslationClient(check=review_check("target_language", quote=r"\(p=\frac{1}{2}\)"))
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INVALID"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert len(client.calls) == 2 and "reason=REVIEW_EVIDENCE" in caplog.text


def test_token_only_issue_is_restored_then_rejected_as_immutable_evidence(caplog):
    def quote_immutable_token(payload):
        text = payload["paragraph_pairs"][0]["translated_text"]
        token = re.search(r"__UPRAISE_KEEP_[a-f0-9]{16}_\d+__", text).group()
        return review_check("faithful", quote=token)

    client = TranslationClient(check=quote_immutable_token)
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INVALID"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert len(client.calls) == 2 and "reason=REVIEW_EVIDENCE" in caplog.text
    response = json.loads(client.calls[1]["messages"][0]["content"][0]["text"])
    assert "__UPRAISE_KEEP_" in response["paragraph_pairs"][0]["translated_text"]


def test_prose_and_token_issue_quote_is_restored_for_feedback_and_correction():
    def quote_wrong_prose_and_token(payload):
        text = payload["paragraph_pairs"][0]["translated_text"]
        quote = text.split(". Vedi", 1)[0]
        assert "La certezza" in quote and "__UPRAISE_KEEP_" in quote
        check = review_check("faithful", quote=quote)
        check["issues"][0]["instruction"] = "Replace certezza with probabilità."
        return check

    def mistranslate_then_correct(result, payload):
        if "previous_translation" not in payload:
            result["paragraphs"][0]["text"] = result["paragraphs"][0]["text"].replace(
                "La probabilità", "La certezza")
        else:
            quote = payload["reviewer_feedback"][0]["quote"]
            assert quote == r"La certezza di un arco è data da \(p=\frac{1}{2}\)"
            assert "__UPRAISE_KEEP_" not in quote
            assert "__UPRAISE_KEEP_" in payload["previous_translation"]["paragraphs"][0]["text"]
        return result

    client = TranslationClient(mistranslate_then_correct, checks=[quote_wrong_prose_and_token, review_check()])
    translated = LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert len(client.calls) == 4
    assert translated.paragraphs[0].text == r"La probabilità di un arco è data da \(p=\frac{1}{2}\). Vedi [p. 1]."


@pytest.mark.parametrize("defect", ["missing_issues", "empty_issues", "unknown_paragraph", "unknown_quote",
                                   "category_mismatch", "true_flag_issue", "string_id", "empty_reason",
                                   "empty_instruction"])
def test_invalid_review_evidence_never_triggers_correction(defect, caplog):
    check = review_check("faithful")
    issue = check["issues"][0]
    if defect == "missing_issues":
        del check["issues"]
    elif defect == "empty_issues":
        check["issues"] = []
    elif defect == "unknown_paragraph":
        issue["paragraph_id"] = 99
    elif defect == "unknown_quote":
        issue["quote"] = "This sentence is absent from the translated paragraph."
    elif defect == "category_mismatch":
        issue["category"] = "target_language"
    elif defect == "true_flag_issue":
        check["faithful"] = True
    elif defect == "string_id":
        issue["paragraph_id"] = "0"
    elif defect == "empty_reason":
        issue["reason"] = ""
    else:
        issue["instruction"] = ""
    client = TranslationClient(check=check)
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INVALID"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert len(client.calls) == 2
    reason = "JSON_SCHEMA" if defect in {"missing_issues", "string_id", "empty_reason", "empty_instruction"} else "REVIEW_EVIDENCE"
    assert "reason=" + reason in caplog.text


def test_each_false_review_flag_needs_its_own_actionable_issue(caplog):
    check = review_check("faithful")
    check["target_language"] = False
    client = TranslationClient(check=check)
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INVALID"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "beginner")
    assert len(client.calls) == 2 and "reason=REVIEW_EVIDENCE" in caplog.text


@pytest.mark.parametrize("defect", ["drop_token", "new_citation", "control", "reorder"])
def test_correction_is_subject_to_the_same_structural_and_notation_checks(defect):
    generations = 0

    def corrupt_correction(result, payload):
        nonlocal generations
        generations += 1
        if generations == 1:
            return result
        assert "previous_translation" in payload and "reviewer_feedback" in payload
        if defect == "drop_token":
            text = result["paragraphs"][0]["text"]
            token = "__UPRAISE_KEEP_" + text.split("__UPRAISE_KEEP_")[1].split("__")[0] + "__"
            result["paragraphs"][0]["text"] = text.replace(token, "")
        elif defect == "new_citation":
            result["paragraphs"][0]["text"] += " [p. 999]"
        elif defect == "control":
            result["paragraphs"][0]["text"] += "\x08"
        else:
            result["paragraphs"].reverse()
        return result

    client = TranslationClient(corrupt_correction, check=review_check("faithful"))
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INVALID"):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")
    assert len(client.calls) == 3


@pytest.mark.parametrize("capture", [False, True])
def test_rejection_diagnostics_preserve_candidate_and_review_only_when_opted_in(capture, tmp_path, caplog):
    settings = Settings(environment="development", local_test_mode=True, lesson_capture_model_outputs=capture)
    folder = tmp_path / "diagnostics"
    client = TranslationClient(check=review_check("faithful"))
    models = LessonModels(settings, client, diagnostic_dir=folder)
    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_REJECTED"):
        models.translate_section(draft(), "en", "it", learner_level="intermediate")
    assert len(client.calls) == 4
    assert "La probabilità" not in caplog.text and "__UPRAISE_KEEP_" not in caplog.text
    files = list(folder.glob("*.json"))
    if not capture:
        assert not files and not folder.exists()
        return
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["phase"] == "translation"
    replies = json.loads(record["raw_output"])
    assert [reply["phase"] for reply in replies] == ["translate", "review", "translate", "review"]
    assert "La probabilità" in json.loads(replies[0]["raw_output"])["paragraphs"][0]["text"]
    assert json.loads(replies[1]["raw_output"])["issues"] == client.check["issues"]


def test_provider_errors_are_not_disguised_as_translation_output_errors():
    error = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")
    client = TranslationClient(lambda *args: error)
    with pytest.raises(ClientError):
        LessonTranslator(Settings(), client).translate(draft(), "en", "it", "intermediate")


def test_truncated_translation_is_not_accepted():
    class Truncated(TranslationClient):
        def converse(self, **request):
            response = super().converse(**request)
            response["stopReason"] = "max_tokens"
            return response

    with pytest.raises(IngestionError, match="LESSON_TRANSLATION_INCOMPLETE"):
        LessonTranslator(Settings(), Truncated()).translate(draft(), "en", "it", "intermediate")

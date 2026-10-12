"""Bounded A3 v2 JSON recovery with invented material and no cloud calls."""

import json
from uuid import UUID

import pytest
from test_a3_model_compat import Converse

from upraise_ai.core.config import Settings
from upraise_ai.schemas.aligned_lessons import AlignedReview, AlignedTexts, ContentUnit
from upraise_ai.services.aligned_models import adapt_units
from upraise_ai.services.aligned_responses import ResponseFormatError, parse_response
from upraise_ai.services.lesson_models import LessonModels

UNIT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def texts_payload(text="A node has three neighbours. [p. 1]", unit_id=UNIT_ID):
    return {"units": [{"content_unit_id": unit_id, "text": text}]}


def review_payload():
    return {"units": [{"content_unit_id": UNIT_ID, "material_preserved": True,
                       "level_suitable": True, "in_language": True,
                       "issues": [], "revision_instructions": []}]}


def damaged_text_payload(text):
    """A complete wrapper with a model's unescaped text string only."""
    return '{"units":[{"content_unit_id":"' + UNIT_ID + '","text":"' + text + '"}]}'


@pytest.mark.parametrize("schema,payload", [(AlignedTexts, texts_payload()), (AlignedReview, review_payload())])
def test_complete_valid_json_matches_local_schema(schema, payload):
    assert parse_response(json.dumps(payload), schema) == schema.model_validate(payload)


@pytest.mark.parametrize("wrapper", [
    "```json\n{}\n```",
    "Here is the requested response:\n{}",
    "Here is the response:\n```json\n{}\n```\nEnd of response.",
    "{{{}",
])
def test_one_complete_object_recovers_harmless_outer_wrapper(wrapper):
    payload = texts_payload()
    assert parse_response(wrapper.format(json.dumps(payload)), AlignedTexts) == AlignedTexts.model_validate(payload)


def test_multiple_root_payloads_are_ambiguous():
    first, second = texts_payload(), texts_payload("A different explanation. [p. 1]", OTHER_ID)
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(json.dumps(first) + "\n" + json.dumps(second), AlignedTexts)
    assert failure.value.reason == "AMBIGUOUS"


@pytest.mark.parametrize("key", ["units", "content_unit_id", "text"])
def test_duplicate_root_and_unit_fields_are_rejected(key):
    if key == "units":
        raw = '{"units":' + json.dumps(texts_payload()["units"]) + ',"units":' + json.dumps(texts_payload("Different material.", OTHER_ID)["units"]) + '}'
    elif key == "content_unit_id":
        raw = '{"units":[{"content_unit_id":"' + UNIT_ID + '","content_unit_id":"' + OTHER_ID + '","text":"A node."}]}'
    else:
        raw = '{"units":[{"content_unit_id":"' + UNIT_ID + '","text":"A node.","text":"Different material."}]}'
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(raw, AlignedTexts)
    assert failure.value.reason == "AMBIGUOUS"


@pytest.mark.parametrize("payload", [
    {"unexpected": texts_payload()},
    {"units": [{"units": texts_payload()["units"]}]},
])
def test_schema_invalid_outer_root_cannot_select_valid_nested_payload(payload):
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(json.dumps(payload), AlignedTexts)
    assert failure.value.reason == "JSON_SCHEMA"


@pytest.mark.parametrize("raw", [
    json.dumps(texts_payload())[:-1],
    damaged_text_payload("A node has three neighbours")[:-5],
    '{"units":[{"content_unit_id":"' + UNIT_ID + '","text":"An unfinished explanation',
    "Not a JSON response.",
])
def test_truncated_or_missing_structure_is_never_invented(raw):
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(raw, AlignedTexts)
    assert failure.value.reason == "JSON_SYNTAX"


@pytest.mark.parametrize("payload", [
    texts_payload(unit_id="not-a-uuid"),
    {"units": [{"content_unit_id": UNIT_ID}]},
    {"units": [{"content_unit_id": UNIT_ID, "text": "A node.", "answer": "Extra field."}]},
    {"units": texts_payload()["units"], "extra": "Extra root field."},
    {"units": []},
    {"units": texts_payload()["units"] * 9},
    texts_payload(text=""),
    texts_payload(text="a" * 16001),
])
def test_local_text_schema_still_rejects_invalid_fields_and_bounds(payload):
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(json.dumps(payload), AlignedTexts)
    assert failure.value.reason == "JSON_SCHEMA"


@pytest.mark.parametrize("field,value", [
    ("material_preserved", "true"),
    ("level_suitable", 1),
    ("in_language", "false"),
    ("issues", ["Issue"] * 21),
    ("revision_instructions", ["Repair"] * 21),
])
def test_review_does_not_coerce_judgements_or_drop_review_bounds(field, value):
    payload = review_payload()
    payload["units"][0][field] = value
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(json.dumps(payload), AlignedReview)
    assert failure.value.reason == "JSON_SCHEMA"


@pytest.mark.parametrize("change", ["missing_judgement", "extra_field", "too_many_units"])
def test_review_contract_fields_and_unit_bound_remain_strict(change):
    payload = review_payload()
    if change == "missing_judgement":
        del payload["units"][0]["in_language"]
    elif change == "extra_field":
        payload["units"][0]["human_approved"] = True
    else:
        payload["units"] *= 9
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(json.dumps(payload), AlignedReview)
    assert failure.value.reason == "JSON_SCHEMA"


@pytest.mark.parametrize("text", [
    r"Use \(\alpha+\beta\) for this relation. [p. 1]",
    "First line.\nSecond line. [p. 1]",
    r"Use \[\frac{a}{b}+\nu\] for this relation. [p. 1]",
])
def test_only_string_escape_damage_is_recovered(text):
    result = parse_response(damaged_text_payload(text), AlignedTexts)
    assert result.units[0].text == text


@pytest.mark.parametrize("opening,closing", [(r"\(", r"\)"), (r"\[", r"\]"), ("$", "$"), ("$$", "$$")])
def test_json_looking_tex_control_words_remain_tex_inside_math(opening, closing):
    text = "Use " + opening + r"\text{node}=\frac{a}{b}+\nu" + closing + ". [p. 1]"
    result = parse_response(damaged_text_payload(text), AlignedTexts)
    assert result.units[0].text == text
    assert "\t" not in result.units[0].text and "\f" not in result.units[0].text and "\n" not in result.units[0].text


@pytest.mark.parametrize("formula", [r"\text{node}", r"\nu", r"\rho", r"\tau"])
def test_one_unescaped_tex_control_word_cannot_silently_become_json_whitespace(formula):
    text = "Use $" + formula + "$. [p. 1]"
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(damaged_text_payload(text), AlignedTexts)
    assert failure.value.reason == "JSON_MATH_ESCAPE"


def test_correct_json_escaping_quotes_math_and_newlines_is_preserved():
    text = 'The word "node" appears here.\n' + r'Use \(\text{"node"}=\frac{a}{b}+\nu\). [p. 1]'
    result = parse_response(json.dumps(texts_payload(text)), AlignedTexts)
    assert result.units[0].text == text


def test_valid_json_newline_escape_is_not_mistaken_for_tex():
    text = "First line.\nSecond line. [p. 1]"
    raw = json.dumps(texts_payload(text))
    assert r"\n" in raw
    assert parse_response(raw, AlignedTexts).units[0].text == text


@pytest.mark.parametrize("whitespace", ["\n", "\r", "\t"])
def test_valid_json_whitespace_followed_by_letters_inside_math_is_preserved(whitespace):
    text = r"Use \(x" + whitespace + r"next\). [p. 1]"
    assert parse_response(json.dumps(texts_payload(text)), AlignedTexts).units[0].text == text


def test_ambiguity_scan_limit_cannot_accept_a_later_second_payload():
    raw = json.dumps(texts_payload()) + "\n" + ("[ " * 257) + json.dumps(texts_payload("Another explanation.", OTHER_ID))
    with pytest.raises(ResponseFormatError):
        parse_response(raw, AlignedTexts)


def test_unescaped_inner_quotes_are_not_guessed():
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(damaged_text_payload('A "quoted" technical term. [p. 1]'), AlignedTexts)
    assert failure.value.reason == "JSON_SYNTAX"


@pytest.mark.parametrize("control", ["\x00", "\b", "\f", "\x1f"])
def test_decoded_nontext_controls_are_rejected(control):
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(json.dumps(texts_payload("A node " + control + " [p. 1]")), AlignedTexts)
    assert failure.value.reason == "JSON_STRING_CONTROL"


def test_raw_response_limit_is_checked_before_parsing():
    with pytest.raises(ResponseFormatError) as failure:
        parse_response(" " * 200001, AlignedTexts)
    assert failure.value.reason == "OUTPUT_TOO_LARGE"


def test_unpaired_unicode_surrogate_is_rejected():
    with pytest.raises(ResponseFormatError):
        parse_response(json.dumps(texts_payload("A node \ud800 [p. 1]")), AlignedTexts)


def test_native_roles_fallback_recover_math_json_and_keep_unit_identity():
    text = r"A degree counts neighbours. Here \(k=3\) means three neighbours. [p. 1]"
    unit = ContentUnit(content_unit_id=UUID(UNIT_ID), index=0, section_index=0,
                       source_language="en", material_text=text,
                       source={"document_id": OTHER_ID, "document_version_id": "source-v1", "pages": [1],
                               "source_chunk_ids": ["invented-source"], "quality_status": "AUTO_CHECKED"})
    raw = damaged_text_payload(text)
    verdict = review_payload()
    verdict['units'][0].update(material_preserved=False, issues=['Clarify the definition.'],
                             revision_instructions=['Explain the degree more clearly.'])
    review = json.dumps(verdict)
    client = Converse([RuntimeError("Native structured output unavailable"), raw, review,
                       RuntimeError("Native structured output unavailable"), raw])
    models = LessonModels(Settings(), client)
    result, reports = adapt_units(models, {"chunks": [{"text": text, "page_numbers": [1]}]}, [unit], "en",
                                  learner_level="beginner", profiles=models.settings.lesson_level_profiles,
                                  target_words=30, max_words=50)
    assert result.units[0].content_unit_id == unit.content_unit_id
    assert result.units[0].text == text and not reports[UNIT_ID].approved
    assert reports[UNIT_ID].issues[0].message == 'Clarify the definition.'
    assert ["outputConfig" in call for call in client.calls] == [True, False, True, True, False]

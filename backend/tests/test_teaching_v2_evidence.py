"""Immutable excerpt references recover bad quotations without accepting bad facts."""

import json

import pytest
from test_a3_model_compat import Converse
from test_teaching_v2 import material_error

from upraise_ai.core.config import Settings
from upraise_ai.services.teaching_v2 import TeachingModelsV2
from upraise_ai.services.teaching_v2.contracts import TeachingReviewEvidenceInvalid
from upraise_ai.services.teaching_v2.entailment import verify_entailment
from upraise_ai.services.teaching_v2.evidence import indexed_material_review, spans
from upraise_ai.services.teaching_v2.material_review import evidence_problem, review_material


def data():
    return {"required_material": ["The sample value is 7."], "candidates": [r"The sample value is **9**; calculate \(R=a/b\)."],
            "clarifying_context": [], "required_per_candidate": True}


def model(*replies):
    client = Converse([json.dumps(reply) for reply in replies])
    return TeachingModelsV2(Settings(), client), client


def paragraph_reply(**updates):
    return {"supported": False, "reason": "The candidate changes the source value.", **updates}


def indexed_finding(**updates):
    return {"findings": [{"category": "changed_fact", "candidate_index": 0, "candidate_span_index": 0,
                           "required_index": 0, "required_span_index": 0,
                           "message": "The candidate changes seven to nine.", **updates}], "natural": True, "warnings": []}


def test_paragraph_entailment_recovers_reformatted_quotes_but_still_rejects_changed_value():
    models, client = model(paragraph_reply())
    issues = verify_entailment(models, data())
    assert len(issues) == 1 and "changes the source value" in issues[0]
    sent, _ = json.JSONDecoder().raw_decode(client.calls[-1]["messages"][0]["content"][0]["text"])
    assert sent["candidate"] == data()["candidates"][0]
    assert sent["reference"] == data()["required_material"]
    assert len(client.calls) == 1


@pytest.mark.parametrize("updates", [{"candidate_index": -1}, {"candidate_index": 1},
    {"supported": "true"}, {"reason": ""}, {"unsupported_quote": "Invented evidence"}])
def test_invalid_paragraph_check_never_passes(updates):
    models, _ = model(paragraph_reply(**updates), paragraph_reply(**updates))
    with pytest.raises(TeachingReviewEvidenceInvalid) as error:
        verify_entailment(models, data())
    assert error.value.details["stage"] == "teaching_v2_entailment"
    assert "**9**" not in json.dumps(error.value.details)


def test_supported_paragraph_after_copying_failure_can_pass():
    models, _ = model(paragraph_reply(supported=True))
    assert verify_entailment(models, data()) == []


def test_paragraph_recovery_checks_every_unit_using_only_its_own_required_material():
    models, client = model(paragraph_reply(), paragraph_reply(supported=True))
    payload = data()
    payload["candidates"].append("Another unit uses Q.")
    payload["required_material"].append("Define Q in this other unit.")
    issues = verify_entailment(models, payload)
    assert len(issues) == 1 and "paragraph 1" in issues[0]
    for index, call in enumerate(client.calls[-2:]):
        sent, _ = json.JSONDecoder().raw_decode(call["messages"][0]["content"][0]["text"])
        assert sent["candidate"] == payload["candidates"][index]
        assert sent["reference"] == [payload["required_material"][index]]


def test_indexed_material_recovery_uses_original_text_and_keeps_error_blocking(monkeypatch):
    bad = material_error("The value changed.", candidate_quote="value is 9", required_quote="value is 7")
    models, client = model(bad, bad, indexed_finding())
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_findings", lambda m, p, findings: (findings, []))
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_entailment", lambda *args: [])
    decision, diagnostic = review_material(models, data(), "Review source meaning.")
    assert not decision.passed and not decision.correct
    assert diagnostic["evidence_checks"] == 3 and len(client.calls) == 3
    assert diagnostic["evidence"][0]["candidate_quote"] == data()["candidates"][0]
    sent, _ = json.JSONDecoder().raw_decode(client.calls[-1]["messages"][0]["content"][0]["text"])
    assert sent["candidate_spans"][0] == {"candidate_index": 0, "spans": [{"span_index": 0, "text": data()["candidates"][0]}]}
    assert sent["required_spans"][0]["spans"][0]["span_index"] == 0


@pytest.mark.parametrize("updates", [{"candidate_span_index": -1}, {"required_span_index": 1},
    {"required_index": -1}, {"candidate_index": 1}, {"category": "omitted_available"}, {"category": "invalid_citation"}])
def test_invalid_material_references_fail_closed(updates):
    models, _ = model(indexed_finding(**updates))
    with pytest.raises(TeachingReviewEvidenceInvalid):
        indexed_material_review(models, data(), "Review.", None)


def test_other_units_material_cannot_support_a_finding():
    models, _ = model(indexed_finding(required_index=1))
    payload = data()
    payload["required_material"].append("A different unit.")
    review = indexed_material_review(models, payload, "Review.", None)
    assert evidence_problem(review, payload)["code"] == "REQUIRED_UNIT_MISMATCH"


def test_indexed_unsupported_claim_can_have_no_corresponding_source_excerpt():
    models, _ = model(indexed_finding(category="unsupported_claim", required_index=-1, required_span_index=-1))
    review = indexed_material_review(models, data(), "Review.", None)
    assert evidence_problem(review, data()) is None
    assert review.findings[0].candidate_quote == data()["candidates"][0]


def test_spans_preserve_unicode_math_operators_and_long_unbroken_tokens():
    text = r"The value **9** differs from 7; \(R=a/b\), α ≤ β. " * 50 + "x" * 1700
    parts = spans(text)
    assert all(part in text and 0 < len(part) <= 600 for part in parts)
    assert "".join("".join(part.split()) for part in parts) == "".join(text.split())

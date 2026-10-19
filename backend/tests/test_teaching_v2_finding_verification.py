"""Source-label uncertainty cannot hide real candidate errors, using fake AI."""

import json

import pytest
from test_a3_model_compat import Converse

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.teaching_v2 import TeachingModelsV2
from upraise_ai.services.teaching_v2.contracts import MaterialFinding
from upraise_ai.services.teaching_v2.finding_verification import verify_findings
from upraise_ai.services.teaching_v2.material_review import review_material

SOURCE = "| Specmen | R |\n| Bta | 7 |"
CANDIDATE = "The row labelled Bta has R = 7."


def data(candidate=CANDIDATE):
    return {"required_material": [SOURCE], "candidates": [candidate], "clarifying_context": [],
            "required_per_candidate": False}


def finding(category="changed_fact", candidate_quote=CANDIDATE):
    return MaterialFinding(category=category, candidate_index=0, candidate_quote=candidate_quote,
                           required_index=0, required_quote="| Bta | 7 |",
                           message="The row label may be an extraction error.")


def assessment(disposition="source_quality_only", index=0):
    return {"assessments": [{"finding_index": index, "disposition": disposition,
                             "reason": "The label is reported faithfully; its spelling is uncertain."}]}


def models(*replies):
    client = Converse([json.dumps(item) for item in replies])
    return TeachingModelsV2(Settings(), client), client


def test_source_label_accusation_becomes_teacher_warning_after_independent_check():
    review = {"findings": [finding().model_dump()], "warnings": [], "natural": True}
    nli = {"supported": True, "reason": "Matches the row."}
    model, client = models(review, assessment(), nli)
    decision, evidence = review_material(model, data(), "Audit material.")
    assert decision.passed and decision.warnings[0].category == "source_gap"
    assert evidence["evidence"] == []
    assert len(client.calls) == 3
    assert client.calls[1]["modelId"] == model.settings.lesson_generation_model_id


def test_whole_draft_entailment_still_blocks_when_label_accusation_is_reclassified():
    text = CANDIDATE + " R measures pressure."
    review = {"findings": [finding().model_dump()], "warnings": [], "natural": True}
    nli = {"supported": False, "reason": "The source does not define R."}
    model, _ = models(review, assessment(), nli)
    decision, _ = review_material(model, data(text), "Audit material.")
    assert not decision.passed and not decision.correct
    assert "does not define R" in decision.issues[0]
    assert decision.warnings[0].category == "source_gap"


@pytest.mark.parametrize("candidate", ["The row labelled Bta has R = 9.", "Bta is a verified species name.",
                                        "The value proves a causal mechanism."])
def test_confirmed_actual_errors_stay_blocking_even_if_entailment_misses_them(candidate):
    review = {"findings": [finding(candidate_quote=candidate).model_dump()], "warnings": [], "natural": True}
    nli = {"supported": True, "reason": "Missed by fixture."}
    model, _ = models(review, assessment("candidate_error"), nli)
    decision, _ = review_material(model, data(candidate), "Audit material.")
    assert not decision.passed and not decision.correct


def test_language_and_citation_findings_cannot_be_reclassified():
    findings = [finding(category=category) for category in ("wrong_language", "invalid_citation")]
    model, client = models()
    retained, warnings = verify_findings(model, data(), findings)
    assert retained == findings and not warnings and not client.calls


def test_mixed_findings_preserve_original_indices_and_nonfactual_error():
    findings = [finding(category="wrong_language"), finding()]
    model, client = models(assessment(index=1))
    retained, warnings = verify_findings(model, data(), findings)
    assert retained == findings[:1] and warnings
    sent, _ = json.JSONDecoder().raw_decode(client.calls[0]["messages"][0]["content"][0]["text"])
    assert [item["finding_index"] for item in sent["findings"]] == [1]
    assert "message" not in sent["findings"][0]


def test_incidental_header_omission_can_be_a_presentation_warning():
    omitted = finding(category="omitted_available", candidate_quote="")
    model, _ = models(assessment("presentation_only"))
    retained, warnings = verify_findings(model, data(), [omitted])
    assert not retained and warnings[0].category == "style"


@pytest.mark.parametrize("disposition", ["source_quality_only", "candidate_error"])
def test_actual_missing_content_stays_blocking_despite_source_quality_concerns(disposition):
    omitted = finding(category="omitted_available", candidate_quote="")
    model, _ = models(assessment(disposition))
    retained, warnings = verify_findings(model, data(), [omitted])
    assert retained == [omitted] and not warnings


@pytest.mark.parametrize("indices", [[-1], [1], [0, 0]])
def test_invalid_assessment_coverage_fails_closed(indices):
    bad = {"assessments": [assessment(index=index)["assessments"][0] for index in indices]}
    model, _ = models(bad, bad)
    with pytest.raises(IngestionError, match="TEACHING_V2_REVIEW_EVIDENCE_INVALID"):
        verify_findings(model, data(), [finding()])

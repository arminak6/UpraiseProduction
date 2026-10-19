"""Grounded candidate findings distinguish source limitations from actual errors."""

import json

import pytest
from pydantic import ValidationError
from test_a3_model_compat import Converse, section
from test_teaching_v2 import MATERIAL_PASS, material_error

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.teaching_v2 import TeachingModelsV2
from upraise_ai.services.teaching_v2.contracts import GroundedMaterialReview, TeachingMaterialRejected

SOURCE = "The sensor reports T = 4. Compare the observation with a reference. Definition:"
CANDIDATE = "Compare the observation with a reference. The defining equation is absent. [p. 1]"
WARNING = {"category": "source_gap", "message": "The source omits the defining equation."}


@pytest.fixture(autouse=True)
def isolate_material_findings(monkeypatch):
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_findings", lambda models, payload, findings: (findings, []))
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_entailment", lambda *args: [])


def check(responses):
    client = Converse([json.dumps(item) for item in responses])
    models = TeachingModelsV2(Settings(), client)
    src = section()
    src["chunks"][0]["text"] = SOURCE
    return models, client, src


def test_missing_source_content_is_a_warning_without_false_global_flags():
    review = {**MATERIAL_PASS, "warnings": [WARNING]}
    models, client, src = check([review])
    warnings = models.check_written(src, [CANDIDATE], "en", "intermediate")
    assert [item.message for item in warnings] == [WARNING["message"]]
    assert len(client.calls) == 1
    assert "no global correctness/completeness booleans" in client.calls[0]["system"][0]["text"]
    with pytest.raises(ValidationError):
        GroundedMaterialReview.model_validate({**review, "correct": False})


@pytest.mark.parametrize("category,candidate,quote,required,flag", [
    ("changed_fact", "The sensor reports T = 5.", "T = 5", "T = 4", "correct"),
    ("unsupported_claim", "The sensor proves a causal relationship.", "proves a causal relationship", "The sensor reports T = 4.", "correct"),
    ("omitted_available", CANDIDATE, "", "T = 4", "complete"),
    ("wrong_language", "El sensor mide temperatura.", "El sensor mide temperatura.", "", "in_language"),
    ("invalid_citation", "A supported claim. [p. 99]", "[p. 99]", "", "correct"),
])
def test_actual_errors_remain_blocking_even_with_source_gap_warnings(category, candidate, quote, required, flag):
    review = material_error("An actual candidate error.", category=category, candidate_quote=quote, required_quote=required)
    review["warnings"] = [WARNING]
    models, _, src = check([review])
    with pytest.raises(TeachingMaterialRejected) as failure:
        models.check_written(src, [candidate], "en", "intermediate")
    assert not getattr(failure.value.review, flag)


def test_unsupported_claim_without_a_source_excerpt_is_a_real_error_not_invalid_review():
    bad = material_error("This causal claim is absent from the source.", category="unsupported_claim",
                         candidate_quote="proves causation", required_quote="")
    bad["findings"][0]["required_index"] = -1
    models, _, src = check([bad])
    with pytest.raises(TeachingMaterialRejected) as error:
        models.check_written(src, ["The sensor proves causation."], "en", "intermediate")
    assert not error.value.review.correct and error.value.code == "TEACHING_V2_MATERIAL_REJECTED"


@pytest.mark.parametrize("defect", ["candidate_quote", "required_quote", "candidate_index", "required_index", "negative_index"])
def test_fabricated_or_out_of_bounds_evidence_never_passes_or_instructs_author(defect):
    review = material_error("A changed value.", candidate_quote="T = 5", required_quote="T = 4")
    finding = review["findings"][0]
    finding.update({"candidate_quote": "Invented candidate text"} if defect == "candidate_quote" else
                   {"required_quote": "T = 9000"} if defect == "required_quote" else
                   {"candidate_index": 1} if defect == "candidate_index" else
                   {"required_index": 1} if defect == "required_index" else {"required_index": -1})
    models, client, src = check([review, review])
    with pytest.raises(IngestionError, match="TEACHING_V2_REVIEW_EVIDENCE_INVALID"):
        models.check_written(src, ["The sensor reports T = 5."], "en", "intermediate")
    assert len(client.calls) == 4  # Two quote attempts, bounded indexed/schema recovery.
    feedback, _ = json.JSONDecoder().raw_decode(client.calls[-1]["messages"][0]["content"][0]["text"])
    assert feedback["evidence_feedback"]["previous_finding"] == finding


def test_reviewer_can_correct_bad_evidence_without_changing_faithful_lesson():
    bad = material_error("Demands an absent formula.", category="omitted_available", candidate_quote="", required_quote="T = a/b")
    models, client, src = check([bad, {**MATERIAL_PASS, "warnings": [WARNING]}])
    warnings = models.check_written(src, [CANDIDATE], "en", "intermediate")
    assert len(client.calls) == 2 and warnings[0].message == WARNING["message"]


def test_omission_already_present_in_another_candidate_is_not_accepted_as_evidence():
    bad = material_error("Missing T.", category="omitted_available", candidate_quote="", required_quote="T = 4")
    models, client, src = check([bad, MATERIAL_PASS])
    assert models.check_written(src, [CANDIDATE, "The sensor reports T = 4."], "en", "intermediate") == []
    feedback, _ = json.JSONDecoder().raw_decode(client.calls[-1]["messages"][0]["content"][0]["text"])
    assert feedback["evidence_feedback"]["code"] == "OMITTED_QUOTE_ALREADY_PRESENT"


def test_omission_evidence_cannot_come_only_from_other_topics_or_context():
    bad = material_error("Missing next-topic formula.", category="omitted_available", candidate_quote="", required_quote="T = 4")
    models, _, src = check([bad, bad])
    with pytest.raises(IngestionError, match="TEACHING_V2_REVIEW_EVIDENCE_INVALID"):
        models.check_written(src, [CANDIDATE], "en", "intermediate", required_material=["Compare with a reference."])


def test_another_aligned_unit_cannot_satisfy_the_current_units_missing_fact():
    missing = material_error("First unit omits T.", category="omitted_available", candidate_quote="", required_quote="T = 4")
    models, _, src = check([missing])
    with pytest.raises(TeachingMaterialRejected) as failure:
        models.check_written(src, [CANDIDATE, "The sensor reports T = 4."], "en", "intermediate",
                             required_material=["T = 4", "A separate comparison."])
    assert not failure.value.review.complete


def test_passing_model_review_cannot_allow_invented_numerical_question_inputs():
    models, _, src = check([MATERIAL_PASS])
    with pytest.raises(TeachingMaterialRejected) as failure:
        models.check_written(src, ["Suppose the sensor measured 37.5 rather than 8.2. What would you conclude?"], "en", "intermediate")
    assert "numerical inputs" in failure.value.details["reason"]


def test_questions_reuse_source_values_and_equivalent_number_formatting():
    models, _, src = check([MATERIAL_PASS])
    assert models.check_written(src, ["What does a reading of 4.0 mean? [p. 1]"], "en", "intermediate") == []


def test_equivalent_formula_constants_in_explanation_are_not_a_question_input():
    models, _, src = check([MATERIAL_PASS])
    assert models.check_written(src, ["An equivalent expression has a factor of 1/2."], "en", "intermediate") == []


def test_fragment_policy_applies_to_arbitrary_subjects_and_keeps_meaningful_numbers():
    from test_teaching_v2 import authored
    src = section()
    src["chunks"][0]["text"] = "27\n[Figure] A decorative word 'Next' with an isolated numeral.\nIntuition: compare a plant's growth with a reference plant.\n| Plant | Height |\n| A | 12 cm |"
    text = "Compare a plant's growth with a reference plant as a qualitative intuition. Plant A is 12 cm tall. [p. 1]"
    selected = {"items": [{"source_index": 0, "disposition": "teach", "teaching_quotes": [src["chunks"][0]["text"]], "note": ""}]}
    client = Converse([json.dumps(selected), authored(text), json.dumps({**MATERIAL_PASS, "warnings": [WARNING]})])
    draft, report, _ = TeachingModelsV2(Settings(), client).generate_section(src, "en")
    assert "12 cm" in draft.paragraphs[0].text and not report.approved
    assert "27" not in draft.paragraphs[0].text and "Next" not in draft.paragraphs[0].text
    assert "equations, data tables" in client.calls[1]["system"][0]["text"]

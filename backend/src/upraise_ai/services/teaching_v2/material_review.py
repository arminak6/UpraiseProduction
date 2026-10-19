"""Review candidate claims, not completeness of the source itself."""

import re
from decimal import Decimal, InvalidOperation

from .contracts import GroundedMaterialReview, MaterialDecision, TeachingReviewEvidenceInvalid, structured
from .entailment import verify_entailment
from .evidence import indexed_material_review
from .finding_verification import verify_findings


def normalized(text):
    # Only layout whitespace is equivalent; operators/case/values stay exact.
    return " ".join(text.split())


def numbers(text):
    values = set()
    for value in re.findall(r"(?<![\w.])[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?", text):
        try:
            values.add(Decimal(value.replace(",", ".")))
        except InvalidOperation:
            values.add(value)
    return values


def question_number_issues(payload):
    # Questions must reuse actual source data, rather than invent hypothetical
    # measurements. This does not constrain equivalent notation in explanations.
    evidence = [*payload["required_material"], *[item["text"] for item in payload["clarifying_context"]]]
    allowed = set().union(*(numbers(text) for text in evidence))
    issues = []
    for index, text in enumerate(payload["candidates"]):
        if not any(mark in text for mark in ("?", "？", "؟")):
            continue
        prose = re.sub(r"\[(?:p\.|pp\.|Word blocks?|CSV blocks?)[^\]]*\]", " ", text, flags=re.IGNORECASE)
        prose = re.sub(r"(?m)^\s*\d+[.)]\s+", "", prose)
        if numbers(prose) - allowed:
            issues.append(f"Candidate {index + 1} introduces numerical inputs in a learner question that are absent from the supplied material. "
                          "Use a qualitative question, source-defined variables or numerical data already supplied; remove invented measurements.")
    return issues


def evidence_problem(review, payload):
    for index, finding in enumerate(review.findings):
        if not 0 <= finding.candidate_index < len(payload["candidates"]):
            return {"finding": index, "code": "CANDIDATE_INDEX_INVALID"}
        candidate = payload["candidates"][finding.candidate_index]
        if finding.category != "omitted_available":
            if not finding.candidate_quote.strip() or normalized(finding.candidate_quote) not in normalized(candidate):
                return {"finding": index, "code": "CANDIDATE_QUOTE_NOT_FOUND"}
        if finding.category not in {"wrong_language", "invalid_citation"}:
            if finding.category == "unsupported_claim" and finding.required_index == -1 and not finding.required_quote.strip():
                # Absence cannot be quoted. The actual candidate claim is still
                # mandatory, and full allowed material is independently audited.
                continue
            required = payload["required_material"]
            if not 0 <= finding.required_index < len(required):
                return {"finding": index, "code": "REQUIRED_INDEX_INVALID"}
            if payload.get("required_per_candidate") and finding.required_index != finding.candidate_index:
                return {"finding": index, "code": "REQUIRED_UNIT_MISMATCH"}
            quote = normalized(finding.required_quote)
            if not quote or quote not in normalized(required[finding.required_index]):
                return {"finding": index, "code": "REQUIRED_QUOTE_NOT_FOUND"}
            candidates = [candidate] if payload.get("required_per_candidate") else payload["candidates"]
            if finding.category == "omitted_available" and any(quote in normalized(text) for text in candidates):
                return {"finding": index, "code": "OMITTED_QUOTE_ALREADY_PRESENT"}
    return None


def review_material(models, payload, policy):
    feedback = None
    for attempt in range(3):
        reviewed = (indexed_material_review(models, payload, policy, feedback) if attempt == 2 else
                    structured(models, policy, {**payload, "evidence_feedback": feedback},
                               GroundedMaterialReview, "teaching_v2_final_review", reviewer=True, max_tokens=6000))
        problem = evidence_problem(reviewed, payload)
        if problem is None:
            findings, warnings = verify_findings(models, payload, reviewed.findings)
            kinds = {finding.category for finding in findings}
            question_issues = question_number_issues(payload)
            # Always inspect the whole draft, including when the broad review
            # concentrated on a source typo and missed a substantive added claim.
            entailment_issues = verify_entailment(models, payload)
            decision = MaterialDecision(correct=not question_issues and not entailment_issues and not kinds.intersection({"changed_fact", "unsupported_claim", "invalid_citation"}),
                complete="omitted_available" not in kinds, in_language="wrong_language" not in kinds,
                natural=reviewed.natural, issues=([finding.message for finding in findings] + entailment_issues + question_issues)[:30], warnings=(reviewed.warnings + warnings)[:20])
            evidence = [{"category": item.category, "candidate_index": item.candidate_index,
                         "candidate_quote": item.candidate_quote[:300], "required_index": item.required_index,
                         "required_quote": item.required_quote[:300]} for item in findings[:5]]
            return decision, {"evidence_checks": attempt + 1, "evidence": evidence}
        feedback = {**problem, "previous_finding": reviewed.findings[problem["finding"]].model_dump()}
    # Ungrounded accusations never become author correction instructions, nor
    # does malformed review silently pass. No raw output enters logs/audit.
    raise TeachingReviewEvidenceInvalid("material_review", problem["code"])

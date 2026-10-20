"""Human-visible DRAFT recovery for bounded automated review failures."""

from upraise_ai.schemas.aligned_lessons import AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import DraftParagraph, ReviewIssue, SectionDraft

POLICY = "flag-draft-for-human-review-v1"
FLAG_PREFIX = "Human review required: "
FLAG_SUMMARY = "DRAFT; human review required because automated checks remain unresolved."

REVIEW_ERRORS = {
    "TEACHING_V2_REVIEW_EVIDENCE_INVALID", "TEACHING_V2_MODEL_OUTPUT_INVALID",
    "TEACHING_V2_MATERIAL_REJECTED", "TEACHING_V2_NARRATION_REJECTED",
    "TEACHING_V3_MODEL_RESPONSE_INVALID", "TEACHING_V3_NARRATION_REJECTED",
    "TEACHING_V3_COURSE_REVIEW_INVALID", "TEACHING_V3_COURSE_INCONSISTENT",
    "TEACHING_V3_WRITTEN_CONTINUITY_REJECTED", "TEACHING_V3_SPOKEN_CONTINUITY_REJECTED",
}


def review_failure(error):
    return error.code in REVIEW_ERRORS


def review_flag(stage, error, *, pages=(), category="source", issues=()):
    # Exception strings/provider output may contain private data. Only trusted
    # codes and bounded, already parsed review findings belong in the report.
    details = getattr(error, "details", {})
    problem = details.get("problem")
    if isinstance(problem, dict):
        problem = problem.get("code")
    code = error.code + ("; " + problem if problem else "")
    findings = getattr(getattr(error, "review", None), "issues", issues)
    message = (FLAG_PREFIX + stage + " remains unresolved (" + code + "). "
               "Generation continued as a DRAFT without automated approval. "
               "Review this part against its source before teaching.")
    if findings:
        message += " Findings: " + "; ".join(" ".join(s.split())[:500] for s in findings[:5])
    return ReviewIssue(category=category, message=message[:4000], pages=list(pages))


def gap_text(language):
    messages = {
        "en": "Human review required. A reliable explanation is unavailable for this part. Please review the source and complete this draft.",
        "it": "È necessaria una revisione umana. Per questa parte non è disponibile una spiegazione affidabile. Consultare la fonte e completare questa bozza.",
        "fr": "Une révision humaine est nécessaire. Une explication fiable manque pour cette partie. Consultez la source et complétez ce brouillon.",
        "de": "Eine menschliche Prüfung ist erforderlich. Für diesen Teil fehlt eine zuverlässige Erklärung. Bitte prüfen Sie die Quelle und vervollständigen Sie diesen Entwurf.",
        "es": "Se requiere revisión humana. No hay una explicación fiable para esta parte. Revise la fuente y complete este borrador.",
        "pt": "É necessária uma revisão humana. Não há uma explicação fiável para esta parte. Consulte a fonte e complete este rascunho.",
    }
    return messages.get(language.lower().split("-")[0], messages["en"])


def material_gap(section, language, *, units=None):
    text = gap_text(language)
    if units is not None:
        return AlignedTexts(units=[AlignedText(content_unit_id=u.content_unit_id, text=text) for u in units])
    return SectionDraft(paragraphs=[DraftParagraph(text=text, pages=section["pages"])])

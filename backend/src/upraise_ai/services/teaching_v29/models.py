"""V2.9 teaching uses a single judge, retaining its output after final review."""

from pydantic import BaseModel, ConfigDict, Field

from upraise_ai.schemas.aligned_lessons import AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import ReviewReport, SectionDraft
from upraise_ai.services.aligned_models import POLICY as ALIGNMENT_POLICY
from upraise_ai.services.aligned_models import structured as aligned_structured
from upraise_ai.services.lesson_prompts import learner_level_system_prompt
from upraise_ai.services.source_references import citation_policy
from upraise_ai.services.teaching_v2 import TeachingModelsV2
from upraise_ai.services.teaching_v2.contracts import structured
from upraise_ai.services.teaching_v2.first_draft import retention_error
from upraise_ai.services.teaching_v2.prompts import TEACHING_POLICY

from . import workflow
from .prompts import POLICY, VERSION


class RevisionText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16000)


def combined(reports):
    issues = [issue for report in reports for issue in report.issues]
    return ReviewReport(approved=not issues,
        summary="V2.9 automated final checks passed; human approval remains required." if not issues
                else "DRAFT; human review required because automated checks remain unresolved.", issues=issues)


class TeachingModelsV29(TeachingModelsV2):
    teaching_versions = {**TeachingModelsV2.teaching_versions, "teaching_version": "v2.9",
        "teaching_policy": VERSION, "math_speech_prompt": VERSION,
        "draft_review_recovery": POLICY, "material_recovery": "keep-judge-output-with-final-review-tags-v1",
        "material_review": "independent-source-and-level-review-v29",
        "material_finding_verification": "judge-source-adjudication-v1",
        "material_evidence": "source-context-and-indexed-candidate-reviews-v1",
        "material_grounding": "independent-source-review-v29",
        "post_edit_material_review": "v29-fresh-final-review",
        "math_runtime": "judge-reviewed-natural-narration-v29",
        "material_final_editor": "single-source-authoritative-judge-v1",
        "review_strategy": POLICY, "judge_rounds": "1"}
    reviews_final_candidate = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teaching_versions.update(judge_model=self.settings.lesson_generation_model_id)

    @property
    def preserve_first_draft(self):
        # Shared pipelines must retain the finalized candidate on quality failures.
        return self.draft_review_recovery

    def first_candidate(self, stage, payload, schema, generate):
        return workflow.first_candidate(self, stage, payload, schema, generate)

    def context(self, section, language, learner_level, *, units=None, profiles=None):
        return {"kind": "written", **self.material_context(section), "language": language,
                "learner_level": learner_level,
                "level_profile": (profiles or self.settings.lesson_level_profiles).get(learner_level),
                "citation_numbers": section["pages"],
                "citation_policy": citation_policy(section),
                "fixed_units": [unit.model_dump(mode="json", exclude={"canonical_review"}) for unit in units]
                               if units is not None else None,
                "scope": "Each candidate retains its corresponding fixed-unit topic using source-supported facts."
                         if units is not None else "Cover all substantive teaching material in this source window."}

    def checked_material(self, section, candidate, language, learner_level, *, units=None, max_words=None,
                         allow_source_excerpt=False):
        context = self.context(section, language, learner_level, units=units)
        final, reports, generated = workflow.adjudicate(self, candidate, context, language,
            stage="written", units=units, max_words=max_words)
        return final, int(generated), combined(reports).issues

    def check_written(self, section, texts, language, learner_level, required_material=None, *, correction_rounds=0):
        context = self.context(section, language, learner_level)
        if required_material is not None:
            context.update(required_topics=required_material,
                scope="Review this selected teaching topic only; original teaching is not factual authority.")
        candidate = SectionDraft(paragraphs=[{"text": text, "pages": section["pages"]} for text in texts])
        candidate, local = workflow.prepare(candidate, context, language)
        return combined(workflow.review(self, candidate, context, language, stage="final", local_issues=local)).issues

    def adapt_units(self, section, units, language, *, learner_level, profiles, target_words, max_words,
                    notify=None, current=None):
        section, source_warnings = self.select_source(section)
        context = self.context(section, language, learner_level, units=units, profiles=profiles)
        payload = {**context, "units": context["fixed_units"], "target_words": target_words, "max_words": max_words}
        policy = ALIGNMENT_POLICY + TEACHING_POLICY + citation_policy(section) + learner_level_system_prompt(
            learner_level, "generator", profiles=profiles)
        def generate():
            return aligned_structured(self, policy, payload, AlignedTexts, "teaching_v29_difficulty_writer", expected_units=units)
        try:
            candidate = self.first_candidate("difficulty", [policy, payload], AlignedTexts, generate)
        except Exception as error:
            if retention_error(error) is None:
                raise
            candidate = AlignedTexts(units=[AlignedText(content_unit_id=unit.content_unit_id,
                text=unit.material_text) for unit in units])
        if notify:
            notify("REVIEW")
        final, reports, _ = workflow.adjudicate(self, candidate, context, language, stage="difficulty",
                                               units=units, max_words=max_words)
        if notify:
            notify("FINALIZE")
        return final, {str(unit.content_unit_id): self.retain_warnings(report, source_warnings) if source_warnings else report
                       for unit, report in zip(units, reports, strict=True)}

    def revise_chunk(self, section, original, feedback, neighbours, *, learner_level, notify):
        section, source_warnings = self.select_source(section)
        language = section["source_language"]
        context = self.context(section, language, learner_level, profiles=section.get("level_profiles"))
        context.update(teacher_feedback=feedback, original_teaching=original,
                       required_topic=section.get("alignment_unit"),
                       neighbouring_teaching_for_continuity_only=neighbours,
                       scope="Revise only the selected topic according to source-supported teacher feedback.")
        policy = TEACHING_POLICY + citation_policy(section) + learner_level_system_prompt(
            learner_level, "generator", profiles=section.get("level_profiles"))
        def generate():
            return structured(self, policy, context, RevisionText, "teaching_v29_revision_writer")
        try:
            candidate = self.first_candidate("revision", [policy, context], RevisionText, generate)
        except Exception as error:
            if retention_error(error) is None:
                raise
            candidate = RevisionText(text=original)
        notify("REVIEW")
        final, reports, generated = workflow.adjudicate(self, candidate, context, language, stage="revision")
        notify("FINALIZE")
        report = combined(reports)
        if source_warnings:
            report = self.retain_warnings(report, source_warnings)
        return final.text, report, int(generated)

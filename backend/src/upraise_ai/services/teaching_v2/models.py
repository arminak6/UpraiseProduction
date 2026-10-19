"""Independent V2 authoring and grounded review; V1 stays unchanged."""

import hashlib
import json
import re

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import ReviewIssue, ReviewReport, SectionDraft
from upraise_ai.services.aligned_models import validate_ids, validate_material_format
from upraise_ai.services.lesson_models import LessonModels, _LanguageClient
from upraise_ai.services.source_references import citation_policy
from upraise_ai.services.v3.bedrock_teacher import BedrockTeacherV1

from .context import DocumentContext, factual_chunk
from .contracts import (
    MAX_MATERIAL_CORRECTIONS,
    MaterialDecision,
    TeachingMaterialRejected,
    TeachingReviewEvidenceInvalid,
    structured,
    validate_written,
)
from .excerpts import EXCERPT_SUMMARY, EXCERPT_WARNING, RECOVERY_WARNINGS, source_excerpt
from .material_review import review_material
from .prompts import (
    CONTEXT_POLICY,
    MATERIAL_AUTHORING_POLICY,
    MATERIAL_CORRECTION_POLICY,
    MATERIAL_REVIEW_POLICY,
    TEACHING_POLICY,
    VERSION,
)
from .source_selection import select_source


class _TeachingClientV2(_LanguageClient):
    def converse(self, **request):
        if getattr(self, "authoring", False) or self.teaching_prompt is not None:
            request["system"] = [*request.get("system", []), {"text": TEACHING_POLICY}]
            if getattr(self, "clarifying_context", None):
                request["system"].append({"text": CONTEXT_POLICY})
                messages = [dict(message) for message in request["messages"]]
                user = next(message for message in reversed(messages) if message["role"] == "user")
                user["content"] = [*user["content"], {"text": json.dumps(
                    {"clarifying_context": self.clarifying_context}, ensure_ascii=False)}]
                request["messages"] = messages
        return super().converse(**request)


class TeachingModelsV2(LessonModels):
    teaching_versions = {"teaching_version": "v2", "teaching_policy": VERSION,
                         "math_speech_prompt": VERSION, "math_runtime": "reviewed-natural-narration-v2",
                         "post_edit_material_review": "teaching-v2-independent-final",
                         "material_review": "grounded-candidate-findings-v4",
                         "material_finding_verification": "source-presentation-only-v2",
                         "material_evidence": "host-bound-evidence-v2",
                         "material_grounding": "generation-model-paragraph-entailment-v2",
                         "material_final_editor": "configured-review-model-v1",
                         "material_recovery": "source-excerpt-draft-v1",
                         "teaching_source_selection": "exact-source-excerpts-v1",
                         "teaching_context": "same-document-bounded-context-v1"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transport = _TeachingClientV2(self.client)
        self.teacher._client = self.transport
        # V2 script/review calls carry their own language and output policies.
        self.v2_teacher = BedrockTeacherV1(session=None, client=self.client,
            model_id=self.settings.lesson_generation_model_id, reviewer_model_id=self.settings.lesson_review_model_id)
        self.document_context = DocumentContext([])
        self.selected_sections = {}

    def set_document_context(self, documents):
        self.document_context = DocumentContext(documents)
        self.selected_sections = {}

    def select_source(self, section):
        key = tuple(chunk.get("chunk_id", chunk["text"]) for chunk in section["chunks"])
        if key not in self.selected_sections:
            context = self.document_context.select(section)
            selected, warnings = select_source(self, {**section, "primary_source_count": len(section["chunks"]),
                                                      "chunks": [*section["chunks"], *context]})
            ids = {chunk.get("chunk_id") for chunk in section["chunks"]}
            primary = [chunk for chunk in selected["chunks"] if chunk.get("chunk_id") in ids]
            if not primary:
                primary = [{**section["chunks"][0], "text": "[No readable substantive teaching content in this source window.]"}]
                warnings.append(ReviewIssue(category="source", message="This source window contains no readable substantive teaching content.",
                                            pages=section["pages"]))
            selected = {**section, "chunks": primary,
                        "selected_clarifying_context": [factual_chunk(chunk) for chunk in selected["chunks"]
                                                        if chunk.get("chunk_id") not in ids]}
            self.selected_sections[key] = selected, [warning for warning in warnings
                                                    if set(warning.pages).intersection(section["pages"])]
        return self.selected_sections[key]

    def material_context(self, section):
        # Use the same factual evidence as the original teacher. Extraction
        # diagnostics may propose different wording or missing content; those
        # proposals are not source facts and must not become correction orders.
        return {"source_context": [factual_chunk(chunk) for chunk in section["chunks"]],
                "clarifying_context": section.get("selected_clarifying_context", self.document_context.select(section)),
                "source_quality": [{"chunk_id": chunk.get("chunk_id"),
                    "status": chunk.get("quality", {}).get("status", "UNVERIFIED")}
                    for chunk in section["chunks"]]}

    def check_written(self, section, texts, language, learner_level, required_material=None, *, correction_rounds=0):
        key = tuple(chunk.get("chunk_id", chunk["text"]) for chunk in section["chunks"])
        selected = self.selected_sections.get(key)
        source_warnings = []
        if selected:
            section, source_warnings = selected
        for text in texts:
            validate_written(text)
        payload = {"kind": "written", **self.material_context(section), "candidates": texts,
             "required_material": required_material if required_material is not None else
                                  [chunk["text"] for chunk in section["chunks"]],
             "required_per_candidate": required_material is not None,
             "scope": "All substantive source-window content is required." if required_material is None else
                      "Each candidate must preserve its corresponding required material in supplied order. "
                      "Check this chunk/batch only; other source-window topics are not required.",
             "language": language, "learner_level": learner_level, "citation_numbers": section.get("pages", [])}
        policy = TEACHING_POLICY + MATERIAL_REVIEW_POLICY + CONTEXT_POLICY + citation_policy(section)
        try:
            decision, evidence = review_material(self, payload, policy)
        except TeachingReviewEvidenceInvalid as error:
            error.details.update(source_window=section.get("window", []), learner_level=learner_level,
                                 correction_rounds=correction_rounds)
            raise
        if self.diagnostic_dir:
            from upraise_ai.services.lesson_pipeline import write_json
            key = hashlib.sha256(json.dumps([VERSION, payload], sort_keys=True).encode()).hexdigest()
            # Bounded parsed findings/excerpts follow job TTL/erasure. Raw
            # provider responses never enter diagnostics, logs or audit.
            details = TeachingMaterialRejected(decision, section=section, learner_level=learner_level,
                                               correction_rounds=correction_rounds).details
            if decision.passed:
                details.pop("reason")
            write_json(self.diagnostic_dir.parent / "teaching-v2-material" / f"{key}.json",
                       {"review_key": key, "passed": decision.passed, **evidence,
                        "candidate_excerpts": [text[:1200] for text in texts[:3]] if not decision.passed else [],
                        "context_source_chunk_ids": [item.get("chunk_id") for item in payload["clarifying_context"]], **details})
        if not decision.passed:
            raise TeachingMaterialRejected(decision, section=section, learner_level=learner_level,
                                           correction_rounds=correction_rounds)
        pages = section.get("pages", sorted({page for chunk in section["chunks"] for page in chunk.get("page_numbers", [])}))
        warnings = source_warnings + [ReviewIssue(category="source" if item.category == "source_gap" else "clarity",
                                message=item.message, pages=pages) for item in decision.warnings]
        if not decision.natural and not any(item.category == "style" for item in decision.warnings):
            warnings.append(ReviewIssue(category="clarity", pages=pages,
                message="The written explanation needs teacher review for style or clarity."))
        return warnings

    def checked_material(self, section, candidate, language, learner_level, *, units=None, max_words=None,
                         allow_source_excerpt=False):
        """Use actual final-review feedback; never retry the same rejection blindly."""
        from upraise_ai.services.lesson_pipeline import validate_draft

        required = [unit.material_text for unit in units] if units is not None else None
        for rounds in range(MAX_MATERIAL_CORRECTIONS + 1):
            invalid_citations = False
            if isinstance(candidate, SectionDraft):
                texts = [part.text for part in candidate.paragraphs]
                try:
                    validate_draft(candidate, section["pages"])
                    allowed = set(section["pages"])
                    for text in texts:
                        for match in re.finditer(r"\[(?:p|pp)\.\s*(\d+)(?:\s*-\s*(\d+))?\]", text):
                            start, end = int(match[1]), int(match[2] or match[1])
                            if end < start or end - start > 5000 or not set(range(start, end + 1)).issubset(allowed):
                                raise IngestionError("LESSON_CITATIONS_INVALID", retryable=True)
                except IngestionError as error:
                    if error.code != "LESSON_CITATIONS_INVALID":
                        raise
                    invalid_citations = True
            else:
                validate_ids(units, candidate.units)
                for unit, item in zip(units, candidate.units, strict=True):
                    item.text = validate_material_format(unit, item.text)
                texts = [item.text for item in candidate.units]
            try:
                if invalid_citations:
                    decision = MaterialDecision(correct=False, complete=True, in_language=True, natural=True,
                        issues=[f"Use only the primary source numbers {section['pages']} in paragraph pages and inline citations. "
                                "Remove claims taken only from neighbouring topics; do not merely relabel an unsupported claim."])
                    raise TeachingMaterialRejected(decision, section=section, learner_level=learner_level, correction_rounds=rounds)
                warnings = self.check_written(section, texts, language, learner_level, required, correction_rounds=rounds)
                return candidate, rounds, warnings
            except TeachingMaterialRejected as error:
                if rounds == MAX_MATERIAL_CORRECTIONS:
                    if (allow_source_excerpt and not invalid_citations and error.review.in_language
                            and (not error.review.correct or not error.review.complete)):
                        recovered = source_excerpt(section, language, units=units, max_words=max_words)
                        if recovered is not None:
                            draft, warnings = recovered
                            return draft, rounds, warnings
                    raise
                payload = {**self.material_context(section), "previous_candidate": candidate.model_dump(mode="json"),
                           "review": error.review.model_dump(), "language": language, "learner_level": learner_level,
                           "required_material": required,
                           "fixed_units": [unit.model_dump(mode="json") for unit in units] if units is not None else None,
                           "max_words_per_unit": max_words}
                candidate = structured(self, TEACHING_POLICY + MATERIAL_CORRECTION_POLICY + CONTEXT_POLICY + citation_policy(section),
                                       payload, type(candidate), "teaching_v2_material_correction",
                                       reviewer=rounds == MAX_MATERIAL_CORRECTIONS - 1, max_tokens=8000)

    @staticmethod
    def retain_warnings(report, warnings):
        if not warnings:
            return report
        issues = list({json.dumps(item.model_dump(), sort_keys=True): item for item in [*report.issues, *warnings]}.values())
        summary = EXCERPT_SUMMARY if any(item.message == EXCERPT_WARNING for item in issues) else report.summary
        if any(item.message in RECOVERY_WARNINGS and item.message != EXCERPT_WARNING for item in issues):
            summary = "Reviewed-text DRAFT; teacher editing for the requested level is required."
        return ReviewReport(approved=False, summary=summary,
            issues=issues, revision_instructions=report.revision_instructions)

    @staticmethod
    def reuse_canonical_units(units):
        """Same difficulty already received final material review; do not rewrite."""
        for unit in units:
            validate_written(unit.material_text)
            if unit.canonical_review is None:
                raise IngestionError("TEACHING_V2_CANONICAL_REVIEW_MISSING")
        return (AlignedTexts(units=[AlignedText(content_unit_id=unit.content_unit_id, text=unit.material_text) for unit in units]),
                {str(unit.content_unit_id): unit.canonical_review for unit in units})

    def generate_section(self, section, language, *args, learner_level="intermediate", **kwargs):
        notify = kwargs.get("notify")
        section, source_warnings = self.select_source(section)
        profiles = kwargs.get("level_profiles") or self.settings.lesson_level_profiles
        payload = {**self.material_context(section), "language": language, "learner_level": learner_level,
                   "level_profile": profiles.get(learner_level), "primary_source_numbers": section["pages"]}
        draft = structured(self, TEACHING_POLICY + MATERIAL_AUTHORING_POLICY + CONTEXT_POLICY + citation_policy(section),
                           payload, SectionDraft, "teaching_v2_section")
        if notify:
            notify("REVIEW")
        draft, rounds, warnings = self.checked_material(section, draft, language, learner_level, allow_source_excerpt=True)
        report = ReviewReport(approved=True, summary="V2 automated material checks completed; human review is required.")
        report = self.retain_warnings(report, [*source_warnings, *warnings])
        if notify:
            notify("FINALIZE")
        return draft, report, int(rounds > 0)

    def adapt_units(self, section, units, language, *, learner_level, **kwargs):
        section, source_warnings = self.select_source(section)
        self.transport.authoring = True
        self.transport.clarifying_context = section.get("selected_clarifying_context", self.document_context.select(section))
        try:
            author_section = {**section, "chunks": [factual_chunk(chunk) for chunk in section["chunks"]]}
            texts, reports = super().adapt_units(author_section, units, language, learner_level=learner_level, **kwargs)
        finally:
            self.transport.authoring = False
            self.transport.clarifying_context = None
        texts, _, warnings = self.checked_material(section, texts, language, learner_level, units=units,
                                         max_words=kwargs.get("max_words"), allow_source_excerpt=True)
        if any(item.message in RECOVERY_WARNINGS for item in warnings):
            # Earlier adaptation findings describe the discarded prose, not this copy.
            reports = {str(unit.content_unit_id): ReviewReport(approved=True, summary=EXCERPT_SUMMARY) for unit in units}
        reports = {key: self.retain_warnings(report, [*source_warnings, *warnings]) for key, report in reports.items()}
        return texts, reports

    def revise_chunk(self, section, *args, learner_level, **kwargs):
        section, _ = self.select_source(section)
        self.transport.authoring = True
        self.transport.clarifying_context = section.get("selected_clarifying_context", self.document_context.select(section))
        try:
            author_section = {**section, "chunks": [factual_chunk(chunk) for chunk in section["chunks"]]}
            text, report, revisions = super().revise_chunk(author_section, *args, learner_level=learner_level, **kwargs)
        finally:
            self.transport.authoring = False
            self.transport.clarifying_context = None
        # RevisionPipeline checks the final output after translation/alignment corrections.
        return text, report, revisions

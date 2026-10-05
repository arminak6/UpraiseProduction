from upraise_ai.core.config import Settings
from upraise_ai.core.utils import resolve_project_path
from upraise_ai.schemas.documents import ContentQuality, ElementType, QualityStatus
from upraise_ai.services.formats import ParseResult, ProgressCallback

from .fidelity import (
    inline_fragments,
    latex_structure_issues,
    merge_inline_math,
    source_evidence,
    table_structure_issues,
)
from .inline_math import looks_like_inline_math
from .vision import (
    FORMULA_PROMPT_VERSION,
    INLINE_MATH_PROMPT_VERSION,
    VERIFICATION_PROMPT_VERSION,
    BedrockImageDescriber,
)


class PdfEnricher:
    def __init__(
        self,
        settings: Settings,
        image_describer: BedrockImageDescriber | None,
        math_transcriber: BedrockImageDescriber | None,
        *,
        verifier: BedrockImageDescriber | None = None,
    ):
        self.settings = settings
        self.image_describer = image_describer
        self.math_transcriber = math_transcriber
        self.verifier = verifier

    def enrich(self, parse_result: ParseResult, progress: ProgressCallback | None = None) -> list[str]:
        warnings = list(parse_result.warnings)
        for position, element in enumerate(parse_result.elements):
            if element.source_text is None:
                element.source_text = element.original_text
            if element.source_structured_content is None:
                element.source_structured_content = element.structured_content
            element.original_text = element.source_text
            element.structured_content = element.source_structured_content
            element.quality = ContentQuality(checks=["parser_output_preserved"])
            self._progress(
                progress, "enrich", 0.42 + 0.20 * position / max(len(parse_result.elements), 1),
                f"Checking source fidelity for element {position + 1} of {len(parse_result.elements)}.",
            )
            if element.element_type is ElementType.FORMULA:
                self._formula(element)
            elif element.metadata.get("inline_math_candidate") and element.source_text.strip():
                self._inline_math(element)
            elif element.element_type is ElementType.TABLE:
                self._table(element)
            elif element.element_type is ElementType.FIGURE:
                self._figure(element)
            if element.quality.status is QualityStatus.NEEDS_REVIEW:
                pages = ", ".join(map(str, element.page_numbers or [element.page_number or "unknown"]))
                warnings.append(
                    f"Source fidelity review required for {element.element_type.value} "
                    f"element {element.element_index} on page(s) {pages}: "
                    + " ".join(element.quality.issues)
                )
        return list(dict.fromkeys(warnings))

    def _formula(self, element):
        paths, issues = source_evidence(element, self.settings.project_root)
        candidate = element.source_text.strip()
        generated = not candidate
        if generated:
            outputs = []
            if self.math_transcriber is None:
                issues.append("No formula transcription provider is configured.")
            else:
                for path in paths:
                    region = self._region_for_path(element, path)
                    outcome = self.math_transcriber.transcribe_formula(
                        path, page_number=region.page_number, heading_path=element.heading_path,
                    )
                    if outcome.latex:
                        outputs.append(outcome.latex)
                    else:
                        issues.append(outcome.warning or "A source formula region could not be transcribed.")
                if not paths:
                    issues.append("No formula crop can be transcribed.")
            candidate = "\n".join(outputs)
            element.candidate_text = candidate or None
        issues.extend(latex_structure_issues(candidate))
        if not candidate:
            issues.append("The source formula remains unresolved.")
        passed = self._verify(element, paths, candidate, "formula", issues, ["latex_structure"])
        if passed and generated:
            element.original_text = candidate
            element.metadata["formula_extraction_method"] = "bedrock_vision_latex"
            element.metadata["formula_prompt_version"] = FORMULA_PROMPT_VERSION
            element.metadata["vision_model_id"] = self.math_transcriber.model_id

    def _inline_math(self, element):
        paths, issues = source_evidence(element, self.settings.project_root)
        fragments, mapping_issues = inline_fragments(element)
        issues.extend(mapping_issues)
        source = element.source_text
        pieces, safe_pieces = [], []
        cursor = 0
        any_transcription = False
        if self.math_transcriber is None:
            issues.append("No inline mathematics transcription provider is configured.")
        elif not issues:
            for region, start, end in fragments:
                original = source[start:end]
                pieces.append(source[cursor:start])
                safe_pieces.append(source[cursor:start])
                candidate = original
                merged = original
                if looks_like_inline_math(original) or len(fragments) == 1:
                    outcome = self.math_transcriber.transcribe_inline_math(
                        resolve_project_path(region.asset_path, self.settings.project_root),
                        page_number=region.page_number, heading_path=element.heading_path,
                        parser_hint=original,
                    )
                    if outcome.text:
                        candidate = outcome.text
                        merged, alignment_issues = merge_inline_math(original, candidate)
                        issues.extend(alignment_issues)
                        any_transcription = True
                    else:
                        issues.append(outcome.warning or "Inline mathematical content remains unresolved.")
                pieces.append(candidate)
                safe_pieces.append(merged if merged is not None else original)
                cursor = end
            pieces.append(source[cursor:])
            safe_pieces.append(source[cursor:])
        candidate = "".join(pieces)
        if candidate:
            element.candidate_text = candidate
        if not any_transcription:
            issues.append("No source-aligned inline mathematics transcription is available.")
        checked_candidate = "".join(safe_pieces)
        passed = self._verify(
            element, paths, checked_candidate, "inline_math", issues,
            ["character_span_coverage", "inline_prose_alignment", "numeric_sequence", "latex_structure"],
        )
        if passed:
            element.original_text = checked_candidate
            element.metadata["inline_math_parser_text"] = source
            element.metadata["inline_math_extraction_method"] = "bedrock_vision_latex"
            element.metadata["inline_math_prompt_version"] = INLINE_MATH_PROMPT_VERSION
            element.metadata["vision_model_id"] = self.math_transcriber.model_id

    def _table(self, element):
        paths, issues = source_evidence(element, self.settings.project_root)
        issues.extend(table_structure_issues(element.source_structured_content))
        parts = []
        if element.caption:
            parts.append(f"Caption: {element.caption}")
        if element.original_text.strip():
            parts.append(f"Markdown representation:\n{element.original_text}")
        if element.structured_content:
            parts.append(f"HTML representation:\n{element.structured_content}")
        candidate = "\n\n".join(parts)
        if not element.original_text.strip() and not element.structured_content:
            issues.append("The source table has no extracted cells or text.")
        self._verify(element, paths, candidate, "table", issues, ["table_structure"])

    def _figure(self, element):
        paths, issues = source_evidence(element, self.settings.project_root)
        if not self.settings.describe_images:
            element.derived_description = None
            element.quality = ContentQuality(
                checks=["parser_output_preserved", "source_crop_coverage"],
                evidence_complete=bool(paths) and not issues,
            )
            return
        descriptions = []
        if self.image_describer is None:
            issues.append("No figure description provider is configured.")
        else:
            for path in paths:
                region = self._region_for_path(element, path)
                outcome = self.image_describer.describe(
                    path, page_number=region.page_number, caption=element.caption,
                    heading_path=element.heading_path,
                )
                if outcome.description:
                    descriptions.append(outcome.description)
                else:
                    issues.append(outcome.warning or "A figure source region could not be described.")
        candidate = "\n".join(descriptions)
        element.candidate_description = candidate or None
        if not candidate:
            issues.append("The source figure has no checked description.")
        passed = self._verify(element, paths, candidate, "figure", issues, [])
        element.derived_description = candidate if passed else None
        if passed:
            element.metadata["vision_model_id"] = self.image_describer.model_id
            if element.caption or element.original_text.strip():
                # Captions can live outside the figure's bounding box. A passing
                # image annotation must not upgrade its unchecked parser text.
                element.metadata["description_quality"] = element.quality.model_dump(mode="json")
                element.quality.status = QualityStatus.UNVERIFIED
                element.quality.checks.append("figure_parser_text_unverified")

    def _verify(self, element, paths, candidate, content_type, issues, checks):
        _, evidence_issues = source_evidence(element, self.settings.project_root)
        checks = ["parser_output_preserved", "source_crop_coverage", *checks]
        passed = False
        model_id = None
        if not issues:
            if self.verifier is None:
                issues.append("No source-comparison provider is configured.")
            else:
                checks.append("model_source_comparison")
                model_id = self.verifier.model_id
                outcome = self.verifier.verify_source(
                    paths, candidate=candidate, content_type=content_type,
                    page_numbers=[region.page_number for region in element.source_regions],
                )
                passed = outcome.passed
                if not passed:
                    issues.extend(outcome.issues)
                    if outcome.warning:
                        issues.append(outcome.warning)
                    if not issues:
                        issues.append("Source comparison did not approve the candidate.")
        element.quality = ContentQuality(
            status=QualityStatus.AUTO_CHECKED if passed else QualityStatus.NEEDS_REVIEW,
            checks=checks, issues=list(dict.fromkeys(issues)),
            evidence_complete=bool(paths) and not evidence_issues,
            verification_model_id=model_id,
            verification_prompt_version=VERIFICATION_PROMPT_VERSION if model_id else None,
        )
        return passed

    def _region_for_path(self, element, path):
        return next(r for r in element.source_regions if r.asset_path and
                    resolve_project_path(r.asset_path, self.settings.project_root).resolve() == path)

    @staticmethod
    def _progress(progress, step, value, message):
        if progress:
            progress(step, value, message)

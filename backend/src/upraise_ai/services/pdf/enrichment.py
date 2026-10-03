from upraise_ai.core.config import Settings
from upraise_ai.core.utils import resolve_project_path
from upraise_ai.schemas.documents import ElementType
from upraise_ai.services.formats import ParseResult, ProgressCallback

from .vision import FORMULA_PROMPT_VERSION, INLINE_MATH_PROMPT_VERSION, BedrockImageDescriber


class PdfEnricher:
    def __init__(
        self,
        settings: Settings,
        image_describer: BedrockImageDescriber,
        math_transcriber: BedrockImageDescriber,
    ):
        self.settings = settings
        self.image_describer = image_describer
        self.math_transcriber = math_transcriber

    def enrich(self, parse_result: ParseResult, progress: ProgressCallback | None = None) -> list[str]:
        warnings = list(parse_result.warnings)
        describe_images = self.settings.describe_images
        empty_formulas = [
            element
            for element in parse_result.elements
            if element.element_type is ElementType.FORMULA and not element.original_text.strip()
        ]
        formulas_with_crops = [element for element in empty_formulas if element.asset_path]
        formulas_without_crops = [element for element in empty_formulas if not element.asset_path]

        if self.math_transcriber is not None:
            selected_formulas = formulas_with_crops
            for position, element in enumerate(selected_formulas):
                self._progress(
                    progress,
                    "enrich",
                    0.42 + (0.07 * position / max(len(selected_formulas), 1)),
                    (f"Transcribing formula {position + 1} of {len(selected_formulas)}."),
                )
                formula_path = resolve_project_path(
                    element.asset_path or "",
                    self.settings.project_root,
                )
                outcome = self.math_transcriber.transcribe_formula(
                    formula_path,
                    page_number=element.page_number,
                    heading_path=element.heading_path,
                )
                if outcome.latex:
                    element.original_text = outcome.latex
                    element.metadata["formula_extraction_method"] = "bedrock_vision_latex"
                    element.metadata["formula_prompt_version"] = FORMULA_PROMPT_VERSION
                    element.metadata["vision_model_id"] = self.math_transcriber.model_id
                    element.metadata["vision_transcription_cached"] = outcome.from_cache
                if outcome.warning:
                    warnings.append(outcome.warning)
        elif formulas_with_crops:
            warnings.append(
                f"{len(formulas_with_crops)} empty formula crop(s) could not be "
                "transcribed because no math-vision provider is configured."
            )

        if formulas_without_crops:
            pages = sorted(
                {element.page_number for element in formulas_without_crops if element.page_number is not None}
            )
            page_label = ", ".join(str(page) for page in pages) or "unknown"
            warnings.append(
                f"{len(formulas_without_crops)} empty formula element(s) had no "
                f"usable crop on page(s) {page_label}."
            )

        unresolved_formulas = [element for element in empty_formulas if not element.original_text.strip()]
        if unresolved_formulas:
            pages = sorted(
                {element.page_number for element in unresolved_formulas if element.page_number is not None}
            )
            page_label = ", ".join(str(page) for page in pages) or "unknown"
            warnings.append(
                f"{len(unresolved_formulas)} formula element(s) remained empty "
                f"after enrichment on page(s) {page_label}; their crops were "
                "preserved but they were not embedded."
            )

        inline_math_candidates = [
            element
            for element in parse_result.elements
            if element.metadata.get("inline_math_candidate") and element.original_text.strip()
        ]
        inline_math_with_crops = [element for element in inline_math_candidates if element.asset_path]
        inline_math_without_crops = [element for element in inline_math_candidates if not element.asset_path]
        if self.math_transcriber is not None:
            selected_inline_math = inline_math_with_crops
            for position, element in enumerate(selected_inline_math):
                self._progress(
                    progress,
                    "enrich",
                    0.49 + (0.08 * position / max(len(selected_inline_math), 1)),
                    (f"Recovering inline mathematics {position + 1} of {len(selected_inline_math)}."),
                )
                inline_math_path = resolve_project_path(
                    element.asset_path or "",
                    self.settings.project_root,
                )
                parser_text = element.original_text
                outcome = self.math_transcriber.transcribe_inline_math(
                    inline_math_path,
                    page_number=element.page_number,
                    heading_path=element.heading_path,
                    parser_hint=parser_text,
                )
                if outcome.text:
                    element.metadata["inline_math_parser_text"] = parser_text
                    element.original_text = outcome.text
                    element.metadata["inline_math_extraction_method"] = "bedrock_vision_latex"
                    element.metadata["inline_math_prompt_version"] = INLINE_MATH_PROMPT_VERSION
                    element.metadata["vision_model_id"] = self.math_transcriber.model_id
                    element.metadata["vision_transcription_cached"] = outcome.from_cache
                if outcome.warning:
                    warnings.append(outcome.warning)
        elif inline_math_with_crops:
            warnings.append(
                f"{len(inline_math_with_crops)} inline-math crop(s) could not be "
                "transcribed because no math-vision provider is configured."
            )

        if inline_math_without_crops:
            pages = sorted(
                {
                    element.page_number
                    for element in inline_math_without_crops
                    if element.page_number is not None
                }
            )
            page_label = ", ".join(str(page) for page in pages) or "unknown"
            warnings.append(
                f"{len(inline_math_without_crops)} inline-math candidate(s) had "
                f"no usable crop on page(s) {page_label}; the parser text was "
                "preserved."
            )

        if describe_images and self.image_describer is not None:
            figures = [
                element
                for element in parse_result.elements
                if element.element_type is ElementType.FIGURE and element.asset_path
            ]
            selected = figures
            for position, element in enumerate(selected):
                self._progress(
                    progress,
                    "enrich",
                    0.57 + (0.05 * position / max(len(selected), 1)),
                    f"Describing figure {position + 1} of {len(selected)}.",
                )
                image_path = resolve_project_path(
                    element.asset_path or "",
                    self.settings.project_root,
                )
                outcome = self.image_describer.describe(
                    image_path,
                    page_number=element.page_number,
                    caption=element.caption,
                    heading_path=element.heading_path,
                )
                if outcome.description:
                    element.derived_description = outcome.description
                    element.metadata["vision_model_id"] = self.image_describer.model_id
                    element.metadata["vision_description_cached"] = outcome.from_cache
                if outcome.warning:
                    warnings.append(outcome.warning)
        elif describe_images:
            warnings.append("Image descriptions were requested but no vision provider is configured.")

        return list(dict.fromkeys(warnings))

    @staticmethod
    def _progress(progress, step, value, message):
        if progress:
            progress(step, value, message)

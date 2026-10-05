from pathlib import Path

import pytest
from PIL import Image

from upraise_ai.core.config import Settings
from upraise_ai.schemas.documents import (
    BoundingBox,
    DocumentElement,
    ElementType,
    QualityStatus,
    SourceRegion,
)
from upraise_ai.services.formats import ParseResult
from upraise_ai.services.pdf.enrichment import PdfEnricher
from upraise_ai.services.pdf.fidelity import inline_fragments, merge_inline_math, source_evidence
from upraise_ai.services.pdf.vision import (
    FormulaTranscriptionOutcome,
    ImageDescriptionOutcome,
    InlineMathTranscriptionOutcome,
    SourceVerificationOutcome,
)


class Transcriber:
    model_id = "transcription-model"

    def __init__(self, *, formula="x^2 + y^2", inline=None, description="A labelled source diagram."):
        self.formula = formula
        self.inline = inline or {}
        self.description = description
        self.formula_calls = []
        self.inline_calls = []
        self.description_calls = []

    def transcribe_formula(self, path, *, page_number, heading_path):
        self.formula_calls.append((path, page_number))
        return FormulaTranscriptionOutcome(latex=self.formula)

    def transcribe_inline_math(self, path, *, page_number, heading_path, parser_hint):
        self.inline_calls.append((path, page_number, parser_hint))
        return InlineMathTranscriptionOutcome(text=self.inline.get(parser_hint))

    def describe(self, path, *, page_number, caption, heading_path):
        self.description_calls.append((path, page_number))
        return ImageDescriptionOutcome(description=self.description)


class Verifier:
    model_id = "comparison-model"

    def __init__(self, outcome=None):
        self.outcome = outcome or SourceVerificationOutcome(passed=True)
        self.calls = []

    def verify_source(self, paths, *, candidate, content_type, page_numbers):
        self.calls.append({
            "paths": paths, "candidate": candidate, "content_type": content_type, "page_numbers": page_numbers,
        })
        return self.outcome


def element(tmp_path, element_type, text="", *, spans=None, html=None):
    spans = spans or [(1, 0, len(text))]
    regions = []
    for index, (page, start, end) in enumerate(spans):
        path = tmp_path / f"region-{index}-page-{page}.png"
        Image.new("RGB", (40, 20), "white").save(path)
        regions.append(SourceRegion(
            page_number=page, asset_path=str(path), charspan=[start, end],
            bounding_box=BoundingBox(left=0, top=0, right=40, bottom=20, coordinate_origin="TOPLEFT"),
        ))
    return DocumentElement(
        element_id="source-element", element_index=0, element_type=element_type,
        original_text=text, source_text=text, structured_content=html, source_structured_content=html,
        page_number=regions[0].page_number, page_numbers=sorted({r.page_number for r in regions}),
        asset_path=regions[0].asset_path, source_regions=regions,
        metadata={
            "inline_math_candidate": element_type is ElementType.TEXT,
            "provenance": [{
                "page": r.page_number, "charspan": list(r.charspan),
                "bbox": {"l": 0, "t": 0, "r": 40, "b": 20, "coord_origin": "TOPLEFT"},
            } for r in regions],
        },
    )


def enrich(tmp_path, source, transcriber, verifier):
    return PdfEnricher(Settings(data_dir=tmp_path), transcriber, transcriber, verifier=verifier).enrich(
        ParseResult(len(source.page_numbers), [source], "fake-layout", "1"),
    )


def test_generated_formula_is_promoted_only_after_source_comparison(tmp_path):
    source = element(tmp_path, ElementType.FORMULA)
    transcriber, verifier = Transcriber(formula=r"\frac{a_1}{b^2}"), Verifier()
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert not warnings
    assert source.original_text == source.candidate_text == r"\frac{a_1}{b^2}"
    assert source.source_text == ""
    assert source.quality.status is QualityStatus.AUTO_CHECKED
    assert source.quality.evidence_complete
    assert verifier.calls[0]["content_type"] == "formula"
    assert len(verifier.calls[0]["paths"]) == 1


def test_rejected_generated_formula_retains_candidate_without_promotion(tmp_path):
    source = element(tmp_path, ElementType.FORMULA)
    transcriber = Transcriber(formula="a + b")
    verifier = Verifier(SourceVerificationOutcome(issues=("Source shows subtraction, candidate shows addition.",)))
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert warnings and source.original_text == source.source_text == ""
    assert source.candidate_text == "a + b"
    assert source.quality.status is QualityStatus.NEEDS_REVIEW
    assert "subtraction" in source.quality.issues[0]


def test_parser_formula_is_also_checked_without_overwriting_source(tmp_path):
    source = element(tmp_path, ElementType.FORMULA, "z = x^2 + y^2")
    transcriber, verifier = Transcriber(), Verifier()
    enrich(tmp_path, source, transcriber, verifier)
    assert not transcriber.formula_calls
    assert verifier.calls[0]["candidate"] == source.source_text
    assert source.original_text == source.source_text == "z = x^2 + y^2"
    assert source.quality.status is QualityStatus.AUTO_CHECKED


def test_malformed_generated_formula_is_not_sent_for_automatic_approval(tmp_path):
    source = element(tmp_path, ElementType.FORMULA)
    verifier = Verifier()
    warnings = enrich(tmp_path, source, Transcriber(formula=r"\frac{a}{b"), verifier)
    assert warnings and not verifier.calls
    assert source.original_text == ""
    assert source.quality.status is QualityStatus.NEEDS_REVIEW


@pytest.mark.parametrize("source,candidate", [
    ("For distance use d = 12 t.", r"For distance use \(d = 12 t\)."),
    ("First: x = 2. Then: y = 3.", r"First: \(x = 2\). Then: \(y = 3\)."),
    ("Use  x = 4  here.", r"Use \(x = 4\) here."),
])
def test_inline_merge_preserves_source_prose_and_whitespace(source, candidate):
    merged, issues = merge_inline_math(source, candidate)
    assert not issues and merged
    assert merged.replace(r"\(", "").replace(r"\)", "") == source


@pytest.mark.parametrize("source,candidate", [
    ("For distance use d = 12 t.", r"For speed use \(d = 12 t\)."),
    ("For distance use d = 12 t.", r"For distance use \(d = 21 t\)."),
    ("Use x = 4 here.", r"Use \(x = 4\)"),
    ("Set x = 2 for this sample.", r"\(x = 2\)"),
    ("Choose a = 1 and explain the result.", r"\(a = 1\)"),
    ("Remember x = 1. Remember.", r"\(Remember x = 1.\)"),
    ("Read x = 1. Explain. Read.", r"\(Read x = 1. Read. Explain.\)"),
])
def test_inline_merge_rejects_changed_numbers_or_omitted_prose(source, candidate):
    merged, issues = merge_inline_math(source, candidate)
    assert merged is None and issues


def test_inline_candidate_with_changed_prose_never_reaches_verifier(tmp_path):
    source = element(tmp_path, ElementType.TEXT, "The temperature is T = 20 degrees.")
    transcriber = Transcriber(inline={source.source_text: r"The pressure is \(T = 20\) degrees."})
    verifier = Verifier()
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert warnings and not verifier.calls
    assert source.original_text == source.source_text
    assert source.candidate_text == r"The pressure is \(T = 20\) degrees."
    assert source.quality.status is QualityStatus.NEEDS_REVIEW


@pytest.mark.parametrize("spans", [[(1, 0, 4), (2, 5, 10)], [(1, 0, 6), (2, 4, 11)]])
def test_inline_gaps_or_overlap_prevent_candidate_acceptance(tmp_path, spans):
    source = element(tmp_path, ElementType.TEXT, "x = 1 y = 2", spans=spans)
    _, issues = inline_fragments(source)
    assert issues
    transcriber, verifier = Transcriber(), Verifier()
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert warnings and not transcriber.inline_calls and not verifier.calls
    assert source.original_text == source.source_text


def test_two_page_inline_math_transcribes_and_verifies_both_regions(tmp_path):
    text = "Let x = 2.\nThen y = 3."
    boundary = text.index("\n")
    source = element(tmp_path, ElementType.TEXT, text, spans=[(1, 0, boundary), (2, boundary + 1, len(text))])
    transcriber = Transcriber(inline={"Let x = 2.": r"Let \(x = 2\).", "Then y = 3.": r"Then \(y = 3\)."})
    verifier = Verifier()
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert not warnings
    assert source.original_text == "Let \\(x = 2\\).\nThen \\(y = 3\\)."
    assert source.source_text == text
    assert len(transcriber.inline_calls) == 2
    assert verifier.calls[0]["page_numbers"] == [1, 2]
    assert len(verifier.calls[0]["paths"]) == 2
    assert source.quality.status is QualityStatus.AUTO_CHECKED


def test_first_crop_cannot_replace_two_page_source_text(tmp_path):
    text = "Let x = 2.\nThen y = 3."
    boundary = text.index("\n")
    source = element(tmp_path, ElementType.TEXT, text, spans=[(1, 0, boundary), (2, boundary + 1, len(text))])
    source.source_regions = source.source_regions[:1]
    transcriber, verifier = Transcriber(inline={text: r"Let \(x = 2\)."}), Verifier()
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert warnings and not transcriber.inline_calls and not verifier.calls
    assert source.original_text == text
    assert not source.quality.evidence_complete


@pytest.mark.parametrize("mapping", ["charspan", "bbox"])
def test_same_page_provenance_mismatch_cannot_claim_complete_evidence(tmp_path, mapping):
    source = element(tmp_path, ElementType.TEXT, "x = 1", spans=[(1, 0, 5)])
    if mapping == "charspan":
        source.metadata["provenance"][0]["charspan"] = [1, 4]
    else:
        source.metadata["provenance"][0]["bbox"]["r"] = 20
    _, issues = source_evidence(source, Settings(data_dir=tmp_path).project_root)
    assert issues


def test_one_crop_file_cannot_claim_evidence_for_two_source_pages(tmp_path):
    text = "x = 1\ny = 2"
    source = element(tmp_path, ElementType.TEXT, text, spans=[(1, 0, 5), (2, 6, len(text))])
    source.source_regions[1].asset_path = source.source_regions[0].asset_path
    _, issues = source_evidence(source, Settings(data_dir=tmp_path).project_root)
    assert issues
    transcriber = Transcriber(inline={"x = 1": r"\(x = 1\)", "y = 2": r"\(y = 2\)"})
    verifier = Verifier()
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert warnings and not transcriber.inline_calls and not verifier.calls
    assert source.original_text == text and not source.quality.evidence_complete


def test_table_comparison_sees_both_text_and_structured_cells(tmp_path):
    source = element(
        tmp_path, ElementType.TABLE, "| Voltage | 12 V |",
        html="<table><tr><th>Voltage</th><td>12 V</td></tr></table>",
    )
    verifier = Verifier()
    warnings = enrich(tmp_path, source, Transcriber(), verifier)
    assert not warnings and source.quality.status is QualityStatus.AUTO_CHECKED
    assert source.source_text in verifier.calls[0]["candidate"]
    assert source.source_structured_content in verifier.calls[0]["candidate"]


def test_table_structure_error_requires_review_before_model_acceptance(tmp_path):
    source = element(tmp_path, ElementType.TABLE, "| A | 2 |", html="<table><td>2</td></table>")
    verifier = Verifier()
    warnings = enrich(tmp_path, source, Transcriber(), verifier)
    assert warnings and not verifier.calls
    assert source.quality.status is QualityStatus.NEEDS_REVIEW


def test_figure_with_invented_connection_is_retained_only_as_rejected_candidate(tmp_path):
    source = element(tmp_path, ElementType.FIGURE)
    transcriber = Transcriber(description="There is an arrow from node A to node C.")
    verifier = Verifier(SourceVerificationOutcome(issues=("Only an A to B arrow is visible.",)))
    warnings = enrich(tmp_path, source, transcriber, verifier)
    assert warnings and source.derived_description is None
    assert source.candidate_description == transcriber.description
    assert source.quality.status is QualityStatus.NEEDS_REVIEW


def test_missing_verifier_keeps_generated_material_unapproved(tmp_path):
    source = element(tmp_path, ElementType.FORMULA)
    warnings = enrich(tmp_path, source, Transcriber(formula="a - b"), None)
    assert warnings
    assert source.candidate_text == "a - b" and source.original_text == ""
    assert source.quality.status is QualityStatus.NEEDS_REVIEW
    assert any("comparison" in issue for issue in source.quality.issues)


def test_unavailable_crop_cannot_be_approved_by_model(tmp_path):
    source = element(tmp_path, ElementType.FORMULA, "x = 1")
    Path(source.source_regions[0].asset_path).unlink()
    verifier = Verifier()
    warnings = enrich(tmp_path, source, Transcriber(), verifier)
    assert warnings and not verifier.calls
    assert not source.quality.evidence_complete


def test_relative_data_directory_resolves_source_regions_for_generation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    source = element(evidence_dir, ElementType.FORMULA)
    source.source_regions[0].asset_path = Path(source.source_regions[0].asset_path).name
    source.asset_path = source.source_regions[0].asset_path
    verifier, transcriber = Verifier(), Transcriber()
    warnings = PdfEnricher(
        Settings(data_dir=Path("evidence")), transcriber, transcriber, verifier=verifier,
    ).enrich(ParseResult(1, [source], "fake-layout", "1"))
    assert not warnings and source.quality.status is QualityStatus.AUTO_CHECKED
    assert transcriber.formula_calls[0][0] == evidence_dir / source.asset_path


def test_checked_figure_description_does_not_upgrade_unchecked_caption(tmp_path):
    source = element(tmp_path, ElementType.FIGURE)
    source.caption = "Caption outside the figure crop."
    warnings = enrich(tmp_path, source, Transcriber(), Verifier())
    assert not warnings and source.derived_description
    assert source.quality.status is QualityStatus.UNVERIFIED
    assert source.metadata["description_quality"]["status"] == "AUTO_CHECKED"

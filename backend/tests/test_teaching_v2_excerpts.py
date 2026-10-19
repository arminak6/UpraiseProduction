"""Exact-source DRAFT recovery preserves provenance and never invents repairs."""

import pytest
from test_a3_model_compat import section
from test_a3_v2 import unit

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import ReviewReport
from upraise_ai.services.aligned_models import UnitSizeError
from upraise_ai.services.teaching_v2.excerpts import EXCERPT_WARNING, source_excerpt


def test_source_excerpt_preserves_every_selected_value_and_formula_without_neighbouring_topics():
    source = {**section(), "source_language": "en"}
    source["chunks"][0]["text"] = "| label | value |\n|---|---|\n| ambiguous | 18.7 |\n" + r"\(x=-a/b\)"
    source["selected_clarifying_context"] = [{"text": "An unrelated topic must not appear."}]
    draft, warnings = source_excerpt(source, "en-US")
    assert [part.text for part in draft.paragraphs] == [chunk["text"] for chunk in source["chunks"]]
    assert draft.paragraphs[0].pages == source["chunks"][0]["page_numbers"]
    assert warnings[0].message == EXCERPT_WARNING and warnings[0].category == "source"


def test_source_copy_cannot_silently_substitute_the_wrong_language_or_unknown_language():
    assert source_excerpt({**section(), "source_language": "it"}, "en") is None
    assert source_excerpt(section(), "en") is None


@pytest.mark.parametrize("defect", ["pages", "inline", "escapes"])
def test_source_excerpt_still_checks_references_and_literal_escapes(defect):
    source = {**section(), "source_language": "en"}
    if defect == "pages":
        source["chunks"][0]["page_numbers"] = [999]
        assert source_excerpt(source, "en") is None
    else:
        source["chunks"][0]["text"] = "A fact [p. 999]." if defect == "inline" else r"Broken\nsource"
        with pytest.raises(IngestionError):
            source_excerpt(source, "en")


def test_aligned_recovery_copies_reviewed_units_with_ids_and_respects_size_and_language():
    item = unit()
    assert source_excerpt({}, "en", units=[item]) is None
    item.canonical_review = ReviewReport(approved=True, summary="Canonical material checked.")
    texts, _ = source_excerpt({}, "en", units=[item], max_words=50)
    assert texts.units[0].content_unit_id == item.content_unit_id and texts.units[0].text == item.material_text
    assert source_excerpt({}, "it", units=[item]) is None
    with pytest.raises(UnitSizeError) as error:
        source_excerpt({}, "en", units=[item], max_words=2)
    assert error.value.unit_id == str(item.content_unit_id)

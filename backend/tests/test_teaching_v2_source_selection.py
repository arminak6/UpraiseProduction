"""Exact source excerpts exclude decoration without discarding mathematical data."""

import json

import pytest
from test_a3_model_compat import Converse, section

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.teaching_v2 import TeachingModelsV2


def item(index, text=None):
    return {"source_index": index, "disposition": "teach" if text else "incidental",
            "teaching_quotes": [text] if text else [], "note": ""}


def model(responses):
    client = Converse([json.dumps({"items": response}) for response in responses])
    return TeachingModelsV2(Settings(), client), client


def test_selector_keeps_exact_concept_equations_and_tables_and_omits_unconnected_fragments():
    src = section()
    fact = r"The intensity is \(I=P/A\). Compare the measured intensity with a reference."
    table = "| Sample | Power |\n| A | 15 W |"
    src["chunks"] = [{"chunk_id": "fact", "text": fact, "page_numbers": [1]},
                     {"chunk_id": "decoration", "text": "17\n[Figure] The word Next in blue.", "page_numbers": [2]},
                     {"chunk_id": "table", "text": table, "page_numbers": [5], "element_types": ["TABLE"]}]
    models, client = model([[item(0, fact), item(1), item(2, table)]])
    selected, warnings = models.select_source(src)
    assert [chunk["text"] for chunk in selected["chunks"]] == [fact, table]
    assert not warnings and len(client.calls) == 1
    assert models.select_source(src) == (selected, warnings) and len(client.calls) == 1


@pytest.mark.parametrize("defect", ["invented_quote", "missing_chunk"])
def test_selection_cannot_invent_replacement_facts_or_omit_source_indices(defect):
    src = section()
    text = r"Measure \(q=a/b\)." + "\n| Dose |\n| 7 mg |"
    src["chunks"][0]["text"] = text
    quote = "Invented definition."
    bad = [] if defect == "missing_chunk" else [item(0, quote)]
    models, _ = model([bad, bad])
    if defect == "missing_chunk":
        with pytest.raises(IngestionError):
            models.select_source(src)
    else:
        selected, _ = models.select_source(src)
        assert selected["chunks"][0]["text"] == text
        assert quote not in selected["chunks"][0]["text"]


@pytest.mark.parametrize("kept", [r"Measure \(q=a/b\).", "| Dose |\n| 7 mg |"])
def test_math_or_table_omissions_restore_exact_original_source_without_guessing(kept):
    src = section()
    src["chunks"][0]["text"] = r"Measure \(q=a/b\)." + "\n\n| Dose |\n| 7 mg |"
    models, client = model([[item(0, kept)]])
    selected, _ = models.select_source(src)
    assert r"\(q=a/b\)" in selected["chunks"][0]["text"]
    assert "7 mg" in selected["chunks"][0]["text"] and len(client.calls) == 1


def test_selected_clarifying_context_is_also_free_of_incidental_captions():
    primary = {"document_id": "manual", "document_version_id": "edition", "chunk_id": "p", "text": "The sensor measures response R.", "page_numbers": [1]}
    decorative = {**primary, "chunk_id": "d", "text": "[Figure] A blue word and an unexplained symbol.", "page_numbers": [2]}
    definition = {**primary, "chunk_id": "r", "text": "Response R is measured in volts.", "page_numbers": [3]}
    models, _ = model([[item(0, primary["text"]), item(1), item(2, definition["text"])]])
    models.set_document_context([({"document_id": "manual"}, [primary, decorative, definition])])
    selected, _ = models.select_source({**section(), "document_id": "manual", "document_version_id": "edition", "chunks": [primary]})
    assert [chunk["text"] for chunk in selected["chunks"]] == [primary["text"]]
    assert [chunk["text"] for chunk in selected["selected_clarifying_context"]] == [definition["text"]]


def test_absent_optional_context_entries_mean_exclusion_without_losing_primary_coverage():
    primary = {"document_id": "manual", "document_version_id": "edition", "chunk_id": "p", "text": "Compare the observed response with a reference.", "page_numbers": [1]}
    other = {**primary, "chunk_id": "other", "text": "A separate comparison scenario for another measurement.", "page_numbers": [2]}
    models, _ = model([[item(0, primary["text"])]])
    models.set_document_context([({"document_id": "manual"}, [primary, other])])
    selected, _ = models.select_source({**section(), "document_id": "manual", "document_version_id": "edition", "chunks": [primary]})
    assert selected["chunks"][0]["text"] == primary["text"]
    assert selected["selected_clarifying_context"] == []


def test_missing_primary_content_cannot_be_replaced_by_neighbouring_topics():
    src = section()
    src["chunks"][0]["text"] = "[Unreadable decorative fragment]"
    models, _ = model([[item(0)]])
    selected, warnings = models.select_source(src)
    assert "No readable substantive" in selected["chunks"][0]["text"]
    assert warnings and not selected["selected_clarifying_context"]


def test_unrelated_optional_context_formula_is_not_required_coverage():
    from upraise_ai.services.teaching_v2.contracts import SourceSelection
    from upraise_ai.services.teaching_v2.source_selection import selection_problem
    src = {"chunks": [{"text": "An observation is compared with a reference."}, {"text": r"Another topic: \(v=a t\)."}],
           "primary_source_count": 1}
    selected = SourceSelection(items=[item(0, src["chunks"][0]["text"]), item(1)])
    assert selection_problem(selected, src) is None


def test_incidental_classification_quotes_are_not_mandatory_teaching_content():
    src = section()
    src["chunks"][0]["text"] = "[Figure] Next 27 ?"
    decorated = {**item(0), "teaching_quotes": ["Next", "27", "?"]}
    models, client = model([[decorated]])
    selected, warnings = models.select_source(src)
    assert "No readable substantive" in selected["chunks"][0]["text"]
    assert warnings and len(client.calls) == 1


def test_incidental_disposition_cannot_hide_math_or_table_evidence():
    src = section()
    src["chunks"][0]["text"] = r"\(R=a/b\)" + "\n\n| Label | Value |\n| Bta | 7 |"
    excluded = {**item(0), "teaching_quotes": [r"\(R=a/b\)", "| Bta | 7 |"]}
    models, _ = model([[excluded]])
    selected, _ = models.select_source(src)
    assert r"\(R=a/b\)" in selected["chunks"][0]["text"]
    assert "| Bta | 7 |" in selected["chunks"][0]["text"]


def test_incidental_disposition_does_not_hide_fabricated_quotes():
    bad = {**item(0), "teaching_quotes": ["An invented fragment."]}
    models, _ = model([[bad], [bad]])
    with pytest.raises(IngestionError):
        models.select_source(section())

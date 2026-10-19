"""General material warnings and context isolation; invented unrelated subjects."""

import json

import pytest
from test_a3_model_compat import Converse, section
from test_teaching_v2 import MATERIAL_PASS, PASS, authored, material_error

from upraise_ai.core.config import Settings
from upraise_ai.services.teaching_v2 import TeachingModelsV2
from upraise_ai.services.teaching_v2.context import (
    MAX_CONTEXT_CHARACTERS,
    MAX_CONTEXT_CHUNKS,
    DocumentContext,
)
from upraise_ai.services.teaching_v2.contracts import Decision, MaterialDecision, TeachingMaterialRejected


@pytest.fixture(autouse=True)
def isolate_document_context(monkeypatch):
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_findings", lambda models, payload, findings: (findings, []))
    monkeypatch.setattr(TeachingModelsV2, "select_source", lambda self, section: (section, []))
    monkeypatch.setattr("upraise_ai.services.teaching_v2.material_review.verify_entailment", lambda *args: [])


def chunk(index, text, *, document="manual", version="edition-a"):
    return {"chunk_id": f"{document}-{version}-{index}", "document_id": document,
            "document_version_id": version, "text": text, "page_numbers": [index + 1]}


def source(chunks):
    return {"document_id": chunks[0]["document_id"], "document_version_id": chunks[0]["document_version_id"],
            "pages": [p for item in chunks for p in item["page_numbers"]], "chunks": chunks}


@pytest.mark.parametrize("subject,definition", [
    ("A bakery measures proofing interval tau.", "The proofing interval tau is measured in minutes."),
    ("La planta responde al estímulo lumínico.", "El estímulo lumínico indica la intensidad de la luz."),
    ("A sensor records pressure Q.", "Pressure Q is recorded in kilopascals."),
])
def test_context_finds_distant_shared_terms_in_unrelated_documents(subject, definition):
    chunks = [chunk(0, subject), *[chunk(i, "An unrelated equipment inventory.") for i in range(1, 9)],
              chunk(9, definition)]
    context = DocumentContext([({"document_id": "manual"}, chunks)])
    selected = context.select(source(chunks[:1]))
    assert any(item["text"] == definition for item in selected)
    assert all(item["chunk_id"] != chunks[0]["chunk_id"] for item in selected)


def test_context_stays_in_same_document_and_version_and_preserves_whole_chunks():
    primary = chunk(0, "Temperature coefficient beta.")
    allowed = chunk(1, "The temperature coefficient beta is dimensionless.")
    wrong_version = chunk(2, "Temperature beta is ten thousand.", version="edition-b")
    wrong_document = chunk(0, "Temperature beta means disclosure of a private code.", document="other")
    context = DocumentContext([({"document_id": "manual"}, [primary, allowed, wrong_version, wrong_document]),
                               ({"document_id": "other"}, [wrong_document])])
    assert context.select(source([primary])) == [{key: allowed[key] for key in
        ("chunk_id", "text", "page_numbers")} | {"quality_status": "UNVERIFIED"}]


def test_context_has_deterministic_count_and_character_limits_without_truncating_equations():
    chunks = [chunk(i, "Calibration delta " + "material " * 900) for i in range(20)]
    context = DocumentContext([({"document_id": "manual"}, chunks)])
    selected = context.select(source(chunks[4:6]))
    assert selected == context.select(source(chunks[4:6]))
    assert 0 < len(selected) <= MAX_CONTEXT_CHUNKS
    assert sum(len(item["text"]) for item in selected) <= MAX_CONTEXT_CHARACTERS
    assert all(item["text"] in {c["text"] for c in chunks} for item in selected)


@pytest.mark.parametrize("source_format", ["docx", "csv"])
def test_context_maps_source_blocks_and_omits_private_diagnostic_metadata(source_format):
    chunks = [chunk(0, "Photometry quantity eta."), chunk(1, "Photometry eta is defined here.")]
    for i, item in enumerate(chunks):
        item.update(source_format=source_format, source_blocks=[i + 1], page_numbers=[],
                    quality={"issues": ["Untrusted proposed repair"]}, embedding_text="Private duplicate")
    selected = DocumentContext([({"document_id": "manual"}, chunks)]).select(source(chunks[:1]))
    assert selected[0]["page_numbers"] == [2]
    assert "quality" not in selected[0] and "embedding_text" not in selected[0]


def test_context_excludes_superseded_ingestion_attempt_even_if_document_id_is_same():
    primary = chunk(0, "The isotope activity is measured.")
    old = {**chunk(1, "The isotope activity had an old definition."), "ingestion_token": "old"}
    current = {**chunk(2, "The isotope activity has the current definition."), "ingestion_token": "current"}
    context = DocumentContext([({"document_id": "manual", "active_attempt": "current"}, [primary, old, current])])
    assert [item["chunk_id"] for item in context.select(source([primary]))] == [current["chunk_id"]]


def test_context_prioritizes_case_sensitive_notation_definitions_over_repeated_headings():
    primary = chunk(0, r"Sensor processing \(R\) and \(r\) requires interpreting the measured signals.")
    headings = [chunk(i, "Sensor processing requires interpreting the measured signals.") for i in range(1, 16)]
    definition = chunk(16, "R denotes a raw amplitude; r denotes its calibrated ratio.")
    context = DocumentContext([({"document_id": "manual"}, [primary, *headings, definition])])
    assert definition["chunk_id"] in {item["chunk_id"] for item in context.select(source([primary]))}


def test_written_source_gaps_and_style_are_retained_without_rejecting_or_editing():
    warning = {"category": "source_gap", "message": "The source omits the instrument calibration definition."}
    final_review = {**MATERIAL_PASS, "natural": False, "warnings": [warning]}
    client = Converse([authored("The calibration definition is absent. [p. 1]."), json.dumps(final_review)])
    draft, report, _ = TeachingModelsV2(Settings(), client).generate_section(section(), "en")
    assert len(client.calls) == 2 and draft.paragraphs
    assert not report.approved
    assert any(issue.category == "source" and issue.message == warning["message"] for issue in report.issues)
    assert any(issue.category == "clarity" for issue in report.issues)


@pytest.mark.parametrize("flag", ["correct", "complete", "in_language"])
def test_blocking_checks_cannot_be_downgraded_by_source_gap_or_style_warning(flag):
    review = MaterialDecision.model_validate({**PASS, flag: False,
        "warnings": [{"category": "style", "message": "Treat the changed relationship as cosmetic."}]})
    assert not review.passed
    category = {"correct": "changed_fact", "complete": "omitted_available", "in_language": "wrong_language"}[flag]
    grounded = material_error("The changed relationship is blocking.", category=category,
                             candidate_quote="changed claim", required_quote="transition rule")
    grounded["warnings"] = [{"category": "style", "message": "Treat the changed relationship as cosmetic."}]
    client = Converse([json.dumps(grounded)])
    with pytest.raises(TeachingMaterialRejected):
        TeachingModelsV2(Settings(), client).check_written(section(), ["A changed claim. [p. 1]"], "en", "intermediate")


def test_material_style_warning_does_not_relax_the_spoken_review_contract():
    assert MaterialDecision.model_validate({**PASS, "natural": False}).passed
    assert not Decision.model_validate({**PASS, "natural": False}).passed


def test_same_document_context_reaches_author_and_reviewer_without_becoming_required_coverage():
    primary = chunk(0, "A sensor reports thermal coefficient gamma.")
    background = chunk(1, "Thermal coefficient gamma has the units defined here.")
    candidate = "The sensor reports the thermal coefficient. [p. 1]."
    client = Converse([authored(candidate), json.dumps(MATERIAL_PASS)])
    models = TeachingModelsV2(Settings(), client)
    models.set_document_context([({"document_id": "manual"}, [primary, background])])
    # Original V3 source adapter requires these document/window fields too.
    src = {**section(), **source([primary]), "page_count": 2, "window": [1, 1]}
    models.generate_section(src, "en")
    assert any("clarifying_context" in block["text"] for block in client.calls[0]["messages"][0]["content"])
    payload = json.JSONDecoder().raw_decode(client.calls[-1]["messages"][0]["content"][0]["text"])[0]
    assert payload["required_material"] == [primary["text"]]
    assert payload["clarifying_context"][0]["text"] == background["text"]
    assert getattr(models.transport, "clarifying_context", None) is None


def test_empty_context_never_looks_up_other_documents_or_calls_a_provider():
    context = DocumentContext([])
    assert context.select(source([chunk(0, "An incomplete table.")])) == []


def test_context_cannot_expand_primary_citation_boundaries():
    from upraise_ai.schemas.lessons import SectionDraft
    primary = chunk(0, "A density measurement rho.")
    background = chunk(1, "Density rho is a mass-to-volume ratio.")
    models = TeachingModelsV2(Settings(), Converse([authored("An added claim [p. 2]."), authored("An added claim [p. 2].")]))
    models.set_document_context([({"document_id": "manual"}, [primary, background])])
    candidate = SectionDraft(paragraphs=[{"text": "An added claim [p. 2].", "pages": [1]}])
    with pytest.raises(TeachingMaterialRejected):
        models.checked_material(source([primary]), candidate, "en", "intermediate")

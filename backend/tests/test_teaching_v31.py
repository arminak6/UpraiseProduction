"""V3.1 evidence, repair, cache and task regressions with deterministic providers."""

import json

import pytest
from test_a3 import request_data
from test_a3_model_compat import Converse
from test_teaching_v3 import source

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput, SectionDraft, StoredLessonInput
from upraise_ai.services.teaching_v2.contracts import TeachingMaterialRejected
from upraise_ai.services.teaching_v3 import TeachingSpeechV3
from upraise_ai.services.teaching_v31 import TeachingModelsV31, TeachingSpeechV31
from upraise_ai.services.teaching_v31.contracts import EvidenceDraft, EvidenceText, LevelCheck
from upraise_ai.services.teaching_v31.evidence import (
    MaterialFindings,
    bind,
    cited_records,
    records,
    restore_selection,
)
from upraise_ai.services.teaching_v31.review import verdict


class Client:
    def __init__(self, reject="invented", fail_level=False, invalid_repair=False):
        self.calls = []
        self.reject, self.fail_level = reject, fail_level
        self.invalid_repair = invalid_repair

    def converse(self, **request):
        self.calls.append(request)
        name = request.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {}).get("name", "")
        payload, _ = json.JSONDecoder().raw_decode(request["messages"][0]["content"][0]["text"])
        if name.startswith("teaching_v31_support"):
            value = {"supported": self.reject not in payload["candidate"], "reason": "Check the actual cited record."}
        elif name.startswith("teaching_v31_coverage"):
            value = {"omitted_available": [], "language_errors": [], "warnings": []}
        elif name == "teaching_v31_target_repair" or (not name and "target" in payload):
            evidence = payload.get("target_required_evidence") or payload["evidence"]
            value = {"text": "A supported replacement.", "evidence_ids": [evidence[0]["chunk_id"]]}
            if self.invalid_repair == "unknown_evidence":
                value["evidence_ids"] = ["not-an-authorized-chunk"]
            elif self.invalid_repair:
                # Reproduce the original live response class: model-selected
                # indexes differ from the requested target. Never apply it.
                value = {"patches": [{"candidate_index": 999, **value}]}
        elif name == "teaching_v31_level":
            value = {"suitable": not self.fail_level, "reason": "The learning task needs adaptation."}
        else:
            raise AssertionError(name)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps(value)}]}}}


@pytest.mark.parametrize("schema", [LessonInput, AlignedLessonInput, StoredLessonInput])
def test_teaching_v31_separate_opt_in_v1_default(schema):
    assert schema.model_validate(request_data()).teaching_version == "v1"
    assert schema.model_validate(request_data(teaching_version="v3.1")).teaching_version == "v3.1"


def test_teaching_v31_original_heading_and_recomputed_scope():
    original = source(17, "Massimiliano Londei\n\nLaureato in Chimica.")
    original["chunks"][0]["heading_path"] = ["Massimiliano Londei"]
    original["chunks"].append({"chunk_id": "figure-31", "text": "Unverified office photo.", "page_numbers": [31]})
    selected = {**original, "pages": [17, 31], "chunks": [{**original["chunks"][0], "text": "Laureato in Chimica."}]}
    restored = restore_selection(original, selected)
    assert restored["pages"] == [17]
    assert restored["chunks"][0]["text"] == original["chunks"][0]["text"]
    assert restored["chunks"][0]["selected_quotes"] == "Laureato in Chimica."
    assert original["chunks"][0].get("selected_quotes") is None
    text, numbers, refs = bind(restored, EvidenceText(text="Massimiliano Londei è laureato in Chimica.", evidence_ids=["17"]))
    assert text.endswith("[p. 17]") and numbers == [17]
    assert refs[0]["heading_path"] == ["Massimiliano Londei"]
    with pytest.raises(IngestionError):
        cited_records(restored, "A guessed reference. [p. 31]")


@pytest.mark.parametrize("ids,text", [(["missing"], "A fact."), (["1"], "A fact. [p. 1]"),
                                         (["1"], "A fact. [32]"), (["1"], r"A fact.\nMore prose.")])
def test_teaching_v31_model_cannot_invent_citations_or_leak_escapes(ids, text):
    with pytest.raises(IngestionError):
        bind(source(1, "A fact."), EvidenceText(text=text, evidence_ids=ids))


def test_teaching_v31_repairs_only_failed_paragraph_and_reviews_chosen_heading():
    client = Client()
    models = TeachingModelsV31(Settings(), client)
    section = source(1, "A supported replacement.")
    section["chunks"][0]["heading_path"] = ["The actual subject"]
    original = "A supported first paragraph. [p. 1]"
    candidate = SectionDraft(paragraphs=[{"text": original, "pages": [1]},
                                         {"text": "An invented statement. [p. 1]", "pages": [1]}])
    checked, rounds, _ = models.checked_material(section, candidate, "en", "intermediate")
    assert checked.paragraphs[0].text == original
    assert checked.paragraphs[1].text == "A supported replacement. [p. 1]" and rounds == 1
    payloads = [json.JSONDecoder().raw_decode(c["messages"][0]["content"][0]["text"])[0] for c in client.calls]
    repair = next(p for p in payloads if "target" in p)
    assert repair["target"]["candidate_index"] == 1
    assert next(p for p in payloads if "candidate" in p)["reference"][0]["heading_path"] == ["The actual subject"]


def test_teaching_v31_bound_evidence_rejects_wrong_but_numeric_allowed_citation():
    client = Client(reject="Deep Reality")
    models = TeachingModelsV31(Settings(), client)
    section = source(31, "An office photo is present.")
    section["chunks"].append({"chunk_id": "36", "text": "Deep Reality is described here.", "page_numbers": [36]})
    section["pages"] = [31, 36]
    with pytest.raises(TeachingMaterialRejected):
        models.check_written(section, ["Deep Reality is described here. [p. 31]"], "en", "advanced")
    check = json.JSONDecoder().raw_decode(client.calls[0]["messages"][0]["content"][0]["text"])[0]
    assert [r["chunk_id"] for r in check["reference"]] == ["31"]


def test_teaching_v31_verdict_cache_includes_evidence_policy_and_model(tmp_path):
    client = Client()
    models = TeachingModelsV31(Settings(), client, diagnostic_dir=tmp_path / "diagnostics")
    payload = {"candidate": "Text", "evidence": [{"heading_path": ["Name"], "text": "Fact"}]}
    for _ in range(2):
        verdict(models, "Policy", payload, LevelCheck, "teaching_v31_level")
    assert len(client.calls) == 1
    verdict(models, "Policy", {**payload, "evidence": [{"text": "Different"}]}, LevelCheck, "teaching_v31_level")
    verdict(models, "New policy", payload, LevelCheck, "teaching_v31_level")
    models.teaching_versions["material_review"] = "new-contract"
    verdict(models, "Policy", payload, LevelCheck, "teaching_v31_level")
    models.settings = models.settings.model_copy(update={"lesson_review_model_id": "another-model"})
    verdict(models, "Policy", payload, LevelCheck, "teaching_v31_level")
    assert len(client.calls) == 5


def test_teaching_v31_failed_level_review_is_visible_and_strict_revision_rejects():
    models = TeachingModelsV31(Settings(), Client(fail_level=True))
    models.draft_review_recovery = True
    warnings = models._level_warnings(source(1, "A fact."), "Same teaching.", "en", "advanced", "Same teaching.")
    assert "Difficulty adaptation incomplete" in warnings[0].message
    payload = json.JSONDecoder().raw_decode(models.client.calls[-1]["messages"][0]["content"][0]["text"])[0]
    assert payload["similarity_requires_review"]
    models.draft_review_recovery = False
    with pytest.raises(TeachingMaterialRejected):
        models._level_warnings(source(1, "A fact."), "Same teaching.", "en", "advanced")


def test_teaching_v31_speech_cannot_answer_or_drop_written_question(monkeypatch):
    client = Converse([json.dumps({"preserved": False, "reason": "The narration answers the question."})] * 3)
    models = TeachingModelsV31(Settings(), client)
    speech = TeachingSpeechV31(models.settings, models, client=object())
    monkeypatch.setattr(TeachingSpeechV3, "script", lambda *a, **k: "The answer is four.")
    with pytest.raises(IngestionError, match="TEACHING_V31_SPEECH_TASK_REJECTED"):
        speech.script("What is the observed value?", "en", {}, [], [])


def test_teaching_v31_strict_revision_never_source_recovers(monkeypatch):
    models = TeachingModelsV31(Settings(), Client())
    models.draft_review_recovery = False
    monkeypatch.setattr(models, "_review", lambda *a, **k: ({0: ["Unresolved factual error"]}, []))
    candidate = SectionDraft(paragraphs=[{"text": "Bad claim. [p. 1]", "pages": [1]}])
    with pytest.raises(TeachingMaterialRejected):
        models.checked_material(source(1, "A supported fact."), candidate, "en", "advanced", allow_source_excerpt=True)


def test_teaching_v31_recovery_preserves_passed_paragraph(monkeypatch):
    models = TeachingModelsV31(Settings(), Client())
    models.draft_review_recovery = True
    monkeypatch.setattr(models, "_review", lambda *a, **k: ({1: ["Unresolved factual error"]}, []))
    candidate = SectionDraft(paragraphs=[{"text": "Keep this reviewed paragraph. [p. 1]", "pages": [1]},
                                         {"text": "Bad claim. [p. 1]", "pages": [1]}])
    output, rounds, warnings = models.checked_material(source(1, "A supported fact."), candidate,
                                                       "en", "advanced", allow_source_excerpt=True)
    assert output.paragraphs[0] == candidate.paragraphs[0]
    assert output.paragraphs[1].text == "A supported fact. [p. 1]" and rounds == 2
    assert warnings and "Teacher editing required" in warnings[0].message


def test_teaching_v31_identical_text_cannot_reuse_another_document_binding():
    models = TeachingModelsV31(Settings(), Client(reject="never-matches"))
    first, second = source(1, "A first document fact.", "first"), source(1, "Another fact.", "second")
    text, _ = models._bind(first, EvidenceText(text="Shared prose.", evidence_ids=["1"]))
    models.check_written(second, [text], "en", "intermediate")
    payload = json.JSONDecoder().raw_decode(models.client.calls[0]["messages"][0]["content"][0]["text"])[0]
    assert payload["reference"][0]["document_id"] == "second"
    assert payload["reference"][0]["text"] == "Another fact."


def test_teaching_v31_math_singleton_is_not_a_guessed_citation():
    text, _, _ = bind(source(1, r"The vector is \([3]\)."),
                      EvidenceText(text=r"The vector is \([3]\).", evidence_ids=["1"]))
    assert r"\([3]\)" in text


def test_teaching_v31_host_required_scope_mismatch_fails_before_provider():
    models = TeachingModelsV31(Settings(), Client())
    with pytest.raises(IngestionError, match="TEACHING_V31_REQUIRED_SCOPE_INVALID"):
        models.check_written(source(1, "Fact."), ["Fact. [p. 1]"], "en", "intermediate", [])
    assert not models.client.calls


def test_teaching_v31_author_checkpoint_survives_unavailable_downstream_review(tmp_path):
    client = Converse([json.dumps({"paragraphs": [{"text": "A supported fact.", "evidence_ids": ["1"]}]})])
    section = source(1, "A supported fact.")
    models = TeachingModelsV31(Settings(), client, diagnostic_dir=tmp_path / "diagnostics")
    payload = models._payload(section, "en", "intermediate", 500)
    draft = models._author(section, payload, EvidenceDraft, "teaching_v31_section")
    # A replacement worker reconstructs bindings from the protected parsed draft.
    resumed_client = Converse([])
    resumed = TeachingModelsV31(Settings(), resumed_client, diagnostic_dir=tmp_path / "diagnostics")
    assert resumed._author(section, payload, EvidenceDraft, "teaching_v31_section") == draft
    assert not resumed_client.calls and resumed._bindings
    assert resumed.v31_review_profile["teaching_v31_section"]["cache_hits"] == 1


def test_teaching_v31_multiple_repairs_use_host_targets_and_no_output_indexes():
    client = Client()
    models = TeachingModelsV31(Settings(), client)
    texts = ["Keep the first.", "An invented claim.", "Keep the third.", "Another invented claim."]
    candidate = SectionDraft(paragraphs=[{"text": t + " [p. 1]", "pages": [1]} for t in texts])
    checked, rounds, _ = models.checked_material(source(1, "A supported replacement."), candidate, "en", "intermediate")
    assert rounds == 1
    assert checked.paragraphs[0] == candidate.paragraphs[0]
    assert checked.paragraphs[2] == candidate.paragraphs[2]
    repairs = [c for c in client.calls if '"target"' in c["messages"][0]["content"][0]["text"]]
    assert [json.JSONDecoder().raw_decode(c["messages"][0]["content"][0]["text"])[0]["target"]["candidate_index"]
            for c in repairs] == [1, 3]
    for request in repairs:
        schema = json.loads(request["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"])
        assert set(schema["properties"]) == {"text", "evidence_ids"}


@pytest.mark.parametrize("invalid_repair", [True, "unknown_evidence"])
@pytest.mark.parametrize("strict", [False, True])
def test_teaching_v31_malformed_repairs_are_bounded_never_applied_or_fatal_scope_errors(tmp_path, strict, invalid_repair):
    client = Client(invalid_repair=invalid_repair)
    models = TeachingModelsV31(Settings(), client, diagnostic_dir=tmp_path / "diagnostics")
    models.draft_review_recovery = not strict
    candidate = SectionDraft(paragraphs=[{"text": "Keep this passed paragraph. [p. 1]", "pages": [1]},
                                         {"text": "An invented claim. [p. 1]", "pages": [1]}])
    if strict:
        with pytest.raises(TeachingMaterialRejected):
            models.checked_material(source(1, "A supported fact."), candidate, "en", "advanced", allow_source_excerpt=True)
    else:
        checked, rounds, warnings = models.checked_material(source(1, "A supported fact."), candidate,
                                                            "en", "advanced", allow_source_excerpt=True)
        assert rounds == 2 and checked.paragraphs[0] == candidate.paragraphs[0]
        assert checked.paragraphs[1].text == "A supported fact. [p. 1]"
        assert warnings and "Teacher editing required" in warnings[0].message
    diagnostics = [json.loads(p.read_text()) for p in (tmp_path / "diagnostics").glob("target-repair-*.json")]
    assert len(diagnostics) == 2
    assert all(d["target_index"] == 1 and not d["replacement_applied"] for d in diagnostics)
    assert candidate.paragraphs[1].text == "An invented claim. [p. 1]"
    attempts = [c for c in client.calls if '"target"' in c["messages"][0]["content"][0]["text"]]
    assert len(attempts) == (4 if invalid_repair is True else 2)


def test_teaching_v31_missing_material_appends_only_host_selected_slot(monkeypatch):
    models = TeachingModelsV31(Settings(), Client())
    candidate = SectionDraft(paragraphs=[{"text": "Keep the first paragraph. [p. 1]", "pages": [1]}])
    monkeypatch.setattr(models, "_review", lambda section, texts, *a: ({1: ["A source fact is missing"]}, [])
                        if len(texts) == 1 else ({}, []))
    checked, rounds, _ = models.checked_material(source(1, "A supported replacement."), candidate, "en", "beginner")
    assert rounds == 1 and len(checked.paragraphs) == 2
    assert checked.paragraphs[0] == candidate.paragraphs[0]
    assert checked.paragraphs[1].text == "A supported replacement. [p. 1]"


@pytest.mark.parametrize("append", [False, True])
def test_teaching_v31_large_window_recovers_as_bounded_original_records(append):
    models = TeachingModelsV31(Settings(), Client())
    models.draft_review_recovery = True
    section = source(1, "[Word block 1]\n\n" + "A supported first source sentence. " * 60)
    section["chunks"].append({"chunk_id": "2", "page_numbers": [2],
                              "text": "[Word block 2]\n\n" + "A supported second source sentence. " * 60})
    section["pages"] = [1, 2]
    candidate = SectionDraft(paragraphs=[{"text": "Keep this passed paragraph. [p. 1]", "pages": [1]}])
    if not append:
        candidate.paragraphs.append(type(candidate.paragraphs[0])(text="An invented claim. [pp. 1-2]", pages=[1, 2]))
    output, rounds, warnings = models._recover(section, candidate, {1: ["Required source material is missing."]},
                                               "en", "intermediate", None, 500, 2)
    assert rounds == 2 and warnings
    assert len(output.paragraphs) == 3 and output.paragraphs[0] == candidate.paragraphs[0]
    assert all(len(p.text.split()) <= 500 for p in output.paragraphs)
    assert all("[Word block" not in p.text for p in output.paragraphs)
    assert output.paragraphs[1].text.endswith("[p. 1]") and output.paragraphs[2].text.endswith("[p. 2]")
    assert section["chunks"][0]["text"].startswith("[Word block 1]")


def test_teaching_v31_missing_records_have_separate_targets_and_concise_findings(monkeypatch):
    class Missing(Client):
        def converse(self, **request):
            name = request.get("outputConfig", {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {}).get("name", "")
            payload, _ = json.JSONDecoder().raw_decode(request["messages"][0]["content"][0]["text"])
            if name.startswith("teaching_v31_coverage") and payload["required_excerpt"]["chunk_id"] in {"8", "9"}:
                self.calls.append(request)
                return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps({
                    "omitted_available": ["The service details are missing."], "language_errors": [], "warnings": []})}]}}}
            return super().converse(**request)

    client = Missing()
    models = TeachingModelsV31(Settings(), client)
    section = source(1, "An introductory source fact.")
    section["chunks"].extend([
        {"chunk_id": "6", "page_numbers": [6], "text": "[Word block 6]\nAn innovation heading",
         "heading_path": ["An innovation heading"]},
        {"chunk_id": "8", "page_numbers": [8], "text": "A substantive software service description."},
        {"chunk_id": "9", "page_numbers": [9], "text": "A substantive sustainability service description."},
    ])
    section["pages"] = [1, 6, 8, 9]
    texts = ["A supported introductory fact. [p. 1]"]
    failures, _ = models._review(section, texts, "en", "intermediate")
    assert set(failures) == {1, 2}
    assert list(failures.evidence[1]) == ["8"] and list(failures.evidence[2]) == ["9"]
    assert all("chunk_id" not in text and "document_id" not in text for group in failures.values() for text in group)
    checked_refs = [json.JSONDecoder().raw_decode(c["messages"][0]["content"][0]["text"])[0]["required_excerpt"]
                    for c in client.calls if '"required_excerpt"' in c["messages"][0]["content"][0]["text"]]
    assert all(r["chunk_id"] != "6" for r in checked_refs)
    monkeypatch.setattr(models, "_review", lambda section, texts, *a: (failures, []) if len(texts) == 1 else ({}, []))
    candidate = SectionDraft(paragraphs=[{"text": texts[0], "pages": [1]}])
    output, _, _ = models.checked_material(section, candidate, "en", "intermediate")
    assert output.paragraphs[0] == candidate.paragraphs[0]
    assert output.paragraphs[1].text.endswith("[p. 8]") and output.paragraphs[2].text.endswith("[p. 9]")


def test_teaching_v31_partial_append_repair_cannot_overwrite_an_existing_paragraph():
    candidate = SectionDraft(paragraphs=[{"text": "Keep this paragraph. [p. 1]", "pages": [1]}])
    output = TeachingModelsV31._replace(candidate, {2: ("A recovered later missing fact. [p. 3]", [3])})
    assert output.paragraphs[0] == candidate.paragraphs[0]
    assert len(output.paragraphs) == 2 and output.paragraphs[1].pages == [3]


def test_teaching_v31_recovery_uses_only_target_evidence_and_keeps_subject_labels():
    models = TeachingModelsV31(Settings(), Client())
    models.draft_review_recovery = True
    section = source(1, "An unrelated introductory fact.")
    section["chunks"].append({"chunk_id": "2", "text": "[Word block 2]\n\nA qualified chemist.",
                              "page_numbers": [2], "heading_path": ["Dr. Example"]})
    section["pages"] = [1, 2]
    failures = MaterialFindings()
    failures.add(1, "The biography is missing.", [records(section)[1]])
    candidate = SectionDraft(paragraphs=[{"text": "Keep this passed introduction. [p. 1]", "pages": [1]}])
    output, _, _ = models._recover(section, candidate, failures, "en", "intermediate", None, 500, 2)
    assert len(output.paragraphs) == 2 and output.paragraphs[0] == candidate.paragraphs[0]
    assert "Dr. Example" in output.paragraphs[1].text and output.paragraphs[1].pages == [2]
    assert "unrelated" not in output.paragraphs[1].text


def test_teaching_v31_source_formula_heading_still_requires_coverage():
    from upraise_ai.services.teaching_v31.evidence import substantive

    assert substantive({"text": r"\(x=3\)", "heading_path": [r"\(x=3\)"]})
    assert substantive({"text": "Header", "heading_path": ["Header"], "element_types": ["table"]})
    assert not substantive({"text": "[Word block 6]\nA heading", "heading_path": ["A heading"]})

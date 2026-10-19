"""A plausible outside fact cannot pass by being classified as teaching style."""

import json

import pytest
from test_a3_model_compat import Converse

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.teaching_v2 import TeachingModelsV2
from upraise_ai.services.teaching_v2.entailment import verify_entailment


def payload():
    return {"required_material": ["Compare a plant with a reference plant."], "clarifying_context": [],
            "candidates": ["The reference plant must have identical mass and temperature."], "required_per_candidate": False}


def reply(supported=False):
    return {"supported": supported, "reason": "The matching constraints are not stated in the reference."}


def test_unsupported_scientifically_plausible_relationship_is_blocking():
    client = Converse([json.dumps(reply())])
    result = verify_entailment(TeachingModelsV2(Settings(), client), payload())
    assert result and "matching constraints" in result[0]
    assert "TEXTUAL ENTAILMENT" in client.calls[0]["system"][0]["text"]
    assert client.calls[0]["modelId"] == Settings().lesson_generation_model_id


def test_faithful_supported_paragraph_passes():
    client = Converse([json.dumps(reply(True))])
    data = payload()
    data["candidates"] = ["Compare the plant with its reference."]
    assert verify_entailment(TeachingModelsV2(Settings(), client), data) == []


@pytest.mark.parametrize("bad", [{**reply(), "unsupported_quote": "Not actually in the draft"},
    {**reply(), "supported": "true"}, {**reply(), "candidate_index": -1}, {"checks": []}])
def test_invalid_or_incomplete_entailment_evidence_cannot_pass(bad):
    client = Converse([json.dumps(bad)] * 4)
    with pytest.raises(IngestionError):
        verify_entailment(TeachingModelsV2(Settings(), client), payload())


def test_evidence_location_is_bound_by_host_without_asking_model_to_retype_quotes():
    client = Converse([json.dumps(reply())])
    assert verify_entailment(TeachingModelsV2(Settings(), client), payload())
    sent, _ = json.JSONDecoder().raw_decode(client.calls[-1]["messages"][0]["content"][0]["text"])
    assert sent["candidate"] == payload()["candidates"][0]
    assert sent["reference"] == payload()["required_material"]
    assert len(client.calls) == 1


def test_only_allowed_citation_metadata_is_removed_from_semantic_audit():
    data = payload()
    data.update(citation_numbers=[1, 2, 5], candidates=[r"The value is 7 and \(p=3\). [p. 1] [pp. 1-2] [p. 99] [pp. 1-5]"])
    client = Converse([json.dumps(reply(True))])
    verify_entailment(TeachingModelsV2(Settings(), client), data)
    sent, _ = json.JSONDecoder().raw_decode(client.calls[0]["messages"][0]["content"][0]["text"])
    text = sent["candidate"]
    assert "[p. 1]" not in text and "[pp. 1-2]" not in text
    assert "[p. 99]" in text and "[pp. 1-5]" in text and r"\(p=3\)" in text and "7" in text
    assert "[p. 1]" in data["candidates"][0]  # Published candidate remains intact.

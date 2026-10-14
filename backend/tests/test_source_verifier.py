from __future__ import annotations

import io
import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from PIL import Image

from upraise_ai.services.pdf import vision as vision_module
from upraise_ai.services.pdf.vision import BedrockImageDescriber

PASSING = {"supported": True, "complete": True, "readable": True, "issues": []}


def response(verdict=PASSING, *, stop_reason="end_turn"):
    text = json.dumps(verdict) if not isinstance(verdict, str) else verdict
    return {
        "stopReason": stop_reason,
        "output": {"message": {"content": [{"text": text}]}},
    }


class Client:
    def __init__(self, result=None, *, error=None):
        self.result = response() if result is None else result
        self.error = error
        self.calls = []

    def converse(self, **request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


def crop(tmp_path, name="source.png", *, color="white", size=(120, 80)):
    path = tmp_path / name
    Image.new("RGB", size, color).save(path)
    return path


def verifier(tmp_path, client, *, model_id="test-verifier"):
    return BedrockImageDescriber(session=None, model_id=model_id, cache_dir=tmp_path / "cache", client=client)


def check(subject, paths, **kwargs):
    return subject.verify_source(
        paths,
        candidate=kwargs.pop("candidate", r"\(x^2 + 1\)"),
        content_type=kwargs.pop("content_type", "formula"),
        page_numbers=kwargs.pop("page_numbers", list(range(1, len(paths) + 1))),
        **kwargs,
    )


def test_compares_all_crops_in_order_with_separate_untrusted_candidate(tmp_path):
    paths = [crop(tmp_path), crop(tmp_path, "second.png", color="black")]
    client = Client()
    result = check(verifier(tmp_path, client), paths, page_numbers=[4, 5])
    assert result.passed and not result.issues and not result.warning and not result.from_cache
    request = client.calls[0]
    assert request["modelId"] == "test-verifier"
    blocks = request["messages"][0]["content"]
    assert len(blocks) == 3
    assert all(block["image"]["format"] == "png" for block in blocks[:-1])
    assert blocks[0]["image"]["source"]["bytes"] != blocks[1]["image"]["source"]["bytes"]
    context = json.loads(blocks[-1]["text"])
    assert context == {
        "content_type": "formula",
        "ordered_source_pages": [4, 5],
        "candidate_to_check": r"\(x^2 + 1\)",
    }
    assert "untrusted data" in request["system"][0]["text"]
    assert "superscripts" in request["system"][0]["text"]
    assert "arrow directions and endpoints" in request["system"][0]["text"]


def test_word_picture_verification_uses_native_reference_without_inventing_pdf_pages(tmp_path):
    client = Client()
    result = check(verifier(tmp_path, client), [crop(tmp_path)], content_type='figure',
        page_numbers=[], source_references=['#/pictures/2'])
    assert result.passed and len(client.calls) == 1
    context = json.loads(client.calls[0]['messages'][0]['content'][-1]['text'])
    assert context['ordered_source_pages'] == []
    assert context['ordered_word_picture_references'] == ['#/pictures/2']
    failed = check(verifier(tmp_path, client), [crop(tmp_path)], content_type='figure',
        page_numbers=[], source_references=[])
    assert not failed.passed and len(client.calls) == 1


@pytest.mark.parametrize("flag", ["supported", "complete", "readable"])
def test_mismatch_incomplete_or_unreadable_source_fails_with_review_warning(tmp_path, flag):
    verdict = {**PASSING, flag: False}
    client = Client(response(verdict))
    result = check(verifier(tmp_path, client), [crop(tmp_path)])
    assert not result.passed
    assert result.issues == (f"source_{flag}_check_failed",)
    assert "review" in result.warning
    assert not list((tmp_path / "cache").glob("*.json"))


def test_issues_fail_even_if_model_returns_true_flags_and_rejections_are_not_cached(tmp_path):
    verdict = {**PASSING, "issues": ["Invented arrow from A to C."]}
    client = Client(response(verdict))
    subject = verifier(tmp_path, client)
    paths = [crop(tmp_path)]
    first = check(subject, paths)
    second = check(subject, paths)
    assert not first.passed and not second.passed
    assert first.issues == ("Invented arrow from A to C.",)
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "verdict",
    [
        "",
        "not JSON",
        "```json\n" + json.dumps(PASSING) + "\n```",
        [],
        {"supported": True, "complete": True, "readable": True},
        {**PASSING, "extra": "looks good"},
        {**PASSING, "supported": 1},
        {**PASSING, "supported": "true"},
        {**PASSING, "complete": None},
        {**PASSING, "issues": "[]"},
        {**PASSING, "issues": [1]},
        {**PASSING, "issues": [""]},
        '{"supported":false,"supported":true,"complete":true,"readable":true,"issues":[]}',
        '{"supported":NaN,"complete":true,"readable":true,"issues":[]}',
    ],
)
def test_non_strict_verdicts_fail_closed(tmp_path, verdict):
    result = check(verifier(tmp_path, Client(response(verdict))), [crop(tmp_path)])
    assert not result.passed
    assert result.issues == ("malformed_verification_response",)
    assert result.warning


@pytest.mark.parametrize("stop_reason", ["max_tokens", "content_filtered", "guardrail_intervened", None])
def test_incomplete_or_blocked_response_cannot_pass_even_with_passing_json(tmp_path, stop_reason):
    result = check(verifier(tmp_path, Client(response(stop_reason=stop_reason))), [crop(tmp_path)])
    assert not result.passed
    assert result.issues == ("incomplete_verification_response",)


@pytest.mark.parametrize(
    "bad_output",
    [
        [],
        None,
        {"message": []},
        {"message": {"content": []}},
        {"message": {"content": [{"text": 1}]}},
        {"message": {"content": [{"text": json.dumps(PASSING), "toolUse": {}}]}},
        {"message": {"content": [{"toolUse": {}}]}},
    ],
)
def test_malformed_provider_envelope_fails_closed(tmp_path, bad_output):
    raw = {"stopReason": "end_turn", "output": bad_output}
    result = check(verifier(tmp_path, Client(raw)), [crop(tmp_path)])
    assert not result.passed
    assert result.issues == ("malformed_verification_response",)


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse"),
        EndpointConnectionError(endpoint_url="https://example.invalid"),
    ],
)
def test_provider_failure_returns_review_instead_of_accepting_candidate(tmp_path, error):
    result = check(verifier(tmp_path, Client(error=error)), [crop(tmp_path)])
    assert not result.passed and result.warning
    assert result.issues == ("verification_provider_failed",)


@pytest.mark.parametrize(
    "change",
    ["candidate", "content_type", "pages", "first_image", "last_image", "image_order", "model", "prompt"],
)
def test_successful_cache_keys_include_all_evidence_and_verification_inputs(tmp_path, monkeypatch, change):
    paths = [crop(tmp_path), crop(tmp_path, "second.png", color="black")]
    client = Client()
    subject = verifier(tmp_path, client)
    assert check(subject, paths).passed
    cached = check(subject, paths)
    assert cached.passed and cached.from_cache and len(client.calls) == 1
    kwargs = {}
    if change == "candidate":
        kwargs["candidate"] = r"\(x^2 + 2\)"
    elif change == "content_type":
        kwargs["content_type"] = "inline_math"
    elif change == "pages":
        kwargs["page_numbers"] = [3, 4]
    elif change == "first_image":
        paths[0] = crop(tmp_path, color="red")
    elif change == "last_image":
        paths[-1] = crop(tmp_path, "second.png", color="red")
    elif change == "image_order":
        paths.reverse()
    elif change == "model":
        subject = verifier(tmp_path, client, model_id="another-verifier")
    elif change == "prompt":
        monkeypatch.setattr(vision_module, "VERIFICATION_PROMPT_VERSION", "source-verification-next")
    new = check(subject, paths, **kwargs)
    assert new.passed and not new.from_cache and len(client.calls) == 2


def test_corrupt_or_rejected_cached_verdict_is_rechecked(tmp_path):
    paths = [crop(tmp_path)]
    client = Client()
    subject = verifier(tmp_path, client)
    check(subject, paths)
    cache_path = next((tmp_path / "cache").glob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["verdict"]["complete"] = False
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    assert not check(subject, paths).from_cache
    cache_path.write_text("not JSON", encoding="utf-8")
    assert not check(subject, paths).from_cache
    assert len(client.calls) == 3


@pytest.mark.parametrize(
    "paths_count,pages,issue",
    [
        (0, [], "incomplete_source_mapping"),
        (2, [1], "incomplete_source_mapping"),
        (1, [None], "incomplete_source_mapping"),
        (1, [True], "incomplete_source_mapping"),
        (1, [0], "incomplete_source_mapping"),
        (21, list(range(1, 22)), "source_exceeds_image_limit"),
    ],
)
def test_incomplete_or_over_limit_evidence_is_not_silently_truncated(tmp_path, paths_count, pages, issue):
    client = Client()
    paths = [crop(tmp_path)] * paths_count
    result = check(verifier(tmp_path, client), paths, page_numbers=pages)
    assert not result.passed and result.issues == (issue,) and not client.calls


def test_nova_two_image_limit_returns_review_without_skipping_source(tmp_path):
    client = Client()
    result = check(verifier(tmp_path, client, model_id="eu.amazon.nova-2-lite-v1:0"), [crop(tmp_path)] * 6)
    assert not result.passed and result.issues == ("source_exceeds_image_limit",)
    assert not client.calls


def test_request_payload_limit_includes_all_images_and_base64_overhead(tmp_path, monkeypatch):
    client = Client()
    subject = verifier(tmp_path, client)
    monkeypatch.setattr(subject, "_prepare_verification_image", lambda _: b"x" * 3_000_000)
    result = check(subject, [crop(tmp_path)] * 7)
    assert not result.passed and result.issues == ("source_exceeds_request_limit",)
    assert not client.calls


def test_missing_source_and_oversized_dimensions_are_reviewed(tmp_path):
    client = Client()
    subject = verifier(tmp_path, client)
    assert check(subject, [tmp_path / "missing.png"]).issues == ("source_image_unavailable",)
    assert check(subject, [crop(tmp_path, size=(8001, 1))]).issues == ("source_image_unavailable",)
    assert not client.calls


def test_source_png_keeps_small_symbol_pixels_without_rescaling(tmp_path):
    path = crop(tmp_path, size=(23, 11))
    original = Image.open(path)
    original.putpixel((7, 3), (1, 2, 3))
    original.save(path)
    encoded = BedrockImageDescriber._prepare_verification_image(path)
    with Image.open(io.BytesIO(encoded)) as decoded:
        assert decoded.format == "PNG" and decoded.size == (23, 11)
        assert decoded.getpixel((7, 3)) == (1, 2, 3)


@pytest.mark.parametrize(
    "kwargs,issue",
    [({"candidate": ""}, "empty_candidate"), ({"content_type": "unknown"}, "unsupported_content_type")],
)
def test_invalid_candidate_or_type_does_not_call_provider(tmp_path, kwargs, issue):
    client = Client()
    result = check(verifier(tmp_path, client), [crop(tmp_path)], **kwargs)
    assert not result.passed and result.issues == (issue,) and not client.calls

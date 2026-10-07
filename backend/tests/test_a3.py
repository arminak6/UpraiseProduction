import io
import json
import wave
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import LessonInput, ReviewReport, SectionDraft
from upraise_ai.services.lesson_callback import CallbackClient, validate_callback_config, validate_https
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.lesson_pipeline import plan_sections, segment, validate_draft
from upraise_ai.services.lesson_speech import LessonSpeech, split_words


def request_data(**updates):
    return {"lesson_id": "lesson-1", "course_id": "course-1", "activity_id": "activity-1",
            "document_ids": [str(uuid4())], "language": "en", "voice": "Joanna", **updates}


def callback_settings(**updates):
    return Settings(**{"callback_allowed_hosts": ["mw.example"], "callback_token_url": "https://idp.example/token",
                       "callback_client_id": "ai-client", "callback_client_secret": "test-only-secret",
                       "callback_certificate": Path("client.pem"), "callback_private_key": Path("client.key"),
                       "lesson_public_base_url": "https://ai.example", **updates})


def test_input_defaults_and_invalid_options():
    data = LessonInput.model_validate(request_data())
    assert data.options.source_window_pages == 5 and data.options.target_chunk_words == 200
    assert LessonInput.model_validate(request_data(callback_url="")).callback_url is None
    for updates in ({"document_ids": [data.document_ids[0]] * 2}, {"language": "invalid_language"},
                    {"options": {"source_window_pages": 0}}, {"options": {"learner_level": "expert"}},
                    {"options": {"target_chunk_words": 29}}, {"unknown": True}):
        with pytest.raises(ValidationError):
            LessonInput.model_validate(request_data(**updates))


def test_source_order_and_multipage_window_not_duplicated():
    def document(name, page_count, chunks):
        row = {"document_id": name, "job_id": "job-"+name, "active_attempt": "attempt",
               "result": {"page_count": page_count}, "metadata": {"language": "en"}}
        return row, [{"chunk_id": name+str(i), "document_version_id": "version", "page_numbers": pages}
                     for i, pages in enumerate(chunks)]

    sections = plan_sections([document("second", 12, [[1], [5, 6], [7], [12]]),
                              document("first", 3, [[1], [3]])], 5)
    assert [s["document_id"] for s in sections] == ["second"] * 3 + ["first"]
    assert sections[0]["pages"] == [1, 5, 6]
    ids = [chunk["chunk_id"] for section in sections for chunk in section["chunks"]]
    assert len(ids) == len(set(ids)) == 6
    assert plan_sections([document("a", 12, [[1], [6], [7], [12]])], 6)[0]["window"] == [1, 6]


def test_segmentation_preserves_formula_order_and_citation_sets():
    formula = r"\(x_1 + \frac{a}{b} = y^2\)"
    text = ("This is a source grounded sentence. " * 9) + formula + " This is the final sentence."
    chunks = split_words(text, 30)
    assert sum(formula in chunk for chunk in chunks) == 1
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())
    draft = SectionDraft(paragraphs=[{"text": text, "pages": [1, 2]},
                                     {"text": "Another supported paragraph.", "pages": [3]}])
    assert all(pages for _, pages in segment(draft, 30))
    with pytest.raises(IngestionError, match="CITATIONS"):
        validate_draft(draft, [1, 2])


class Polly:
    def __init__(self):
        self.calls = []

    def describe_voices(self, **kwargs):
        return {"Voices": [{"Id": "Joanna", "LanguageCode": "en-US", "SupportedEngines": ["neural"]}]}

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        body = (b"\x00\x00" * 16000 if kwargs["OutputFormat"] == "pcm" else
                b'{"type":"viseme","time":100,"value":"a"}\n{"type":"viseme","time":500,"value":"p"}\n')
        return {"AudioStream": io.BytesIO(body)}


class MathModels:
    def math_narration(self, latex, context, language):
        return "x plus one" if language == "en" else None


def test_polly_audio_and_visemes_use_identical_voice_engine_ssml_and_offsets():
    polly = Polly()
    speech = LessonSpeech(Settings(), MathModels(), client=polly)
    assert speech.validate_voice("Joanna", "en") == "en-US"
    with pytest.raises(IngestionError, match="VOICE_LANGUAGE"):
        speech.validate_voice("Joanna", "it")
    full, parts, issues = speech.prepare("Supported words. " * 400, "en")
    assert len(parts) > 1 and not issues and full.startswith("<speak>")
    audio, marks, duration, _ = speech.synthesize(parts, "Joanna", "en-US")
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getnframes() / wav.getframerate() == duration == len(parts)
    assert [mark.time_ms for mark in marks][:4] == [100, 500, 1100, 1500]
    for audio_call, mark_call in zip(polly.calls[::2], polly.calls[1::2], strict=True):
        assert all(audio_call[key] == mark_call[key] for key in ("Text", "TextType", "VoiceId", "LanguageCode", "Engine"))
        assert mark_call["SpeechMarkTypes"] == ["viseme"]


def test_formula_fallback_is_reviewed_and_unavailable_formula_is_not_silently_dropped():
    speech = LessonSpeech(Settings(), MathModels(), client=Polly(), converter=lambda *args: None)
    full, _, issues = speech.prepare(r"A formula \(x+1\).", "en")
    assert "x plus one" in full and not issues
    full, _, issues = speech.prepare(r"A formula \(x+1\).", "it")
    assert "x plus 1" in full and issues[0].category == "speech"


def test_uncertain_contextual_fallback_keeps_explicit_report():
    speech = LessonSpeech(Settings(), MathModels(), client=Polly(), converter=lambda *args: "x star")
    _, _, issues = speech.prepare(r"A formula \(x^*\).", "it")
    assert len(issues) == 1 and issues[0].category == "speech"


@pytest.mark.parametrize("url", ["http://mw.example/done", "https://other.example/done", "https://mw.example:444/done",
                                 "https://user@mw.example/done", "https://mw.example/done#fragment",
                                 "https://mw.example/done\nInjected: header"])
def test_callback_rejects_unapproved_destinations(url):
    with pytest.raises(IngestionError, match="URL_NOT_ALLOWED"):
        validate_https(url, ["mw.example"])


def test_push_requires_both_mtls_and_oauth_even_in_local_mode():
    with pytest.raises(IngestionError, match="NOT_CONFIGURED"):
        validate_callback_config(Settings(callback_allowed_hosts=["mw.example"], environment="development",
                                           local_test_mode=True), "https://mw.example/done")
    validate_callback_config(callback_settings(), "https://mw.example/done")


def test_callback_gets_fresh_short_lived_token_and_does_not_acknowledge():
    client = CallbackClient(callback_settings())
    calls = []

    def post(url, data, headers, **kwargs):
        calls.append((url, data, headers, kwargs))
        return b'{"access_token":"temporary-token","token_type":"Bearer","expires_in":300}' if kwargs else b""

    client.post = post
    row = {"job_id": uuid4(), "metadata": {"callback_url": "https://mw.example/done", "lesson_id": "lesson"},
           "status": "DONE", "error_code": None}
    client.deliver(row, {"status": "DRAFT", "chunks": []})
    assert calls[0][3]["token_endpoint"]
    assert b"grant_type=client_credentials" in calls[0][1]
    assert calls[1][2]["Authorization"] == "Bearer temporary-token"
    assert calls[1][2]["Idempotency-Key"] == "lesson-result-" + str(row["job_id"])
    assert json.loads(calls[1][1])["result"]["status"] == "DRAFT"


@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post", "tls_client_auth"])
def test_oauth_client_authentication_methods(method):
    from urllib.parse import parse_qs

    settings = callback_settings(callback_oauth_auth_method=method,
                                 **({"callback_client_secret": ""} if method == "tls_client_auth" else {}))
    client = CallbackClient(settings)
    calls = []
    def post(url, data, headers, **kwargs):
        calls.append((parse_qs(data.decode()) if kwargs else {}, headers))
        return b'{"access_token":"token","token_type":"Bearer","expires_in":300}' if kwargs else b""
    client.post = post
    row = {"job_id": uuid4(), "metadata": {"callback_url": "https://mw.example/done", "lesson_id": "lesson"},
           "status": "DONE", "error_code": None}
    client.deliver(row, {})
    body, headers = calls[0]
    if method == "client_secret_basic":
        assert headers["Authorization"].startswith("Basic ") and "client_secret" not in body
    elif method == "client_secret_post":
        assert body["client_secret"] == ["test-only-secret"] and "Authorization" not in headers
    else:
        assert body["client_id"] == ["ai-client"] and "client_secret" not in body and "Authorization" not in headers


def test_lesson_model_profiles_cannot_route_outside_eu():
    with pytest.raises(ValidationError):
        Settings(lesson_generation_model_id="global.example-model")
    with pytest.raises(ValidationError):
        Settings(lesson_visual_model_id="arn:aws:bedrock:us-east-1:123:inference-profile/example")
    assert Settings(lesson_visual_model_id="eu.amazon.nova-pro-v1:0")


@pytest.mark.parametrize("lifetime", [0, 30, 901, True, "300"])
def test_callback_rejects_unusable_or_long_lived_tokens(lifetime):
    client = CallbackClient(callback_settings())
    client.post = lambda *args, **kwargs: json.dumps({"access_token": "token", "token_type": "Bearer", "expires_in": lifetime}).encode()
    row = {"job_id": uuid4(), "metadata": {"callback_url": "https://mw.example/done", "lesson_id": "lesson"},
           "status": "FAILED", "error_code": "EXAMPLE"}
    with pytest.raises(IngestionError, match="OAUTH_INVALID"):
        client.deliver(row, None)


def test_callback_blocks_dns_rebinding_and_pins_tls_hostname(monkeypatch):
    client = CallbackClient(callback_settings())
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(IngestionError, match="ADDRESS_NOT_ALLOWED"):
        client.post("https://mw.example/done", b"{}", {})
    captured = {}

    class Pool:
        def __init__(self, host, **kwargs):
            captured.update(host=host, **kwargs)

        def urlopen(self, method, target, **kwargs):
            captured["request"] = kwargs
            return type("Response", (), {"status": 200, "read": lambda *a: b"{}", "close": lambda *a: None})()

        def close(self):
            pass

    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))])
    monkeypatch.setattr("urllib3.HTTPSConnectionPool", Pool)
    assert client.post("https://mw.example/done", b"{}", {}) == b"{}"
    assert captured["host"] == "8.8.8.8" and captured["server_hostname"] == "mw.example"
    assert captured["cert_reqs"] == "CERT_REQUIRED" and captured["key_file"] == "client.key"
    assert not captured["request"]["redirect"] and not captured["request"]["retries"]


def test_model_output_must_be_complete_and_review_consistent():
    class Client:
        def converse(self, **kwargs):
            return {"stopReason": "max_tokens", "output": {"message": {"content": [{"text": "{}"}]}}}

    models = LessonModels(Settings(), Client())
    with pytest.raises(IngestionError, match="INCOMPLETE"):
        models.generate_section({"pages": [1], "window": [1, 1], "chunks": [
            {"chunk_id": "source", "text": "Source fact.", "page_numbers": [1]}]}, "en")
    with pytest.raises(ValidationError):
        ReviewReport(approved=True, summary="approved", issues=[{"category": "accuracy", "message": "error"}])

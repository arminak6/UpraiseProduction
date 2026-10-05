from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError

from upraise_ai.services.v3.polly_speech import (
    PollySpeechSynthesizerV3,
    prepare_speech_document,
    prepare_speech_text,
    split_speech_text,
)


class FakeMathConverter:
    def __init__(self, narrations: list[str | None]):
        self.narrations = narrations
        self.formulas: list[str] = []

    def convert(self, formulas):
        self.formulas.extend(formulas)
        return list(self.narrations)


class FakeAudioStream:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class FakePollyClient:
    def __init__(
        self,
        *,
        failure_code: str | None = None,
        unsupported_neural: bool = False,
    ):
        self.failure_code = failure_code
        self.unsupported_neural = unsupported_neural
        self.calls: list[dict] = []
        self.streams: list[FakeAudioStream] = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure_code:
            raise ClientError(
                {
                    "Error": {
                        "Code": self.failure_code,
                        "Message": "secret details must not be exposed",
                    }
                },
                "SynthesizeSpeech",
            )
        if self.unsupported_neural and kwargs["Engine"] == "neural":
            raise ClientError(
                {
                    "Error": {
                        "Code": "EngineNotSupportedException",
                        "Message": "unsupported",
                    }
                },
                "SynthesizeSpeech",
            )
        stream = FakeAudioStream(b"\x00\x00" * 160)
        self.streams.append(stream)
        return {"AudioStream": stream}


def make_synthesizer(
    tmp_path: Path,
    client: FakePollyClient,
    *,
    max_chunk_characters: int = 2_800,
) -> PollySpeechSynthesizerV3:
    return PollySpeechSynthesizerV3(
        session=None,  # type: ignore[arg-type]
        voice_id="Joanna",
        engine="neural",
        artifacts_dir=tmp_path,
        client=client,
        max_chunk_characters=max_chunk_characters,
    )


def test_synthesizer_chunks_wav_closes_streams_and_uses_cache(
    tmp_path: Path,
) -> None:
    client = FakePollyClient()
    synthesizer = make_synthesizer(
        tmp_path,
        client,
        max_chunk_characters=40,
    )
    text = "First sentence has useful detail. " * 8

    first = synthesizer.synthesize(
        text,
        document_version_id="version-1",
    )

    assert first.warning is None
    assert first.audio is not None
    assert first.audio.startswith(b"RIFF")
    assert first.audio[8:12] == b"WAVE"
    assert len(client.calls) > 1
    assert all(len(call["Text"]) <= 40 for call in client.calls)
    assert all(call["OutputFormat"] == "pcm" for call in client.calls)
    assert all(call["SampleRate"] == "16000" for call in client.calls)
    assert all(stream.closed for stream in client.streams)
    assert len(list(tmp_path.rglob("*.wav"))) == 1

    call_count = len(client.calls)
    cached = synthesizer.synthesize(
        text,
        document_version_id="version-1",
    )

    assert cached.audio == first.audio
    assert cached.from_cache
    assert len(client.calls) == call_count


def test_transient_synthesis_never_reads_or_writes_disk(
    tmp_path: Path,
) -> None:
    client = FakePollyClient()
    synthesizer = make_synthesizer(tmp_path, client)
    text = "A student-specific answer that must remain temporary."

    first = synthesizer.synthesize(
        text,
        document_version_id="version-1",
        persist=False,
    )

    assert first.audio is not None
    assert first.from_cache is False
    assert list(tmp_path.rglob("*.wav")) == []
    first_call_count = len(client.calls)

    second = synthesizer.synthesize(
        text,
        document_version_id="version-1",
        persist=False,
    )

    assert second.audio is not None
    assert second.from_cache is False
    assert len(client.calls) == first_call_count * 2
    assert list(tmp_path.rglob("*.wav")) == []


def test_synthesizer_falls_back_to_standard_engine(tmp_path: Path) -> None:
    client = FakePollyClient(unsupported_neural=True)
    synthesizer = make_synthesizer(tmp_path, client)

    outcome = synthesizer.synthesize(
        "A short teacher explanation.",
        document_version_id="version-1",
    )

    assert outcome.audio is not None
    assert outcome.engine == "standard"
    assert [call["Engine"] for call in client.calls] == [
        "neural",
        "standard",
    ]


def test_synthesizer_returns_safe_nonfatal_warning(tmp_path: Path) -> None:
    client = FakePollyClient(failure_code="AccessDeniedException")
    synthesizer = make_synthesizer(tmp_path, client)

    outcome = synthesizer.synthesize(
        "A short teacher explanation.",
        document_version_id="version-1",
    )

    assert outcome.audio is None
    assert "AccessDeniedException" in (outcome.warning or "")
    assert "secret details" not in (outcome.warning or "")


def test_markdown_normalization_and_sentence_chunking() -> None:
    normalized = prepare_speech_text(
        "## Topic\n- **Random graphs** use a [baseline](https://example.com). "
        "[p. 4] Their range is cited too [pp. 4-5].\n"
        "| Value | Meaning |\n| --- | --- |"
    )

    assert "#" not in normalized
    assert "**" not in normalized
    assert "https://" not in normalized
    assert "[p. 4]" not in normalized
    assert "[pp. 4-5]" not in normalized
    assert "Random graphs" in normalized

    chunks = split_speech_text(
        "One sentence. Two sentence. Three sentence.",
        max_characters=20,
    )
    assert chunks
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_formula_is_converted_to_slow_ssml_before_polly(tmp_path: Path) -> None:
    client = FakePollyClient()
    converter = FakeMathConverter(
        ["delta of q and X sub i equals the triple p, Y, and L"]
    )
    synthesizer = PollySpeechSynthesizerV3(
        session=None,  # type: ignore[arg-type]
        voice_id="Joanna",
        engine="neural",
        artifacts_dir=tmp_path,
        client=client,
        math_converter=converter,
    )

    outcome = synthesizer.synthesize(
        r"A transition is \(\delta(q,X_i)=(p,Y,L)\).",
        document_version_id="version-math",
        persist=False,
    )

    assert outcome.audio is not None
    assert converter.formulas == [r"\delta(q,X_i)=(p,Y,L)"]
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["TextType"] == "ssml"
    assert request["Text"].startswith("<speak>")
    assert '<prosody rate="85%">' in request["Text"]
    assert "delta of q and X sub i" in request["Text"]
    assert r"\delta" not in request["Text"]


def test_failed_general_conversion_uses_reviewed_fallback() -> None:
    converter = FakeMathConverter([None])
    fallback_requests = []

    def fallback(requests):
        fallback_requests.extend(requests)
        return ["x squared plus one"]

    prepared = prepare_speech_document(
        r"Consider \(x^2+1\) now.",
        converter=converter,
        fallback=fallback,
    )

    assert prepared.plain_text == "Consider x squared plus one now."
    assert prepared.use_ssml
    assert [request.latex for request in fallback_requests] == ["x^2+1"]


def test_mechanical_parser_wording_uses_contextual_fallback() -> None:
    converter = FakeMathConverter(
        ["q sub 0 w right tack sub M raised to the asterisk power alpha"]
    )
    fallback_requests = []

    def fallback(requests):
        fallback_requests.extend(requests)
        return ["state q zero on w yields alpha in zero or more steps of M"]

    prepared = prepare_speech_document(
        r"The accepting computation is \(q_0w \vdash_M^* \alpha\).",
        converter=converter,
        fallback=fallback,
    )

    assert "yields alpha in zero or more steps of M" in prepared.plain_text
    assert [request.latex for request in fallback_requests] == [
        r"q_0w \vdash_M^* \alpha"
    ]


def test_each_formula_fallback_receives_only_its_nearest_sentence() -> None:
    converter = FakeMathConverter([None, None])
    fallback_requests = []

    def fallback(requests):
        fallback_requests.extend(requests)
        return ["read zero, write zero", "x squared"]

    prepare_speech_document(
        "A transition label \\(0/0\\) means read and write zero. "
        "This unrelated sentence separates the examples. "
        "A polynomial contains \\(x^2\\).",
        converter=converter,
        fallback=fallback,
    )

    assert "read and write zero" in fallback_requests[0].context
    assert "polynomial" not in fallback_requests[0].context
    assert "polynomial" in fallback_requests[1].context
    assert "transition" not in fallback_requests[1].context

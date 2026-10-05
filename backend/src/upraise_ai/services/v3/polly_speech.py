from __future__ import annotations

import io
import re
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from threading import Lock
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .utils import sha256_text

from .math_speech import (
    MathJaxSpeechRuleConverter,
    MathNarrationFallback,
    MathNarrationRequest,
    MathSpeechConverter,
    convert_with_fallback,
)

CACHE_VERSION = "polly-speech-wav-v3-math"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_MAX_CHUNK_CHARACTERS = 2_800
MAX_POLLY_TOTAL_CHARACTERS = 5_800

_CITATION_PATTERN = re.compile(
    r"\[(?:p{1,2}\.\s*)?[0-9]+(?:\s*[-–]\s*[0-9]+)?(?:\s*,\s*[0-9]+)*\]",
    flags=re.IGNORECASE,
)
_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_PATTERN = re.compile(r"<[^>]+>")
_TABLE_SEPARATOR_PATTERN = re.compile(
    r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$",
    flags=re.MULTILINE,
)
_HEADING_PATTERN = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_LIST_PATTERN = re.compile(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+")
_MATH_PATTERN = re.compile(
    r"(?P<display_dollar>\$\$(?P<display_dollar_body>.+?)\$\$)"
    r"|(?P<display_bracket>\\\[(?P<display_bracket_body>.+?)\\\])"
    r"|(?P<inline_paren>\\\((?P<inline_paren_body>.+?)\\\))"
    r"|(?P<inline_dollar>(?<!\\)(?<!\$)\$(?!\$)"
    r"(?P<inline_dollar_body>.+?)(?<!\\)\$(?!\$))",
    flags=re.DOTALL,
)
_MATH_TOKEN_PATTERN = re.compile(r"UPRISEMATH(?P<index>\d+)TOKEN")
_CONTEXT_BOUNDARY_PATTERN = re.compile(r"[.!?](?:\s+|$)")
_MATH_CONTEXT_RADIUS = 300


@dataclass(frozen=True)
class _ExtractedMath:
    latex: str
    token: str
    display: bool
    context: str


@dataclass(frozen=True)
class SpeechPart:
    text: str
    is_math: bool = False
    display: bool = False


@dataclass(frozen=True)
class PreparedSpeech:
    plain_text: str
    parts: tuple[SpeechPart, ...]
    use_ssml: bool = False


@dataclass(frozen=True)
class SpeechSynthesisOutcome:
    audio: bytes | None
    media_type: str = "audio/wav"
    voice_id: str = ""
    engine: str = ""
    cache_key: str = ""
    from_cache: bool = False
    warning: str | None = None


class PollySpeechSynthesizerV3:
    """Best-effort Amazon Polly speech with optional persistent caching."""

    cache_version = CACHE_VERSION

    def __init__(
        self,
        *,
        session: boto3.Session,
        voice_id: str,
        engine: str,
        artifacts_dir: Path,
        client: Any | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
        math_converter: MathSpeechConverter | None = None,
        math_narration_fallback: MathNarrationFallback | None = None,
    ):
        cleaned_voice = voice_id.strip()
        cleaned_engine = engine.strip().lower()
        if not cleaned_voice:
            raise ValueError("A Polly voice ID is required.")
        if cleaned_engine not in {
            "standard",
            "neural",
            "long-form",
            "generative",
        }:
            raise ValueError(
                "Polly engine must be standard, neural, long-form, or generative."
            )
        if sample_rate not in {8_000, 16_000}:
            raise ValueError(
                "Polly PCM sample rate must be either 8000 or 16000."
            )
        if not 1 <= max_chunk_characters <= 2_800:
            raise ValueError(
                "Polly chunk size must be between 1 and 2,800 characters."
            )

        self.session = session
        self.voice_id = cleaned_voice
        self.engine = cleaned_engine
        self.artifacts_dir = artifacts_dir
        self.sample_rate = sample_rate
        self.max_chunk_characters = max_chunk_characters
        self.math_converter = (
            math_converter
            if math_converter is not None
            else MathJaxSpeechRuleConverter()
        )
        self.math_narration_fallback = math_narration_fallback
        self._client = client
        self._cache_lock = Lock()

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self.session.client(
                "polly",
                config=Config(
                    retries={"max_attempts": 5, "mode": "adaptive"},
                    connect_timeout=10,
                    read_timeout=120,
                ),
            )
        return self._client

    def synthesize(
        self,
        text: str,
        *,
        document_version_id: str,
        persist: bool = True,
    ) -> SpeechSynthesisOutcome:
        prepared_speech = self._prepare_speech_document(text)
        speech_text = prepared_speech.plain_text
        cleaned_version_id = document_version_id.strip()
        if not speech_text:
            return SpeechSynthesisOutcome(
                audio=None,
                voice_id=self.voice_id,
                engine=self.engine,
                warning="The teacher message did not contain speakable text.",
            )
        if not cleaned_version_id:
            return SpeechSynthesisOutcome(
                audio=None,
                voice_id=self.voice_id,
                engine=self.engine,
                warning="The teacher voice could not identify this document version.",
            )
        artifact_root = self.artifacts_dir.resolve()
        document_artifact_dir = (artifact_root / cleaned_version_id).resolve()
        if (
            document_artifact_dir == artifact_root
            or not document_artifact_dir.is_relative_to(artifact_root)
        ):
            return SpeechSynthesisOutcome(
                audio=None,
                voice_id=self.voice_id,
                engine=self.engine,
                warning="The teacher voice received an invalid document version.",
            )

        cache_key = sha256_text(
            "|".join(
                [
                    self.cache_version,
                    self.voice_id,
                    self.engine,
                    str(self.sample_rate),
                    speech_text,
                    str(prepared_speech.use_ssml),
                ]
            )
        )
        cache_path = (
            document_artifact_dir
            / "learning"
            / "v1"
            / "speech"
            / f"{cache_key}.wav"
        )
        if persist:
            cached = self._read_cache(cache_path)
            if cached is not None:
                return SpeechSynthesisOutcome(
                    audio=cached,
                    voice_id=self.voice_id,
                    engine=self.engine,
                    cache_key=cache_key,
                    from_cache=True,
                )

        with self._cache_lock:
            if persist:
                cached = self._read_cache(cache_path)
                if cached is not None:
                    return SpeechSynthesisOutcome(
                        audio=cached,
                        voice_id=self.voice_id,
                        engine=self.engine,
                        cache_key=cache_key,
                        from_cache=True,
                    )
            try:
                audio, actual_engine = self._synthesize_wav(prepared_speech)
                if persist:
                    self._write_cache(cache_path, audio)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get(
                    "Code",
                    "ClientError",
                )
                return SpeechSynthesisOutcome(
                    audio=None,
                    voice_id=self.voice_id,
                    engine=self.engine,
                    cache_key=cache_key,
                    warning=(
                        "Amazon Polly could not create the teacher voice "
                        f"({error_code}). Written learning remains available."
                    ),
                )
            except (BotoCoreError, OSError, RuntimeError, ValueError) as exc:
                return SpeechSynthesisOutcome(
                    audio=None,
                    voice_id=self.voice_id,
                    engine=self.engine,
                    cache_key=cache_key,
                    warning=(
                        "Amazon Polly voice generation failed "
                        f"({type(exc).__name__}). Written learning remains available."
                    ),
                )

        return SpeechSynthesisOutcome(
            audio=audio,
            voice_id=self.voice_id,
            engine=actual_engine,
            cache_key=cache_key,
        )

    def _prepare_speech_document(self, text: str) -> PreparedSpeech:
        return prepare_speech_document(
            text,
            converter=self.math_converter,
            fallback=self.math_narration_fallback,
        )

    def _synthesize_wav(self, speech: PreparedSpeech) -> tuple[bytes, str]:
        if speech.use_ssml:
            chunks = split_speech_ssml(
                speech.parts,
                max_characters=self.max_chunk_characters,
            )
            text_type = "ssml"
        else:
            chunks = split_speech_text(
                speech.plain_text,
                max_characters=self.max_chunk_characters,
            )
            text_type = "text"
        try:
            pcm = self._synthesize_chunks(
                chunks,
                engine=self.engine,
                text_type=text_type,
            )
            actual_engine = self.engine
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if (
                self.engine == "standard"
                or error_code != "EngineNotSupportedException"
            ):
                raise
            pcm = self._synthesize_chunks(
                chunks,
                engine="standard",
                text_type=text_type,
            )
            actual_engine = "standard"

        if not pcm:
            raise RuntimeError("Amazon Polly returned an empty audio stream.")

        destination = io.BytesIO()
        with wave.open(destination, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(pcm)
        return destination.getvalue(), actual_engine

    def _synthesize_chunks(
        self,
        chunks: list[str],
        *,
        engine: str,
        text_type: str,
    ) -> bytes:
        parts: list[bytes] = []
        for chunk in chunks:
            response = self.client.synthesize_speech(
                Engine=engine,
                OutputFormat="pcm",
                SampleRate=str(self.sample_rate),
                Text=chunk,
                TextType=text_type,
                VoiceId=self.voice_id,
            )
            stream = response.get("AudioStream")
            if stream is None:
                raise RuntimeError("Amazon Polly returned no audio stream.")
            try:
                payload = stream.read()
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            if not isinstance(payload, bytes) or not payload:
                raise RuntimeError("Amazon Polly returned an empty audio chunk.")
            parts.append(payload)
        return b"".join(parts)

    @staticmethod
    def _read_cache(path: Path) -> bytes | None:
        if not path.exists():
            return None
        try:
            payload = path.read_bytes()
        except OSError:
            return None
        if (
            len(payload) < 44
            or not payload.startswith(b"RIFF")
            or payload[8:12] != b"WAVE"
        ):
            return None
        return payload

    @staticmethod
    def _write_cache(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)


def prepare_speech_document(
    value: str,
    *,
    converter: MathSpeechConverter,
    fallback: MathNarrationFallback | None = None,
) -> PreparedSpeech:
    tokenized, expressions = _extract_math(value)
    normalized = prepare_speech_text(tokenized)
    if not expressions:
        parts = (SpeechPart(normalized),) if normalized else ()
        return PreparedSpeech(plain_text=normalized, parts=parts)

    narrations = convert_with_fallback(
        [
            MathNarrationRequest(
                latex=expression.latex,
                context=expression.context,
            )
            for expression in expressions
        ],
        converter=converter,
        fallback=fallback,
    )
    parts: list[SpeechPart] = []
    cursor = 0
    for match in _MATH_TOKEN_PATTERN.finditer(normalized):
        prose = normalized[cursor : match.start()].strip()
        if prose:
            parts.append(SpeechPart(prose))
        index = int(match.group("index"))
        if index < len(expressions):
            narration = narrations[index] or _basic_latex_narration(
                expressions[index].latex
            )
            if narration:
                parts.append(
                    SpeechPart(
                        narration,
                        is_math=True,
                        display=expressions[index].display,
                    )
                )
        cursor = match.end()
    trailing = normalized[cursor:].strip()
    if trailing:
        parts.append(SpeechPart(trailing))

    plain_text = _join_part_text(parts)
    return PreparedSpeech(
        plain_text=plain_text,
        parts=tuple(parts),
        use_ssml=bool(parts),
    )


def _extract_math(value: str) -> tuple[str, list[_ExtractedMath]]:
    expressions: list[_ExtractedMath] = []
    output: list[str] = []
    cursor = 0
    for match in _MATH_PATTERN.finditer(value):
        output.append(value[cursor : match.start()])
        body = next(
            (
                match.group(group)
                for group in (
                    "display_dollar_body",
                    "display_bracket_body",
                    "inline_paren_body",
                    "inline_dollar_body",
                )
                if match.group(group) is not None
            ),
            "",
        ).strip()
        if not body:
            output.append(match.group(0))
            cursor = match.end()
            continue
        index = len(expressions)
        token = f"UPRISEMATH{index}TOKEN"
        expressions.append(
            _ExtractedMath(
                latex=body,
                token=token,
                display=bool(
                    match.group("display_dollar")
                    or match.group("display_bracket")
                ),
                context=_math_context(value, match.start(), match.end()),
            )
        )
        output.append(f" {token} ")
        cursor = match.end()
    output.append(value[cursor:])
    return "".join(output), expressions


def _math_context(value: str, start: int, end: int) -> str:
    """Return the nearest sentence-sized context around one expression."""

    lower_bound = max(0, start - _MATH_CONTEXT_RADIUS)
    upper_bound = min(len(value), end + _MATH_CONTEXT_RADIUS)
    prefix = value[lower_bound:start]
    previous_boundaries = list(_CONTEXT_BOUNDARY_PATTERN.finditer(prefix))
    context_start = (
        lower_bound + previous_boundaries[-1].end()
        if previous_boundaries
        else lower_bound
    )

    suffix = value[end:upper_bound]
    next_boundary = _CONTEXT_BOUNDARY_PATTERN.search(suffix)
    context_end = (
        end + next_boundary.start() + 1
        if next_boundary is not None
        else upper_bound
    )
    return value[context_start:context_end].strip()


def _basic_latex_narration(value: str) -> str:
    """Last-resort structural reading when both general narrators fail."""

    text = value
    replacements = {
        r"\frac": " fraction ",
        r"\sqrt": " square root ",
        r"\sum": " sum ",
        r"\prod": " product ",
        r"\int": " integral ",
        r"\cdot": " times ",
        r"\times": " times ",
        r"\pm": " plus or minus ",
        r"\leq": " less than or equal to ",
        r"\le": " less than or equal to ",
        r"\geq": " greater than or equal to ",
        r"\ge": " greater than or equal to ",
        r"\neq": " not equal to ",
        r"\in": " in ",
        r"\subseteq": " is a subset of ",
        r"\to": " maps to ",
        r"\rightarrow": " maps to ",
        r"\mid": " such that ",
        r"\ldots": " dot dot dot ",
        r"\cdots": " dot dot dot ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\\([A-Za-z]+)", r" \1 ", text)
    text = text.replace("_", " sub ").replace("^", " to the power ")
    text = text.replace("=", " equals ").replace("+", " plus ")
    text = text.replace("−", " minus ").replace("-", " minus ")
    text = text.replace("{", " ").replace("}", " ")
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def prepare_speech_text(value: str) -> str:
    """Convert common Markdown in teacher messages into speakable plain text."""

    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _IMAGE_PATTERN.sub(r"\1", text)
    text = _LINK_PATTERN.sub(r"\1", text)
    text = _CITATION_PATTERN.sub("", text)
    text = _HTML_PATTERN.sub(" ", text)
    text = _TABLE_SEPARATOR_PATTERN.sub("", text)
    text = _HEADING_PATTERN.sub("", text)
    text = _LIST_PATTERN.sub("", text)
    text = text.replace("```", " ").replace("`", "")
    text = text.replace("**", "").replace("__", "")
    text = text.replace("|", ". ")
    text = text.replace("*", "").replace("_", " ")
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def split_speech_ssml(
    parts: Sequence[SpeechPart],
    *,
    max_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> list[str]:
    if max_characters <= 0:
        raise ValueError("Speech chunk size must be greater than zero.")
    atomic_parts: list[SpeechPart] = []
    for part in parts:
        for text in split_speech_text(
            part.text,
            max_characters=max_characters,
        ):
            atomic_parts.append(
                SpeechPart(
                    text=text,
                    is_math=part.is_math,
                    display=part.display,
                )
            )

    chunks: list[str] = []
    current: list[SpeechPart] = []
    for part in atomic_parts:
        candidate = [*current, part]
        candidate_plain = _join_part_text(candidate)
        candidate_ssml = _render_ssml(candidate)
        if current and (
            len(candidate_plain) > max_characters
            or len(candidate_ssml) > MAX_POLLY_TOTAL_CHARACTERS
        ):
            chunks.append(_render_ssml(current))
            current = [part]
        else:
            current = candidate
    if current:
        chunks.append(_render_ssml(current))
    return chunks


def _join_part_text(parts: Sequence[SpeechPart]) -> str:
    result = ""
    for part in parts:
        if not part.text:
            continue
        separator = ""
        if result and not re.match(r"^[,.;:!?)]", part.text):
            separator = " "
        result += separator + part.text
    return result.strip()


def _render_ssml(parts: Sequence[SpeechPart]) -> str:
    rendered = ""
    for part in parts:
        separator = ""
        if rendered and not re.match(r"^[,.;:!?)]", part.text):
            separator = " "
        content = escape(part.text)
        if part.is_math:
            content = f'<prosody rate="85%">{content}</prosody>'
            if part.display:
                content = f'<break time="200ms"/>{content}<break time="250ms"/>'
        rendered += separator + content
    return f"<speak>{rendered.strip()}</speak>"


def split_speech_text(
    value: str,
    *,
    max_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> list[str]:
    if max_characters <= 0:
        raise ValueError("Speech chunk size must be greater than zero.")
    text = value.strip()
    if not text:
        return []

    sentences = _SENTENCE_BOUNDARY_PATTERN.split(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        for part in _split_long_segment(sentence, max_characters):
            candidate = f"{current} {part}".strip() if current else part
            if len(candidate) <= max_characters:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = part
    if current:
        chunks.append(current)
    return chunks


def _split_long_segment(value: str, max_characters: int) -> list[str]:
    if len(value) <= max_characters:
        return [value]

    parts: list[str] = []
    current = ""
    for word in value.split():
        if len(word) > max_characters:
            if current:
                parts.append(current)
                current = ""
            parts.extend(
                word[index : index + max_characters]
                for index in range(0, len(word), max_characters)
            )
            continue
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_characters:
            current = candidate
        else:
            parts.append(current)
            current = word
    if current:
        parts.append(current)
    return parts

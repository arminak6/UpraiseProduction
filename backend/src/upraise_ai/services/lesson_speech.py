"""V3 speech preparation/WAV generation, with the required A3 viseme adapter."""
import hashlib
import json
import math
import re
from pathlib import Path

from botocore.config import Config

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import ReviewIssue, Viseme
from upraise_ai.services.aws import aws_session
from upraise_ai.services.v3.math_speech import CachedMathNarrationFallback, MathJaxSpeechRuleConverter
from upraise_ai.services.v3.polly_speech import (
    _MATH_PATTERN,
    PollySpeechSynthesizerV3,
    PreparedSpeech,
    prepare_speech_document,
    split_speech_ssml,
)

MATH_PATTERN = _MATH_PATTERN
SUPPORTED_MATH_LOCALES = {"en", "de", "fr", "es", "it", "ca", "hi", "nb", "nn", "sv"}


def convert_math(formula, language):
    locale = language.split("-")[0].lower()
    if locale not in SUPPORTED_MATH_LOCALES:
        return None
    try:
        return MathJaxSpeechRuleConverter(locale=locale).convert([formula])[0]
    except (OSError, RuntimeError, ValueError):
        return None


def speech_units(text):
    """Keep delimited formulas atomic when splitting teaching text."""
    units, last = [], 0
    for match in MATH_PATTERN.finditer(text):
        units.extend(re.findall(r"\S+\s*", text[last:match.start()]))
        units.append(match.group() + " ")
        last = match.end()
    units.extend(re.findall(r"\S+\s*", text[last:]))
    return units

def split_words(text, target):
    output, current, words = [], [], 0
    for unit in speech_units(text):
        if words >= target and current and re.search(r"[.!?][\"')\]]?\s*$", current[-1]):
            output.append("".join(current).strip())
            current, words = [], 0
        # Bound paragraphs without punctuation too; formulas remain atomic.
        if words >= target * 1.5 and current:
            output.append("".join(current).strip())
            current, words = [], 0
        current.append(unit)
        words += len(unit.split())
    if current:
        output.append("".join(current).strip())
    return output


class _Converter:
    def __init__(self, converter, language):
        self.converter, self.language = converter, language

    def convert(self, formulas):
        return [self.converter(formula, self.language) for formula in formulas]


class _LanguagePollyClient:
    def __init__(self, client, language_code):
        self.client, self.language_code = client, language_code

    def synthesize_speech(self, **request):
        return self.client.synthesize_speech(**request, LanguageCode=self.language_code)


def align_visemes(raw, duration_ms, offset):
    """Keep all valid Polly marks and fit their independent timeline to PCM."""
    parsed = []
    try:
        if not isinstance(raw, (str, bytes)):
            raise ValueError("Invalid mark response")
        for line in raw.splitlines():
            if not line.strip():
                continue
            mark = json.loads(line)
            if not isinstance(mark, dict):
                raise ValueError("Invalid mark object")
            if mark.get("type") != "viseme":
                continue
            time = mark["time"]
            value = mark["value"]
            if (isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time)
                    or time < 0 or not isinstance(value, str) or not value):
                raise ValueError("Invalid viseme fields")
            parsed.append((float(time), value))
    except (ValueError, KeyError, TypeError, OverflowError):
        raise IngestionError("LESSON_VISEMES_INVALID", retryable=True) from None
    parsed.sort(key=lambda mark: mark[0])
    last = parsed[-1][0] if parsed else 0
    scale = duration_ms / last if last > duration_ms else 1.0
    return [Viseme(time_ms=offset + time * scale, value=value) for time, value in parsed], scale < 1.0


class _ApiSynthesizer(PollySpeechSynthesizerV3):
    def _synthesize_chunks(self, chunks, *, engine, text_type):
        # Delegate actual PCM calls, stream validation and engine fallback to V3.
        # One-item delegation exposes each PCM segment's length for A3 offsets.
        self.marks, self.timing_issues, self.duration_ms = [], [], 0.0
        parts = []
        for chunk in chunks:
            pcm = super()._synthesize_chunks([chunk], engine=engine, text_type=text_type)
            if len(pcm) % 2:
                raise IngestionError("LESSON_AUDIO_INVALID", retryable=True)
            duration = len(pcm) / (self.sample_rate * 2) * 1000
            response = self.client.synthesize_speech(Engine=engine, OutputFormat="json",
                Text=chunk, TextType=text_type, VoiceId=self.voice_id, SpeechMarkTypes=["viseme"])
            stream = response.get("AudioStream")
            if stream is None:
                raise IngestionError("LESSON_VISEMES_INVALID", retryable=True)
            try:
                raw = stream.read()
            finally:
                stream.close()
            marks, adjusted = align_visemes(raw, duration, self.duration_ms)
            self.marks.extend(marks)
            if adjusted:
                self.timing_issues.append(ReviewIssue(category="speech", message=
                    "Polly viseme timing exceeded the PCM segment duration; its timeline was "
                    "rescaled to fit the audio. Review lip synchronization."))
            self.duration_ms += duration
            parts.append(pcm)
        return b"".join(parts)


class LessonSpeech:
    def __init__(self, settings, models, client=None, converter=convert_math):
        self.settings, self.models, self.converter = settings, models, converter
        self.client = client or aws_session(settings).client("polly", region_name=settings.aws_region,
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=10, read_timeout=120))
        self.last_timing_issues, self.last_engine = [], settings.polly_engine
        self._prepared, self._requests = None, None

    def validate_voice(self, voice, language):
        voices, token = [], None
        while True:
            response = self.client.describe_voices(Engine=self.settings.polly_engine,
                IncludeAdditionalLanguageCodes=True, **({"NextToken": token} if token else {}))
            voices.extend(response["Voices"])
            token = response.get("NextToken")
            if not token:
                break
        for item in voices:
            if item["Id"] != voice:
                continue
            codes = [item["LanguageCode"], *item.get("AdditionalLanguageCodes", [])]
            match = next((code for code in codes if code.lower() == language.lower()), None)
            if match is None and "-" not in language:
                match = next((code for code in codes if code.split("-")[0].lower() == language.lower()), None)
            if match and self.settings.polly_engine in item["SupportedEngines"]:
                return match
        raise IngestionError("LESSON_VOICE_LANGUAGE_UNSUPPORTED")

    def prepare(self, text, language):
        issues = []

        def fallback(requests):
            try:
                if hasattr(self.models, "narrate_math_fallback"):
                    values = self.models.narrate_math_fallback(requests, language)
                else:
                    values = [self.models.math_narration(request.latex, request.context, language)
                              for request in requests]
            except Exception:
                values = [None] * len(requests)
            if len(values) != len(requests) or any(not value for value in values):
                issues.append(ReviewIssue(category="speech", message=
                    "V3 math speech used its deterministic or structural fallback after contextual "
                    "narration was unavailable. Review the formula's audio."))
            return values

        guarded_fallback = fallback
        diagnostic_dir = getattr(self.models, "diagnostic_dir", None)
        if diagnostic_dir is not None:
            guarded_fallback = CachedMathNarrationFallback(fallback=fallback,
                cache_dir=Path(diagnostic_dir).parent / "math-narration")
        locale = language.split("-")[0].lower()
        converter = (_Converter(self.converter, language) if self.converter is not convert_math
                     or locale not in SUPPORTED_MATH_LOCALES else MathJaxSpeechRuleConverter(locale=locale))
        spoken_text = re.sub(r"\[CSV rows \d+-\d+\]", "", text)
        spoken_text = re.sub(r"\[Word blocks \d+-\d+\]", "", spoken_text)
        prepared = prepare_speech_document(spoken_text, converter=converter,
                                           fallback=guarded_fallback)
        if not prepared.plain_text:
            raise IngestionError("LESSON_SPEECH_EMPTY")
        # A3 requires SSML output and matching viseme requests. This transport
        # conversion also covers V3's plain-prose branch; speech parts are unchanged.
        self._prepared = PreparedSpeech(plain_text=prepared.plain_text, parts=prepared.parts, use_ssml=True)
        self._requests = split_speech_ssml(prepared.parts, max_characters=2800)
        full = "<speak>" + " ".join(request[7:-8] for request in self._requests) + "</speak>"
        return full, self._requests, issues

    def synthesize(self, requests, voice, language_code):
        if requests != self._requests or self._prepared is None:
            raise IngestionError("LESSON_SPEECH_NOT_PREPARED")
        native = _ApiSynthesizer(session=None, voice_id=voice, engine=self.settings.polly_engine,
            artifacts_dir=self.settings.data_dir, client=_LanguagePollyClient(self.client, language_code))
        audio, engine = native._synthesize_wav(self._prepared)
        if not native.marks:
            raise IngestionError("LESSON_VISEMES_MISSING_OR_UNORDERED", retryable=True)
        self.last_timing_issues, self.last_engine = native.timing_issues, engine
        return audio, native.marks, native.duration_ms / 1000, hashlib.sha256(audio).hexdigest()

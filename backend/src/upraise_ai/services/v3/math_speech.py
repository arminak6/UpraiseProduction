from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

from .utils import sha256_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONVERTER_SCRIPT = PROJECT_ROOT / "math_runtime" / "math_speech.mjs"
MAX_FORMULAS_PER_REQUEST = 100
MAX_FORMULA_CHARACTERS = 12_000
MATH_NARRATION_CACHE_VERSION = "math-narration-v3"
LOW_CONFIDENCE_SPEECH_MARKERS = (
    "left tack",
    "right tack",
    "number sign",
    "raised to the asterisk power",
    "unknown character",
    "unknown symbol",
    "approaches the limit",
    "empty union",
    "empty intersection",
    "empty ring above",
    "times empty",
    "empty times",
    "comma empty",
    "normal dollar",
)
_PRESENTATION_SPACING_PATTERN = re.compile(
    r"\\(?:[,!:;]|quad\b|qquad\b)|\\(?=\s)"
)
_PRESENTATION_DELIMITER_SIZE_PATTERN = re.compile(
    r"\\(?:big|Big|bigg|Bigg)[lrm]?\b"
)
_ALWAYS_CONTEXTUAL_LATEX_PATTERN = re.compile(
    r"\\(?:doteq|dotminus|mathring|vdash|dashv|models|Vdash|vDash|Vvdash)\b"
    r"|\^\s*(?:\{\s*\*\s*\}|\*)"
    r"|\\\$"
)
_RAW_SLASH_PATTERN = re.compile(r"(?<!\\)/")
_AMBIGUOUS_ADJACENCY_SPACE_PATTERN = re.compile(
    r"(?<=[A-Za-z0-9}])\\,(?=[A-Za-z0-9\\])"
)
_SLASH_CONTEXT_PATTERN = re.compile(
    r"\b(?:automaton|machine|read|stack|tape|transition|turing|write)\b",
    flags=re.IGNORECASE,
)
_PROPER_SUBTRACTION_CONTEXT_PATTERN = re.compile(
    r"\b(?:monus|proper subtract(?:ion|or)?|truncated subtract(?:ion|or)?)\b",
    flags=re.IGNORECASE,
)
_FORMAL_LANGUAGE_CONTEXT_PATTERN = re.compile(
    r"\b(?:formal language|kleene|regular expression|regular language)\b",
    flags=re.IGNORECASE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class MathNarrationRequest:
    latex: str
    context: str = ""


class MathSpeechConverter(Protocol):
    def convert(self, formulas: Sequence[str]) -> list[str | None]: ...


class MathNarrationFallback(Protocol):
    def __call__(
        self,
        requests: Sequence[MathNarrationRequest],
    ) -> list[str | None]: ...


class MathJaxSpeechRuleConverter:
    """Convert LaTeX to general ClearSpeak using MathJax and SRE."""

    def __init__(
        self,
        *,
        node_executable: str = "node",
        script_path: Path = DEFAULT_CONVERTER_SCRIPT,
        locale: str = "en",
        domain: str = "clearspeak",
        timeout_seconds: float = 30.0,
    ):
        self.node_executable = node_executable
        self.script_path = script_path.resolve()
        self.locale = locale
        self.domain = domain
        self.timeout_seconds = timeout_seconds

    def convert(self, formulas: Sequence[str]) -> list[str | None]:
        if not formulas:
            return []
        if len(formulas) > MAX_FORMULAS_PER_REQUEST:
            raise ValueError(
                f"At most {MAX_FORMULAS_PER_REQUEST} formulas can be narrated "
                "in one request."
            )
        cleaned = [normalize_latex_for_speech(formula) for formula in formulas]
        if any(
            not formula or len(formula) > MAX_FORMULA_CHARACTERS
            for formula in cleaned
        ):
            raise ValueError(
                "Each formula must contain between 1 and "
                f"{MAX_FORMULA_CHARACTERS} characters."
            )
        if shutil.which(self.node_executable) is None:
            raise RuntimeError("The Node.js math-speech runtime is unavailable.")
        if not self.script_path.is_file():
            raise RuntimeError("The MathJax speech converter script is unavailable.")

        request = json.dumps(
            {
                "formulas": cleaned,
                "locale": self.locale,
                "domain": self.domain,
            },
            ensure_ascii=False,
        )
        try:
            completed = subprocess.run(
                [self.node_executable, str(self.script_path)],
                input=request,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
                cwd=PROJECT_ROOT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("The math-speech converter could not run.") from exc
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                "The math-speech converter returned an invalid response."
            ) from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if completed.returncode != 0 or not isinstance(results, list):
            raise RuntimeError(
                "The math-speech converter rejected the formula request."
            )
        if len(results) != len(cleaned):
            raise RuntimeError(
                "The math-speech converter returned the wrong result count."
            )

        narrations: list[str | None] = []
        for result in results:
            speech = result.get("speech") if isinstance(result, dict) else None
            narrations.append(
                speech.strip() if isinstance(speech, str) and speech.strip() else None
            )
        return narrations


class CachedMathNarrationFallback:
    """Persist reviewed contextual narrations by formula and local context."""

    def __init__(
        self,
        *,
        fallback: MathNarrationFallback,
        cache_dir: Path,
    ):
        self.fallback = fallback
        self.cache_dir = cache_dir.resolve()
        self._lock = Lock()

    def __call__(
        self,
        requests: Sequence[MathNarrationRequest],
    ) -> list[str | None]:
        if not requests:
            return []

        keys = [self._cache_key(request) for request in requests]
        narrations: list[str | None] = [None] * len(requests)
        missing_by_key: dict[str, tuple[MathNarrationRequest, list[int]]] = {}
        with self._lock:
            for index, (key, request) in enumerate(zip(keys, requests, strict=True)):
                cached = self._read(key)
                if cached is not None:
                    narrations[index] = cached
                    continue
                if key not in missing_by_key:
                    missing_by_key[key] = (request, [])
                missing_by_key[key][1].append(index)

        if not missing_by_key:
            return narrations

        missing_items = list(missing_by_key.items())
        generated = self.fallback([item[1][0] for item in missing_items])
        if len(generated) != len(missing_items):
            raise RuntimeError(
                "The reviewed math narration returned the wrong result count."
            )

        with self._lock:
            for (key, (_request, indexes)), narration in zip(
                missing_items,
                generated,
                strict=True,
            ):
                cleaned = narration.strip() if isinstance(narration, str) else ""
                if not cleaned:
                    continue
                for index in indexes:
                    narrations[index] = cleaned
                self._write(key, cleaned)
        return narrations

    @staticmethod
    def _cache_key(request: MathNarrationRequest) -> str:
        normalized_context = _WHITESPACE_PATTERN.sub(
            " ", request.context[:600]
        ).strip()
        return sha256_text(
            "|".join(
                [
                    MATH_NARRATION_CACHE_VERSION,
                    normalize_latex_for_speech(request.latex),
                    normalized_context,
                ]
            )
        )

    def _read(self, key: str) -> str | None:
        path = self.cache_dir / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != MATH_NARRATION_CACHE_VERSION:
            return None
        narration = payload.get("narration")
        return (
            narration.strip()
            if isinstance(narration, str) and narration.strip()
            else None
        )

    def _write(self, key: str, narration: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{key}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": MATH_NARRATION_CACHE_VERSION,
                    "narration": narration,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)


def normalize_latex_for_speech(value: str) -> str:
    """Remove TeX presentation commands that must never become speech."""

    normalized = _PRESENTATION_SPACING_PATTERN.sub(" ", value.strip())
    normalized = _PRESENTATION_DELIMITER_SIZE_PATTERN.sub("", normalized)
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def contextual_narration_guidance(request: MathNarrationRequest) -> list[str]:
    """Return trusted general guidance for recognized ambiguity classes."""

    guidance: list[str] = []
    latex = request.latex
    context = request.context
    if (
        re.search(r"\\(?:doteq|dotminus)\b", latex)
        and _PROPER_SUBTRACTION_CONTEXT_PATTERN.search(context)
    ):
        guidance.append(
            "The dotted operator denotes proper or truncated subtraction here; "
            "say proper minus or truncated subtraction, not dot equals."
        )
    if (
        re.search(r"\^\s*(?:\{\s*\*\s*\}|\*)", latex)
        and _FORMAL_LANGUAGE_CONTEXT_PATTERN.search(context)
    ):
        guidance.append(
            "The superscript star is the Kleene star operator; say Kleene star, "
            "not an asterisk power."
        )
    if (
        _RAW_SLASH_PATTERN.search(latex)
        and _SLASH_CONTEXT_PATTERN.search(context)
    ):
        guidance.append(
            "The slash separates a transition's read and write symbols; say "
            "read the left symbol, write the right symbol, not divided by."
        )
    if re.search(
        r"\\vdash\s*_\s*(?:\{\s*[^}]+\s*\}|[A-Za-z0-9]+)"
        r"\s*\^\s*(?:\{\s*\*\s*\}|\*)",
        latex,
    ):
        guidance.append(
            "The decorated turnstile is a reflexive-transitive computation "
            "relation. Read the complete operator as yields under its subscript "
            "machine in zero or more steps. Never say right tack, sub, power, "
            "asterisk, or star for this decorated operator."
        )
    elif re.search(r"\\vdash\b", latex):
        guidance.append(
            "The turnstile is a one-step computation or derivation relation. "
            "Read it as yields in one step, including its subscript machine "
            "when present. Never say right tack."
        )
    elif re.search(r"\\(?:dashv|models|Vdash|vDash|Vvdash)\b", latex):
        guidance.append(
            "Read the turnstile as the contextual derivation, entailment, or "
            "transition relation; never call it a tack."
        )
    if r"\mathring" in latex:
        guidance.append(
            "Use context to name the ring-marked symbol; never say empty ring "
            "above."
        )
    if r"\$" in latex:
        guidance.append("Say dollar sign, not normal dollar.")
    if _AMBIGUOUS_ADJACENCY_SPACE_PATTERN.search(latex):
        guidance.append(
            "Use context to distinguish concatenation from implicit "
            "multiplication across the silent TeX spacing command."
        )
    return guidance


def convert_with_fallback(
    requests: Sequence[MathNarrationRequest],
    *,
    converter: MathSpeechConverter,
    fallback: MathNarrationFallback | None = None,
) -> list[str | None]:
    if not requests:
        return []
    try:
        narrations = converter.convert([request.latex for request in requests])
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "General math-to-speech conversion failed (%s); using the guarded "
            "fallback path.",
            type(exc).__name__,
        )
        narrations = [None] * len(requests)

    fallback_indices = [
        index
        for index, (request, narration) in enumerate(
            zip(requests, narrations, strict=True)
        )
        if not narration or _needs_contextual_narration(request, narration)
    ]
    if fallback is None or not fallback_indices:
        return narrations
    fallback_requests = [requests[index] for index in fallback_indices]
    try:
        replacements = fallback(fallback_requests)
    except Exception as exc:
        logger.warning(
            "Reviewed math-narration fallback failed (%s).",
            type(exc).__name__,
        )
        return narrations
    if len(replacements) != len(fallback_requests):
        logger.warning(
            "Reviewed math-narration fallback returned a wrong result count."
        )
        return narrations
    for index, replacement in zip(fallback_indices, replacements, strict=True):
        if isinstance(replacement, str) and replacement.strip():
            narrations[index] = replacement.strip()
    return narrations


def _needs_contextual_narration(
    request: MathNarrationRequest,
    narration: str,
) -> bool:
    lowered = narration.casefold()
    if any(marker in lowered for marker in LOW_CONFIDENCE_SPEECH_MARKERS):
        return True
    if _ALWAYS_CONTEXTUAL_LATEX_PATTERN.search(request.latex):
        return True
    if _AMBIGUOUS_ADJACENCY_SPACE_PATTERN.search(request.latex):
        return True
    return bool(
        _RAW_SLASH_PATTERN.search(request.latex)
        and _SLASH_CONTEXT_PATTERN.search(request.context)
    )

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
from typing import Any, Protocol

from .utils import sha256_text

from .math_speech import (
    LOW_CONFIDENCE_SPEECH_MARKERS,
    MAX_FORMULA_CHARACTERS,
    MAX_FORMULAS_PER_REQUEST,
    MathNarrationFallback,
    MathNarrationRequest,
    contextual_narration_guidance,
    normalize_latex_for_speech,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SEMANTIC_CONVERTER_SCRIPT = PROJECT_ROOT / "math_runtime" / "math_speech_v4.mjs"
SEMANTIC_NARRATION_CACHE_VERSION = "math-narration-v4.2-semantic-plan"
MAX_SEMANTIC_NODES_FOR_MODEL = 120
LONG_FORMULA_NODE_THRESHOLD = 24

_OVERLOADED_LATEX_PATTERN = re.compile(
    r"(?<!\\)[/|*]"
    r"|\\(?:mid|vert|Vert|parallel|ast|star|circ|bullet|cdot|times|"
    r"vdash|dashv|models|Vdash|vDash|Vvdash|doteq|dotminus|mathring)\b"
    r"|\^\s*(?:\{\s*\*\s*\}|\*)"
)
_DOMAIN_SENSITIVE_CONTEXT_PATTERN = re.compile(
    r"\b(?:automaton|chem(?:ical|istry)|convolution|formal language|kleene|"
    r"logic|matrix|pointer|probability|quantum|regular expression|set|"
    r"tensor|transition|turing|unit|vector)\b",
    flags=re.IGNORECASE,
)
_FORMAL_LANGUAGE_CONTEXT_PATTERN = re.compile(
    r"\b(?:formal language|kleene|regular expression|regular language)\b",
    flags=re.IGNORECASE,
)
_STRUCTURALLY_DIFFICULT_TYPES = {
    "cases",
    "matrix",
    "multiline",
    "table",
    "tensor",
    "vector",
}
_UNCERTAIN_SEMANTIC_VALUES = {"unknown", "unclassified", "text"}
_WHITESPACE_PATTERN = re.compile(r"\s+")
_DELIMITED_MATH_PATTERN = re.compile(
    r"\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)|"
    r"(?<!\\)(?<!\$)\$(?!\$).+?(?<!\\)\$(?!\$)",
    flags=re.DOTALL,
)
_PAGE_CITATION_SUFFIX_PATTERN = re.compile(
    r"(?P<citation>\s*\[(?:p\.\s*\d+|pp\.\s*\d+-\d+)\])"
    r"(?P<punctuation>[.;]?)\s*$",
    flags=re.IGNORECASE,
)
_MATH_MARKER_PATTERN = re.compile(
    r"[Α-Ͽ₀-₉₊₋₌₍₎⁰-⁹⁺⁻⁼⁽⁾]"
    r"|[∈∉⊂⊆⊄⊇×÷→↦⊢⊨≤≥≠≈≡∞∑∏∫√∪∩∧∨¬]"
    r"|(?<!\\)[_^](?=[A-Za-z0-9{*])"
    r"|\\[A-Za-z]+\b"
    r"|(?<!\w)[A-Za-z]\s*(?:=|:)\s*(?=[A-Za-z0-9({\\Α-Ͽ])"
)
_STRONG_OPERATOR_PATTERN = re.compile(
    r"[=∈∉⊂⊆⊄⊇×÷→↦⊢⊨≤≥≠≈≡∑∏∫√∪∩]"
    r"|\\(?:in|notin|subset|subseteq|supseteq|times|to|rightarrow|"
    r"vdash|models|leq|geq|neq|sum|prod|int|sqrt|cup|cap)\b"
)
_LONG_WORD_PATTERN = re.compile(r"\b[A-Za-z]{3,}\b")
_LIST_DEFINITION_PATTERN = re.compile(
    r"^(?P<prefix>\s*(?:[-+*]|\d+[.)])\s+)"
    r"(?P<formula>.+?)"
    r"(?P<suffix>\s+(?:is|denotes|means|represents)\b.+)$",
    flags=re.IGNORECASE,
)
_FUNCTION_EQUATION_PATTERN = re.compile(
    r"(?P<formula>"
    r"(?:[A-Za-zΑ-Ͽ](?:[₀-₉]|_[A-Za-z0-9{}-]+)*)"
    r"\s*\([^\n.;]*?\)\s*=\s*\([^\n.;]*?\)"
    r")"
)
_TUPLE_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<formula>"
    r"(?:[A-Za-zΑ-Ͽ](?:[₀-₉]|_[A-Za-z0-9{}-]+)*)"
    r"\s*=\s*\([^\n.;]+?\)"
    r")"
)
_SHORT_RELATION_PATTERN = re.compile(
    r"(?P<formula>"
    r"(?:[A-Za-zΑ-Ͽ][A-Za-z0-9Α-Ͽ₀-₉_{}^*+-]*|\d+)"
    r"\s*(?:∈|∉|⊂|⊆|⊇|≤|≥|≠|≈|=|\\in\b|\\subseteq\b|\\leq\b|\\geq\b)"
    r"\s*(?:[A-Za-zΑ-Ͽ][A-Za-z0-9Α-Ͽ₀-₉_{}^*+-]*|\d+)"
    r")"
)
_MATH_ATOM_PATTERN = re.compile(
    r"(?P<formula>"
    r"(?:[A-Za-zΑ-Ͽ]"
    r"(?:[₀-₉]+|_(?:\{[^}\n]+\}|[A-Za-z0-9]+))"
    r"(?:[⁰-⁹]+|\^(?:\{[^}\n]+\}|[A-Za-z0-9*+-]+))?"
    r")"
    r"|(?:[⊢⊨∑∏∫]"
    r"(?:_(?:\{[^}\n]+\}|[A-Za-z0-9]+))?"
    r"(?:\^(?:\{[^}\n]+\}|[A-Za-z0-9*+-]+))?"
    r")"
    r"|[Α-Ͽ]"
    r")"
)

_GREEK_LATEX = {
    "Α": "A",
    "Β": "B",
    "Γ": r"\Gamma",
    "Δ": r"\Delta",
    "Ε": "E",
    "Ζ": "Z",
    "Η": "H",
    "Θ": r"\Theta",
    "Ι": "I",
    "Κ": "K",
    "Λ": r"\Lambda",
    "Μ": "M",
    "Ν": "N",
    "Ξ": r"\Xi",
    "Ο": "O",
    "Π": r"\Pi",
    "Ρ": "P",
    "Σ": r"\Sigma",
    "Τ": "T",
    "Υ": r"\Upsilon",
    "Φ": r"\Phi",
    "Χ": "X",
    "Ψ": r"\Psi",
    "Ω": r"\Omega",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\epsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "ι": r"\iota",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ξ": r"\xi",
    "ο": "o",
    "π": r"\pi",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "τ": r"\tau",
    "υ": r"\upsilon",
    "φ": r"\phi",
    "χ": r"\chi",
    "ψ": r"\psi",
    "ω": r"\omega",
}
_OPERATOR_LATEX = {
    "∈": r"\in",
    "∉": r"\notin",
    "⊂": r"\subset",
    "⊆": r"\subseteq",
    "⊄": r"\nsubseteq",
    "⊇": r"\supseteq",
    "×": r"\times",
    "÷": r"\div",
    "→": r"\to",
    "↦": r"\mapsto",
    "⊢": r"\vdash",
    "⊨": r"\models",
    "≤": r"\leq",
    "≥": r"\geq",
    "≠": r"\neq",
    "≈": r"\approx",
    "≡": r"\equiv",
    "∞": r"\infty",
    "∑": r"\sum",
    "∏": r"\prod",
    "∫": r"\int",
    "√": r"\sqrt",
    "∪": r"\cup",
    "∩": r"\cap",
    "∧": r"\land",
    "∨": r"\lor",
    "¬": r"\neg",
    "…": r"\ldots",
    "−": "-",
}
_SUBSCRIPT_LATEX = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎",
    "0123456789+-=()",
)
_SUPERSCRIPT_LATEX = str.maketrans(
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾",
    "0123456789+-=()",
)
_SUBSCRIPT_CHARACTERS = set("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_SUPERSCRIPT_CHARACTERS = set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
_STARRED_TURNSTILE_SPEECH_PATTERN = re.compile(
    r"\bright tack(?: (?:sub|subscript) (?P<machine>[A-Za-z0-9]+))? "
    r"(?:raised )?to the asterisk(?: power)?\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class SemanticNode:
    node_id: int
    node_type: str
    role: str
    text: str
    children: tuple[int, ...]
    content: tuple[int, ...]

    def prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "type": self.node_type,
            "role": self.role,
            "text": self.text,
            "children": list(self.children),
            "content": list(self.content),
        }


@dataclass(frozen=True)
class SemanticMathAnalysis:
    latex: str
    baseline_speech: str
    mathml: str
    enriched_mathml: str
    nodes: tuple[SemanticNode, ...]
    max_depth: int

    @property
    def required_node_ids(self) -> tuple[int, ...]:
        return tuple(sorted(node.node_id for node in self.nodes))


@dataclass(frozen=True)
class SemanticNarrationRequest:
    latex: str
    context: str
    baseline_speech: str
    semantic_nodes: tuple[SemanticNode, ...]
    required_node_ids: tuple[int, ...]
    risk_reasons: tuple[str, ...]
    mode: str
    locale: str = "en"
    domain: str = "clearspeak"

    def prompt_dict(self, formula_id: int) -> dict[str, Any]:
        return {
            "formula_id": formula_id,
            "latex": self.latex,
            "context": self.context[:600],
            "baseline_speech": self.baseline_speech,
            "mode": self.mode,
            "risk_reasons": list(self.risk_reasons),
            "application_guidance": contextual_narration_guidance(
                MathNarrationRequest(latex=self.latex, context=self.context)
            ),
            "required_node_ids": list(self.required_node_ids),
            "semantic_nodes": [node.prompt_dict() for node in self.semantic_nodes],
        }


class SemanticMathConverter(Protocol):
    def analyze(
        self,
        formulas: Sequence[str],
    ) -> list[SemanticMathAnalysis | None]: ...


class SemanticNarrationFallback(Protocol):
    def __call__(
        self,
        requests: Sequence[SemanticNarrationRequest],
    ) -> list[str | None]: ...


class MathJaxSemanticConverter:
    """Return ClearSpeak and the SRE semantic tree for each LaTeX expression."""

    def __init__(
        self,
        *,
        node_executable: str = "node",
        script_path: Path = DEFAULT_SEMANTIC_CONVERTER_SCRIPT,
        locale: str = "en",
        domain: str = "clearspeak",
        timeout_seconds: float = 30.0,
    ):
        self.node_executable = node_executable
        self.script_path = script_path.resolve()
        self.locale = locale
        self.domain = domain
        self.timeout_seconds = timeout_seconds

    def analyze(
        self,
        formulas: Sequence[str],
    ) -> list[SemanticMathAnalysis | None]:
        if not formulas:
            return []
        if len(formulas) > MAX_FORMULAS_PER_REQUEST:
            raise ValueError(
                f"At most {MAX_FORMULAS_PER_REQUEST} formulas can be analyzed "
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
            raise RuntimeError("The Node.js semantic math runtime is unavailable.")
        if not self.script_path.is_file():
            raise RuntimeError("The V4 semantic math converter is unavailable.")

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
            raise RuntimeError("The semantic math converter could not run.") from exc
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                "The semantic math converter returned an invalid response."
            ) from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if completed.returncode != 0 or not isinstance(results, list):
            raise RuntimeError("The semantic math converter rejected the request.")
        if len(results) != len(cleaned):
            raise RuntimeError(
                "The semantic math converter returned the wrong result count."
            )

        analyses: list[SemanticMathAnalysis | None] = []
        for latex, result in zip(cleaned, results, strict=True):
            try:
                analyses.append(self._parse_analysis(latex, result))
            except (TypeError, ValueError):
                analyses.append(None)
        return analyses

    @staticmethod
    def _parse_analysis(
        latex: str,
        result: Any,
    ) -> SemanticMathAnalysis:
        if not isinstance(result, dict):
            raise TypeError("A semantic conversion result must be an object.")
        speech = result.get("speech")
        mathml = result.get("mathml")
        enriched_mathml = result.get("enrichedMathml")
        semantic_tree = result.get("semanticTree")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (speech, mathml, enriched_mathml)
        ):
            raise ValueError("A semantic conversion result is incomplete.")
        if not isinstance(semantic_tree, dict):
            raise ValueError("A semantic conversion result has no tree.")
        root = semantic_tree.get("stree")
        nodes, max_depth = _flatten_semantic_tree(root)
        if not nodes:
            raise ValueError("The semantic tree is empty.")
        return SemanticMathAnalysis(
            latex=latex,
            baseline_speech=speech.strip(),
            mathml=mathml,
            enriched_mathml=enriched_mathml,
            nodes=tuple(sorted(nodes, key=lambda node: node.node_id)),
            max_depth=max_depth,
        )


class CachedSemanticNarrationFallback:
    """Cache reviewed V4 speech plans by semantic tree, mode, and context."""

    def __init__(
        self,
        *,
        fallback: SemanticNarrationFallback,
        cache_dir: Path,
    ):
        self.fallback = fallback
        self.cache_dir = cache_dir.resolve()
        self._lock = Lock()

    def __call__(
        self,
        requests: Sequence[SemanticNarrationRequest],
    ) -> list[str | None]:
        if not requests:
            return []
        keys = [self._cache_key(request) for request in requests]
        narrations: list[str | None] = [None] * len(requests)
        missing: dict[str, tuple[SemanticNarrationRequest, list[int]]] = {}
        with self._lock:
            for index, (key, request) in enumerate(zip(keys, requests, strict=True)):
                cached = self._read(key)
                if cached is not None:
                    narrations[index] = cached
                else:
                    missing.setdefault(key, (request, []))[1].append(index)
        if not missing:
            return narrations

        missing_items = list(missing.items())
        generated = self.fallback([item[1][0] for item in missing_items])
        if len(generated) != len(missing_items):
            raise RuntimeError("The V4 math narration returned a wrong result count.")
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
    def _cache_key(request: SemanticNarrationRequest) -> str:
        canonical_nodes = [node.prompt_dict() for node in request.semantic_nodes]
        normalized_context = _WHITESPACE_PATTERN.sub(
            " ", request.context[:600]
        ).strip()
        return sha256_text(
            json.dumps(
                {
                    "version": SEMANTIC_NARRATION_CACHE_VERSION,
                    "latex": normalize_latex_for_speech(request.latex),
                    "semantic_nodes": canonical_nodes,
                    "context": normalized_context,
                    "mode": request.mode,
                    "locale": request.locale,
                    "domain": request.domain,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
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
        if payload.get("version") != SEMANTIC_NARRATION_CACHE_VERSION:
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
                    "version": SEMANTIC_NARRATION_CACHE_VERSION,
                    "narration": narration,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)


def semantic_risk_reasons(
    analysis: SemanticMathAnalysis,
    *,
    context: str,
) -> tuple[str, ...]:
    """Classify structural ambiguity without relying on one course's notation."""

    reasons: list[str] = []
    lowered_speech = analysis.baseline_speech.casefold()
    if any(marker in lowered_speech for marker in LOW_CONFIDENCE_SPEECH_MARKERS):
        reasons.append("low_confidence_baseline")
    if _OVERLOADED_LATEX_PATTERN.search(analysis.latex):
        reasons.append("overloaded_operator")
    if _DOMAIN_SENSITIVE_CONTEXT_PATTERN.search(context):
        overloaded_roles = {
            "division",
            "multiplication",
            "set",
            "suchthat",
            "unknown",
        }
        if any(node.role in overloaded_roles for node in analysis.nodes):
            reasons.append("domain_sensitive_context")
    if any(
        node.node_type in _STRUCTURALLY_DIFFICULT_TYPES
        for node in analysis.nodes
    ):
        reasons.append("structured_layout")
    if any(
        node.node_type in _UNCERTAIN_SEMANTIC_VALUES
        or node.role in _UNCERTAIN_SEMANTIC_VALUES
        for node in analysis.nodes
    ):
        reasons.append("uncertain_semantics")
    if "<merror" in analysis.mathml or "\\color{red}" in analysis.mathml:
        reasons.append("parser_warning")
    if len(analysis.nodes) > LONG_FORMULA_NODE_THRESHOLD or analysis.max_depth > 7:
        reasons.append("complex_expression")
    return tuple(dict.fromkeys(reasons))


def narration_mode(
    analysis: SemanticMathAnalysis,
    *,
    configured_mode: str,
) -> str:
    if configured_mode == "detailed":
        return "detailed"
    if configured_mode == "concise":
        return "concise"
    if configured_mode != "auto":
        raise ValueError("Math speech mode must be auto, detailed, or concise.")
    if (
        len(analysis.nodes) > LONG_FORMULA_NODE_THRESHOLD
        or analysis.max_depth > 7
    ):
        return "summary_then_detail"
    return "detailed"


def has_unwrapped_math(value: str) -> bool:
    """Return whether strong math notation occurs outside supported delimiters."""

    without_delimited_math = _DELIMITED_MATH_PATTERN.sub(" ", value)
    return bool(_MATH_MARKER_PATTERN.search(without_delimited_math))


def auto_delimit_math_for_speech(value: str) -> str:
    """Recover common Unicode/plain formula spans before V4 speech analysis.

    This is a safety net for legacy lectures. Newly generated lectures are
    required to preserve explicit LaTeX delimiters and are validated separately.
    """

    if not value or not has_unwrapped_math(value):
        return value
    output: list[str] = []
    cursor = 0
    for match in _DELIMITED_MATH_PATTERN.finditer(value):
        output.append(_auto_delimit_plain_text(value[cursor : match.start()]))
        output.append(match.group(0))
        cursor = match.end()
    output.append(_auto_delimit_plain_text(value[cursor:]))
    return "".join(output)


def unicode_math_to_latex(value: str) -> str:
    """Convert common Unicode math glyphs to equivalent MathJax-safe LaTeX."""

    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character in _SUBSCRIPT_CHARACTERS:
            end = index + 1
            while end < len(value) and value[end] in _SUBSCRIPT_CHARACTERS:
                end += 1
            output.append("_{" + value[index:end].translate(_SUBSCRIPT_LATEX) + "}")
            index = end
            continue
        if character in _SUPERSCRIPT_CHARACTERS:
            end = index + 1
            while end < len(value) and value[end] in _SUPERSCRIPT_CHARACTERS:
                end += 1
            output.append("^{" + value[index:end].translate(_SUPERSCRIPT_LATEX) + "}")
            index = end
            continue
        replacement = _GREEK_LATEX.get(character) or _OPERATOR_LATEX.get(character)
        if replacement is not None:
            output.append(f" {replacement} ")
        else:
            output.append(" " if character in {"\u00a0", "\u202f"} else character)
        index += 1
    return _WHITESPACE_PATTERN.sub(" ", "".join(output)).strip()


def repair_mechanical_narration(
    *,
    latex: str,
    context: str,
    narration: str,
) -> str:
    """Apply high-confidence semantic repairs when SRE exposes glyph names."""

    repaired = narration.strip()
    if re.search(
        r"\\vdash\s*_\s*(?:\{\s*[^}]+\s*\}|[A-Za-z0-9]+)"
        r"\s*\^\s*(?:\{\s*\*\s*\}|\*)",
        latex,
    ):
        repaired = _STARRED_TURNSTILE_SPEECH_PATTERN.sub(
            lambda match: (
                "yields under "
                + match.group("machine").strip()
                + " in zero or more steps"
                if match.group("machine")
                else "yields in zero or more steps"
            ),
            repaired,
        )
    if (
        re.search(r"\^\s*(?:\{\s*\*\s*\}|\*)", latex)
        and _FORMAL_LANGUAGE_CONTEXT_PATTERN.search(context)
    ):
        repaired = re.sub(
            r"raised to the asterisk power",
            "Kleene star",
            repaired,
            flags=re.IGNORECASE,
        )
    return repaired


def _auto_delimit_plain_text(value: str) -> str:
    return "".join(_auto_delimit_line(line) for line in value.splitlines(keepends=True))


def _auto_delimit_line(line: str) -> str:
    newline = ""
    body = line
    if body.endswith("\r\n"):
        body, newline = body[:-2], "\r\n"
    elif body.endswith(("\n", "\r")):
        body, newline = body[:-1], body[-1]
    if not _MATH_MARKER_PATTERN.search(body):
        return line

    list_definition = _LIST_DEFINITION_PATTERN.match(body)
    if list_definition and _MATH_MARKER_PATTERN.search(
        list_definition.group("formula")
    ):
        formula = unicode_math_to_latex(list_definition.group("formula"))
        suffix = _wrap_inline_formulas(
            list_definition.group("suffix"),
            (
                _FUNCTION_EQUATION_PATTERN,
                _TUPLE_ASSIGNMENT_PATTERN,
                _SHORT_RELATION_PATTERN,
                _MATH_ATOM_PATTERN,
            ),
        )
        return (
            list_definition.group("prefix")
            + rf"\({formula}\)"
            + suffix
            + newline
        )

    citation = ""
    citation_punctuation = ""
    content = body
    citation_match = _PAGE_CITATION_SUFFIX_PATTERN.search(content)
    if citation_match:
        citation = citation_match.group("citation")
        citation_punctuation = citation_match.group("punctuation")
        content = content[: citation_match.start()]
    stripped = content.strip()
    leading = content[: len(content) - len(content.lstrip())]
    trailing = content[len(content.rstrip()) :]
    formula_punctuation = citation_punctuation
    if stripped.endswith((".", ";")):
        stripped, formula_punctuation = stripped[:-1].rstrip(), stripped[-1]

    if _looks_like_formula(stripped) and not stripped.startswith("#"):
        connector_match = re.search(r"\s+(with|where)\s+", stripped)
        if connector_match:
            left = stripped[: connector_match.start()].strip()
            right = stripped[connector_match.end() :].strip()
            if _looks_like_formula(left) and _looks_like_formula(right):
                return (
                    leading
                    + rf"\[{unicode_math_to_latex(left)}\]"
                    + f" {connector_match.group(1)} "
                    + rf"\({unicode_math_to_latex(right)}\)"
                    + trailing
                    + citation
                    + formula_punctuation
                    + newline
                )
        return (
            leading
            + rf"\[{unicode_math_to_latex(stripped)}\]"
            + trailing
            + citation
            + formula_punctuation
            + newline
        )

    repaired = _wrap_inline_formulas(
        content,
        (
        _FUNCTION_EQUATION_PATTERN,
        _TUPLE_ASSIGNMENT_PATTERN,
        _SHORT_RELATION_PATTERN,
            _MATH_ATOM_PATTERN,
        ),
    )
    return repaired + citation + citation_punctuation + newline


def _wrap_detected_formula(match: re.Match[str]) -> str:
    formula = unicode_math_to_latex(match.group("formula"))
    return rf"\({formula}\)"


def _wrap_inline_formulas(
    value: str,
    patterns: Sequence[re.Pattern[str]],
) -> str:
    matches: list[re.Match[str]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in patterns:
        for match in pattern.finditer(value):
            bounds = match.span("formula")
            if any(bounds[0] < end and bounds[1] > start for start, end in occupied):
                continue
            matches.append(match)
            occupied.append(bounds)
    if not matches:
        return value
    output: list[str] = []
    cursor = 0
    for match in sorted(matches, key=lambda item: item.start("formula")):
        start, end = match.span("formula")
        output.append(value[cursor:start])
        output.append(_wrap_detected_formula(match))
        cursor = end
    output.append(value[cursor:])
    return "".join(output)


def _looks_like_formula(value: str) -> bool:
    candidate = value.strip()
    if not candidate or not _MATH_MARKER_PATTERN.search(candidate):
        return False
    marker_count = len(_MATH_MARKER_PATTERN.findall(candidate))
    operator_count = len(_STRONG_OPERATOR_PATTERN.findall(candidate))
    long_words = _LONG_WORD_PATTERN.findall(candidate)
    if len(long_words) > 1:
        return False
    return (
        operator_count >= 2
        or marker_count >= 3
        or (operator_count >= 1 and len(long_words) <= 2)
        or (marker_count >= 2 and len(long_words) <= 1)
    )


def convert_semantically(
    requests: Sequence[MathNarrationRequest],
    *,
    converter: SemanticMathConverter,
    semantic_fallback: SemanticNarrationFallback | None = None,
    legacy_fallback: MathNarrationFallback | None = None,
    mode: str = "auto",
    plan_all_formulas: bool = True,
) -> list[str | None]:
    """Create reviewed teaching speech, retaining SRE as the safe fallback."""

    if not requests:
        return []
    try:
        analyses = converter.analyze([request.latex for request in requests])
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "V4 semantic math conversion failed (%s).",
            type(exc).__name__,
        )
        analyses = [None] * len(requests)
    if len(analyses) != len(requests):
        raise RuntimeError("The V4 converter returned a wrong result count.")

    narrations: list[str | None] = [None] * len(requests)
    semantic_indexes: list[int] = []
    semantic_requests: list[SemanticNarrationRequest] = []
    legacy_indexes: list[int] = []

    for index, (request, analysis) in enumerate(
        zip(requests, analyses, strict=True)
    ):
        if analysis is None:
            legacy_indexes.append(index)
            continue
        baseline_speech = repair_mechanical_narration(
            latex=request.latex,
            context=request.context,
            narration=analysis.baseline_speech,
        )
        narrations[index] = baseline_speech
        risks = semantic_risk_reasons(analysis, context=request.context)
        should_plan = bool(risks) or plan_all_formulas
        if (
            should_plan
            and semantic_fallback is not None
            and len(analysis.nodes) <= MAX_SEMANTIC_NODES_FOR_MODEL
        ):
            semantic_indexes.append(index)
            semantic_requests.append(
                SemanticNarrationRequest(
                    latex=request.latex,
                    context=request.context,
                    baseline_speech=baseline_speech,
                    semantic_nodes=analysis.nodes,
                    required_node_ids=analysis.required_node_ids,
                    risk_reasons=risks or ("teacher_narration",),
                    mode=narration_mode(analysis, configured_mode=mode),
                )
            )

    if semantic_requests and semantic_fallback is not None:
        try:
            replacements = semantic_fallback(semantic_requests)
            if len(replacements) != len(semantic_requests):
                raise RuntimeError("V4 narration returned a wrong result count.")
            for index, replacement in zip(
                semantic_indexes,
                replacements,
                strict=True,
            ):
                if isinstance(replacement, str) and replacement.strip():
                    narrations[index] = replacement.strip()
        except Exception as exc:
            logger.warning(
                "Reviewed V4 semantic narration failed (%s); keeping the "
                "deterministic narration.",
                type(exc).__name__,
            )

    if legacy_indexes and legacy_fallback is not None:
        try:
            replacements = legacy_fallback(
                [requests[index] for index in legacy_indexes]
            )
            if len(replacements) != len(legacy_indexes):
                raise RuntimeError("Legacy narration returned a wrong result count.")
            for index, replacement in zip(
                legacy_indexes,
                replacements,
                strict=True,
            ):
                if isinstance(replacement, str) and replacement.strip():
                    narrations[index] = replacement.strip()
        except Exception as exc:
            logger.warning(
                "V4 parser-failure narration failed (%s).",
                type(exc).__name__,
            )
    return narrations


def _flatten_semantic_tree(value: Any) -> tuple[list[SemanticNode], int]:
    nodes: list[SemanticNode] = []
    seen: set[int] = set()
    max_depth = 0

    def visit(raw: Any, depth: int) -> None:
        nonlocal max_depth
        if not isinstance(raw, dict):
            return
        raw_id = raw.get("id")
        try:
            node_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("A semantic node has an invalid ID.") from exc
        if node_id in seen:
            raise ValueError("A semantic node ID was duplicated.")
        seen.add(node_id)
        raw_children = raw.get("children", [])
        raw_content = raw.get("content", [])
        if not isinstance(raw_children, list) or not isinstance(raw_content, list):
            raise ValueError("A semantic node has invalid relationships.")

        child_ids = tuple(_semantic_child_id(child) for child in raw_children)
        content_ids = tuple(_semantic_child_id(child) for child in raw_content)
        nodes.append(
            SemanticNode(
                node_id=node_id,
                node_type=str(raw.get("type", "")).strip().casefold(),
                role=str(raw.get("role", "")).strip().casefold(),
                text=str(raw.get("$t", "")).strip(),
                children=child_ids,
                content=content_ids,
            )
        )
        max_depth = max(max_depth, depth)
        for child in [*raw_children, *raw_content]:
            visit(child, depth + 1)

    visit(value, 1)
    return nodes, max_depth


def _semantic_child_id(value: Any) -> int:
    if not isinstance(value, dict):
        raise ValueError("A semantic child must be an object.")
    try:
        return int(value.get("id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("A semantic child has an invalid ID.") from exc

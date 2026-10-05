from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from upraise_ai.services.v3.math_speech import (
    CachedMathNarrationFallback,
    MathJaxSpeechRuleConverter,
    MathNarrationRequest,
    contextual_narration_guidance,
    convert_with_fallback,
    normalize_latex_for_speech,
)


class _FixedConverter:
    def __init__(self, narrations: list[str | None]):
        self.narrations = narrations

    def convert(self, formulas):
        return self.narrations


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_mathjax_speech_rule_engine_handles_representative_turing_math() -> None:
    converter = MathJaxSpeechRuleConverter()

    narrations = converter.convert(
        [
            r"M=(Q,\Sigma,\Gamma,\delta,q_0,B,F)",
            r"L=\{a^n b^{2n} \mid n \ge 1\}",
            r"1+m+m^2+\cdots+m^n \le n m^{n+1}",
            (
                r"\delta(q,a,X_1,X_2,\ldots,X_k)="
                r"(p,\gamma_1,\gamma_2,\ldots,\gamma_k)"
            ),
        ]
    )

    assert all(narration for narration in narrations)
    assert "q sub 0" in (narrations[0] or "")
    assert "such that n is greater than or equal to 1" in (
        narrations[1] or ""
    )
    assert "m squared" in (narrations[2] or "")
    assert "is less than or equal to" in (narrations[2] or "")
    assert "gamma sub 1" in (narrations[3] or "")
    assert "X sub k" in (narrations[3] or "")


def test_latex_normalization_removes_only_presentation_commands() -> None:
    normalized = normalize_latex_for_speech(
        r"0^{m}\,1\!0^{n}\quad \bigl(q_0\bigr)"
    )

    assert normalized == r"0^{m} 1 0^{n} (q_0)"


def test_ambiguous_formula_and_context_use_fallback() -> None:
    fallback_requests: list[MathNarrationRequest] = []

    def fallback(requests):
        fallback_requests.extend(requests)
        return ["reviewed narration"] * len(requests)

    requests = [
        MathNarrationRequest(
            latex=r"m \doteq n",
            context="Proper subtraction.",
        ),
        MathNarrationRequest(
            latex="0/0",
            context="The Turing machine transition reads and writes a symbol.",
        ),
        MathNarrationRequest(
            latex="x/y",
            context="This is an ordinary arithmetic ratio.",
        ),
        MathNarrationRequest(
            latex=r"n\,m^n",
            context="A complexity bound.",
        ),
    ]
    narrations = convert_with_fallback(
        requests,
        converter=_FixedConverter(
            [
                "m approaches the limit n",
                "0 divided by 0",
                "x divided by y",
                "n m to the n-th power",
            ]
        ),
        fallback=fallback,
    )

    assert narrations == [
        "reviewed narration",
        "reviewed narration",
        "x divided by y",
        "reviewed narration",
    ]
    assert [request.latex for request in fallback_requests] == [
        r"m \doteq n",
        "0/0",
        r"n\,m^n",
    ]


def test_contextual_guidance_covers_general_ambiguity_classes() -> None:
    proper_subtraction = contextual_narration_guidance(
        MathNarrationRequest(
            latex=r"m \doteq n",
            context="The proper subtractor computes this operation.",
        )
    )
    transition = contextual_narration_guidance(
        MathNarrationRequest(
            latex="0/1",
            context="A Turing machine transition label.",
        )
    )
    kleene = contextual_narration_guidance(
        MathNarrationRequest(
            latex=r"0^{*}",
            context="This regular expression denotes a language.",
        )
    )
    computation = contextual_narration_guidance(
        MathNarrationRequest(
            latex=r"q_0w \vdash_M^* \alpha",
            context="An accepting computation.",
        )
    )

    assert "not dot equals" in proper_subtraction[0]
    assert "read the left symbol" in transition[0]
    assert "Kleene star" in kleene[0]
    assert "zero or more steps" in computation[0]
    assert "Never say right tack" in computation[0]


def test_reviewed_math_narration_cache_avoids_repeat_model_calls(
    tmp_path: Path,
) -> None:
    calls: list[list[MathNarrationRequest]] = []

    def fallback(requests):
        calls.append(list(requests))
        return ["zero Kleene star"] * len(requests)

    cached = CachedMathNarrationFallback(
        fallback=fallback,
        cache_dir=tmp_path / "narrations",
    )
    requests = [
        MathNarrationRequest(
            latex=r"0^{*}",
            context="A regular-language expression.",
        )
    ]

    assert cached(requests) == ["zero Kleene star"]
    assert cached(requests) == ["zero Kleene star"]
    assert len(calls) == 1
    assert len(list((tmp_path / "narrations").glob("*.json"))) == 1

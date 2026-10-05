from __future__ import annotations

import re
import unicodedata

_STANDALONE_LETTER_RE = re.compile(r"(?<!\w)[^\W\d_](?!\w)", re.UNICODE)
_LATEX_RE = re.compile(r"\\[A-Za-z]+|[_^]\s*\{")
_INDEX_RE = re.compile(
    r"(?<!\w)[^\W\d_]\s*(?:[_^]\s*(?:\{[^}]+\}|\S)|\d)(?![\w-])",
    re.UNICODE,
)
_SPACED_INDEX_RE = re.compile(
    r"(?<!\w)[^\W\d_]\s+\d(?![\w-])",
    re.UNICODE,
)
_FUNCTION_RE = re.compile(
    r"(?<!\w)[^\W\d_]\s*\([^)]{1,120}\)",
    re.UNICODE,
)
_ASCII_RELATION_RE = re.compile(r"(?:<=|>=|!=|==|->|<-|=>|<=>|(?<!\w)[=<>+\-*/](?!\w))")


def looks_like_inline_math(text: str) -> bool:
    """Return whether text has structural evidence of embedded mathematics.

    The detector intentionally does not map any observed bad glyph to a desired
    mathematical symbol. It uses Unicode categories and expression structure so
    it can route many kinds of course material to visual transcription.
    """

    cleaned = text.strip()
    if not cleaned:
        return False

    standalone_letters = _STANDALONE_LETTER_RE.findall(cleaned)
    standalone_count = len(standalone_letters)
    has_identifier_context = standalone_count > 0

    math_operators = 0
    modifier_symbols = 0
    superscript_or_subscript = 0
    for character in cleaned:
        category = unicodedata.category(character)
        name = unicodedata.name(character, "")
        if category == "Sm":
            math_operators += 1
        if category in {"Lm", "Sk"}:
            modifier_symbols += 1
        if "SUPERSCRIPT" in name or "SUBSCRIPT" in name:
            superscript_or_subscript += 1

    score = 0
    if math_operators:
        score += 3
    if superscript_or_subscript:
        score += 3
    if _LATEX_RE.search(cleaned):
        score += 3
    if _INDEX_RE.search(cleaned) or _SPACED_INDEX_RE.search(cleaned):
        score += 2
    if _FUNCTION_RE.search(cleaned):
        score += 2
    if _ASCII_RELATION_RE.search(cleaned) and has_identifier_context:
        score += 2

    standalone_math_letters = [
        character for character in standalone_letters if "GREEK" in unicodedata.name(character, "")
    ]
    if standalone_math_letters:
        score += 2
    if standalone_count >= 2:
        score += 1
    if standalone_count >= 4:
        score += 1
    if has_identifier_context and modifier_symbols:
        score += 2

    return score >= 2

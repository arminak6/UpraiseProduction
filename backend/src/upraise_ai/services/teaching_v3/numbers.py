"""Preserve decimal precision before a script reaches speech synthesis."""

import re
from decimal import Decimal, InvalidOperation

_END = r"(?!\w|\.\d)"
_DECIMAL = re.compile(r"(?<![\w.])[+-]?(?:\d+\.\d+(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)" + _END)
_NUMBER = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?" + _END)
_ONES = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20), strict=True))
_TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10), strict=True))
_DIGITS = {word: str(value) for word, value in _ONES.items() if value < 10}
_SMALL_WORDS = "|".join([*_ONES, *_TENS, "hundred", "and"])
_SPOKEN_DECIMAL = re.compile(r"\b(?P<sign>minus\s+|negative\s+)?(?P<whole>(?:(?:" + _SMALL_WORDS +
    r")[\s-]+){0,7}(?:" + _SMALL_WORDS + r"))\s+point\s+(?P<fraction>(?:" + "|".join(_DIGITS) +
    r")(?:[\s-]+(?:" + "|".join(_DIGITS) + r")){0,79})\b", re.I)


def decimal_value(text):
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _under_hundred(words):
    if len(words) == 1:
        return _ONES.get(words[0], _TENS.get(words[0]))
    if len(words) == 2 and words[0] in _TENS and 0 < _ONES.get(words[1], 100) < 10:
        return _TENS[words[0]] + _ONES[words[1]]
    return None


def _whole(words):
    if len(words) >= 2 and 0 < _ONES.get(words[0], 100) < 10 and words[1] == "hundred":
        rest = words[2:]
        if rest[:1] == ["and"]:
            rest = rest[1:]
        tail = _under_hundred(rest) if rest else 0
        return _ONES[words[0]] * 100 + tail if tail is not None else None
    return _under_hundred(words)


def normalize_decimals(written, spoken, language):
    """Normalize unambiguous English decimal words ONLY to an equal source value.

    This is a format change, never a guess at a missing zero or an unsourced value.
    Other languages and unsupported number grammar retain the guarded model repair.
    """
    if language.lower().split("-")[0] != "en":
        return spoken
    expected = {decimal_value(m.group()) for m in _DECIMAL.finditer(written.replace("−", "-"))}
    def replace(match):
        whole = _whole(re.split(r"[\s-]+", match['whole'].lower()))
        if whole is None:
            return match[0]
        fraction = ''.join(_DIGITS[w] for w in re.split(r"[\s-]+", match['fraction'].lower()))
        value = ("-" if match['sign'] else "") + str(whole) + "." + fraction
        return value if decimal_value(value) in expected else match[0]
    return _SPOKEN_DECIMAL.sub(replace, spoken)


def missing_decimals(written, spoken):
    """Numerically equivalent literals are valid; spelled decimals must be repaired.

    Integer operands may still be spoken naturally ('choose two'). Decimal values
    retain digits so leading zeroes do not depend on an LLM counting number words.
    Non-decimal mathematical notation remains subject to the meaning reviews.
    """
    available = {decimal_value(m.group()) for m in _NUMBER.finditer(spoken.replace("−", "-"))}
    return list(dict.fromkeys(m.group() for m in _DECIMAL.finditer(written.replace("−", "-"))
                             if decimal_value(m.group()) not in available))

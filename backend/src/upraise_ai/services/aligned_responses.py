"""Recover complete aligned JSON responses without inventing fields or content."""

import json
import re

from pydantic import ValidationError

RESPONSE_FORMAT_VERSION = "aligned-json-strings-v1"
MAX_RESPONSE_CHARACTERS = 200_000
_CONTROL_WORD = re.compile(r"[A-Za-z]+")
_JSON_LOOKING_TEX = {"bar", "beta", "begin", "binom", "boldsymbol", "bf", "big", "bigl", "bigr",
                     "frac", "forall", "fbox", "nu", "nabla", "ne", "neq", "not", "nonumber",
                     "rho", "right", "rm", "tau", "theta", "text", "textrm", "textsf", "texttt",
                     "textnormal", "textit", "textbf", "times", "to", "top", "tilde", "underbrace"}


class ResponseFormatError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def repair_string_escapes(raw, *, detect_ambiguous=False):
    r"""Repair only JSON string escaping, retaining literal LaTeX characters.

    Quotes, field names, IDs, arrays and object boundaries are never guessed.
    Valid JSON escapes remain intact, except unescaped TeX control words inside
    visible math delimiters (e.g. a raw \text must not become a tab plus 'ext').
    """
    output, inside, math, index = [], False, None, 0
    while index < len(raw):
        char = raw[index]
        if char == '"':
            inside = not inside
            math = None
            output.append(char)
            index += 1
            continue
        if not inside:
            output.append(char)
            index += 1
            continue
        if char == "$":
            delimiter = "$$" if raw[index:index + 2] == "$$" else "$"
            if math is None:
                math = delimiter
            elif math == delimiter:
                math = None
            output.append(delimiter)
            index += len(delimiter)
            continue
        if char == "\\" and index + 1 < len(raw):
            next_char = raw[index + 1]
            if next_char == "\\":
                # Already correctly escaped backslash; never decode it twice.
                following = raw[index + 2:index + 3]
                if following in ("(", "["):
                    math = ")" if following == "(" else "]"
                elif following in (")", "]") and following == math:
                    math = None
                output.append(raw[index:index + 2])
                index += 2
                continue
            if next_char in ("(", "["):
                math = ")" if next_char == "(" else "]"
            elif next_char in (")", "]") and next_char == math:
                math = None
            word = _CONTROL_WORD.match(raw, index + 1) if math else None
            if word and len(word[0]) > 1:
                if detect_ambiguous and word[0] in _JSON_LOOKING_TEX:
                    # E.g. raw \nu can encode newline+'u' or a damaged LaTeX
                    # command. Ask the model to encode it unambiguously.
                    raise ResponseFormatError("JSON_MATH_ESCAPE")
                output.append("\\\\" + word[0])
                index = word.end()
                continue
            if next_char not in '"\\/bfnrtu':
                output.append("\\\\" + next_char)
            else:
                output.append(raw[index:index + 2])
            index += 2
            continue
        output.append(json.dumps(char)[1:-1] if ord(char) < 32 else char)
        index += 1
    return "".join(output)


def validate_payload(payload, schema):
    try:
        parsed = schema.model_validate(payload)
    except ValidationError as error:
        raise ResponseFormatError("JSON_SCHEMA") from error

    def check(value):
        if isinstance(value, str):
            if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
                raise ResponseFormatError("JSON_STRING_CONTROL")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ResponseFormatError("JSON_STRING_CONTROL") from error
        elif isinstance(value, dict):
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(parsed.model_dump(mode="json"))
    return parsed


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResponseFormatError("AMBIGUOUS")
        result[key] = value
    return result


def complete_response(cleaned, schema):
    try:
        payload = json.loads(cleaned, object_pairs_hook=unique_fields)
    except json.JSONDecodeError:
        pass
    else:
        # A complete invalid outer object cannot hide a schema-valid nested one.
        return validate_payload(payload, schema)

    def root_start(offset):
        quoted = False
        while offset < len(cleaned):
            char = cleaned[offset]
            if quoted and char == "\\":
                offset += 2
                continue
            if char == '"':
                quoted = not quoted
            if not quoted and char in "{[":
                return offset
            offset += 1
        return -1

    decoder, start = json.JSONDecoder(object_pairs_hook=unique_fields), root_start(0)
    if start < 0:
        raise ResponseFormatError("JSON_SYNTAX")
    # Remove only redundant opening braces, never a field or array boundary.
    while cleaned[start] == "{":
        following = start + 1
        while following < len(cleaned) and cleaned[following].isspace():
            following += 1
        if following >= len(cleaned) or cleaned[following] != "{":
            break
        start = following
    try:
        payload, end = decoder.raw_decode(cleaned, start)
    except json.JSONDecodeError as error:
        # Never salvage a complete child from a truncated parent response.
        raise ResponseFormatError("JSON_SYNTAX") from error
    offset = end
    for _ in range(256):
        other = root_start(offset)
        if other < 0:
            break
        try:
            decoder.raw_decode(cleaned, other)
        except json.JSONDecodeError:
            offset = other + 1
            continue
        raise ResponseFormatError("AMBIGUOUS")
    else:
        raise ResponseFormatError("AMBIGUOUS")
    return validate_payload(payload, schema)


def parse_response(raw, schema):
    if not isinstance(raw, str) or len(raw) > MAX_RESPONSE_CHARACTERS:
        raise ResponseFormatError("OUTPUT_TOO_LARGE")
    cleaned = raw.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    try:
        # Preserve all correctly encoded strings, including math whitespace.
        parsed = complete_response(cleaned, schema)
        repair_string_escapes(cleaned, detect_ambiguous=True)
        return parsed
    except ResponseFormatError as error:
        if error.reason not in {"JSON_SYNTAX", "JSON_STRING_CONTROL"}:
            raise
        repaired = repair_string_escapes(cleaned)
        if repaired == cleaned:
            raise
    return complete_response(repaired, schema)

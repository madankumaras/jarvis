"""Convert spoken numbers to digits.

Scoped to what card and ticket IDs actually need: values below 10000.
"""
from __future__ import annotations

import re

_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000}

_NUMBER_WORDS = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_SCALES)


def _eval_arithmetic(words: list[str]) -> str:
    """Evaluate a run containing a scale word: 'three hundred eighty four' -> 384."""
    total = 0
    current = 0
    for w in words:
        if w in _SCALES:
            current = (current or 1) * _SCALES[w]
            total += current
            current = 0
        elif w in _TENS:
            current += _TENS[w]
        elif w in _TEENS:
            current += _TEENS[w]
        else:
            current += _UNITS[w]
    return str(total + current)


def _eval_concatenated(words: list[str]) -> str:
    """Concatenate digit fragments: 'three eighty four' -> '3' + '84' -> '384'."""
    parts: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _TENS:
            # A tens word absorbs a following unit: 'eighty four' -> '84'
            if i + 1 < len(words) and words[i + 1] in _UNITS and _UNITS[words[i + 1]] != 0:
                parts.append(str(_TENS[w] + _UNITS[words[i + 1]]))
                i += 2
                continue
            parts.append(str(_TENS[w]))
        elif w in _TEENS:
            parts.append(str(_TEENS[w]))
        else:
            parts.append(str(_UNITS[w]))
        i += 1
    return "".join(parts)


def _eval_run(words: list[str]) -> str:
    if any(w in _SCALES for w in words):
        return _eval_arithmetic(words)
    return _eval_concatenated(words)


def normalize_numbers(text: str) -> str:
    """Replace runs of spoken number-words with their digit form.

    Whitespace *inside* a run is absorbed ("three eighty four" -> "384").
    Whitespace *after* a run is preserved ("...four and six" -> "384 and 653").
    """
    tokens = re.split(r"(\W+)", text)
    out: list[str] = []
    run: list[str] = []
    pending_ws = ""

    def flush() -> None:
        if run:
            out.append(_eval_run(run))
            run.clear()

    for tok in tokens:
        low = tok.lower()
        if low in _NUMBER_WORDS:
            if pending_ws and not run:
                out.append(pending_ws)
            pending_ws = ""  # inter-number whitespace is absorbed
            run.append(low)
        elif tok.strip() == "":
            # re.split emits a trailing '' when the input ends in whitespace.
            # It carries no whitespace, so letting it reach pending_ws would
            # clobber the real separator and drop it: "384 " -> "384".
            if not tok:
                continue
            if run:
                pending_ws = tok  # may be inside the run, or trailing it
            else:
                out.append(tok)
        else:
            flush()
            if pending_ws:
                out.append(pending_ws)
                pending_ws = ""
            out.append(tok)

    flush()
    if pending_ws:
        out.append(pending_ws)
    return "".join(out)

"""Snap transcript tokens to real entity IDs from a live vocabulary."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from jarvis.correct.numbers import normalize_numbers
from jarvis.types import Vocab

SNAP_THRESHOLD = 0.85
ASK_THRESHOLD = 0.60

# "MCSL 384", "muscle-384", "M C S L 384", "mcsl384"
#
# Two alternatives, deliberately NOT one greedy letter-run: a single run of
# `[A-Za-z]\s*` happily matches across word boundaries and swallows the
# preceding word, turning "status of MCSL 384" into "status MCSL-384".
#   group 1: a solid word     -> "MCSL", "muscle"
#   group 2: spaced letters   -> "M C S L", "Z I"
_ID_PATTERN = re.compile(
    r"\b(?:([A-Za-z]{2,8})|((?:[A-Za-z]\s){1,7}[A-Za-z]))[\s\-]*(\d{2,4})\b"
)

# Whisper mishears are phonetic, not edit-distance-close: "muscle" scores only
# ~0.78 against "MCSL", below SNAP_THRESHOLD. Fuzzy matching alone cannot fix
# that, so known mishears are mapped explicitly. Grow this from command_log.
_PREFIX_ALIASES = {
    "mcsl": "MCSL",
    "muscle": "MCSL",
    "mussel": "MCSL",
    "michael": "MCSL",
    "zi": "ZI",
    "zed": "ZI",
    "zedi": "ZI",
    "zeti": "ZI",
}


@dataclass
class CorrectionResult:
    text: str
    ambiguous: list[tuple[str, str]] = field(default_factory=list)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _best_match(candidate: str, options: list[str]) -> tuple[str, float]:
    if not options:
        return "", 0.0
    scored = [(o, _ratio(candidate, o)) for o in options]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0]


def correct(text: str, vocab: Vocab) -> CorrectionResult:
    """Normalise numbers, then snap ID-shaped tokens to known entities."""
    text = normalize_numbers(text)
    ids = [*vocab.cards, *vocab.zi_ids]
    ambiguous: list[tuple[str, str]] = []

    if not ids:
        return CorrectionResult(text=text)

    def replace(m: re.Match[str]) -> str:
        raw_prefix = re.sub(r"\s+", "", m.group(1) or m.group(2)).lower()
        digits = m.group(3)
        prefix = _PREFIX_ALIASES.get(raw_prefix, raw_prefix.upper())
        candidate = f"{prefix}-{digits}"

        # An exact hit after aliasing needs no fuzzy scoring at all.
        for known in ids:
            if known.upper() == candidate.upper():
                return known

        best, score = _best_match(candidate, ids)
        # Only ever snap to an entity with the SAME digits. Never invent an ID.
        if best and best.split("-")[-1] != digits:
            return m.group(0)
        if score >= SNAP_THRESHOLD:
            return best
        if score >= ASK_THRESHOLD:
            ambiguous.append((m.group(0), best))
        return m.group(0)

    return CorrectionResult(text=_ID_PATTERN.sub(replace, text), ambiguous=ambiguous)

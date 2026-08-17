"""Shared dataclasses. No logic beyond trivial accessors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RpcError(Exception):
    """Raised when a worker call fails or times out."""


@dataclass
class Response:
    """What Jarvis says back.

    speech: short, spoken aloud. Keep it to one sentence.
    detail: full text for the macOS notification and the log.
    tier:   1 = local pipeline call, 2 = summarised, 3 = claude -p
    """

    speech: str
    detail: str = ""
    tier: int = 1
    ok: bool = True
    needs_confirm: bool = False
    # A PendingAction when needs_confirm is set: the side-effecting call that
    # has been built but deliberately not run. Typed as Any to keep types.py
    # free of router imports.
    pending: Any = None
    # True when Jarvis asked a question and the next utterance is the answer.
    awaiting: bool = False
    # True when the user said goodbye and the conversation should close.
    ends: bool = False


@dataclass
class Vocab:
    """Live entity snapshot used by the correction layer."""

    cards: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    carriers: list[str] = field(default_factory=list)
    zi_ids: list[str] = field(default_factory=list)

    def all(self) -> list[str]:
        return [*self.cards, *self.people, *self.carriers, *self.zi_ids]

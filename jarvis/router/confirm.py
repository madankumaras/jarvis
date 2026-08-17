"""Confirmation gate for anything with a side effect.

The rule, from the spec: anything that is not a clear yes is a no. Re-dictating
a command costs five seconds; a Slack message sent to the wrong colleague
cannot be taken back.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

Verdict = Literal["yes", "no", "unclear"]

DEFAULT_TIMEOUT = 30.0

# Anchored deliberately. "ok so actually make it the GLS store" is a
# correction, not consent, and must not match.
_YES = re.compile(
    r"^\W*(ok(ay)?|yes|yeah|yep|yup|sure|send( it)?|go( ahead)?|do it|confirm)\W*$",
    re.I,
)
_NO = re.compile(
    r"^\W*(no|nope|nah|cancel|stop|don'?t|do not|forget it|never\s?mind)\W*$",
    re.I,
)


def interpret(text: str) -> Verdict:
    """Classify a confirmation reply. Only a bare affirmative counts as yes."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "unclear"
    if _YES.match(cleaned):
        return "yes"
    if _NO.match(cleaned):
        return "no"
    return "unclear"


@dataclass
class PendingAction:
    """A side-effecting call that has been built but deliberately not run."""

    method: str
    params: dict[str, Any]
    speech: str
    detail: str = ""


@dataclass
class Confirmation:
    action: PendingAction
    timeout_seconds: float = DEFAULT_TIMEOUT
    created_at: float = field(default_factory=time.monotonic)
    settled: bool = False

    def expired(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return (now - self.created_at) >= self.timeout_seconds

    def resolve(self, text: str) -> bool:
        """True only on an explicit yes inside the window.

        Always settles, so a stray "ok" later in the session cannot re-fire an
        action the user already answered.
        """
        if self.settled or self.expired():
            self.settled = True
            return False
        self.settled = True
        return interpret(text) == "yes"

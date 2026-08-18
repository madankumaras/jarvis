"""Conversation state: what was just talked about, and what is half-said.

Two jobs:

  * Remember the last card, release and person mentioned, so "go through that
    card" means something.
  * Hold a partially-filled action, so "send a message" can be completed by
    asking "to whom?" and "what should I say?" instead of being rejected.

Deliberately shallow: one remembered entity per type, and one action in flight.
Deeper anaphora ("the one before that") is not worth the ambiguity it invites
when the input is speech.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Spoken references that mean "the thing we were just talking about".
_CARD_REFS = re.compile(
    r"\b(?:that|the|this|it|same)\s+(?:card|ticket|issue|one)\b|\bit\b|\bthat one\b", re.I
)
# An explicit id anywhere in the sentence settles the question, so a phrase
# like "the issue" is not a dangling reference. Observed: "Okay, in ZI-667
# what is the issue?" was answered with "Which card do you mean?" because
# "the issue" matched while ZI-667 sat in the same sentence.
_EXPLICIT_ID = re.compile(r"\b(?!MCSL\b)[A-Z]{2,6}-\d{1,5}\b|\b(?:card|ticket|issue)\s+\d{2,5}\b", re.I)
_PERSON_REFS = re.compile(r"\b(?:him|her|them|same person)\b", re.I)

# Anything that should close the conversation rather than be answered.
_CLOSERS = re.compile(
    r"^\W*(that'?s (?:all|it)|thanks|thank you|nothing|no thanks|bye|goodbye|"
    r"we'?re done|done|stop|exit|quit)\W*$",
    re.I,
)


def is_closing(text: str) -> bool:
    return bool(_CLOSERS.match((text or "").strip()))


@dataclass
class SlotFill:
    """An action being assembled across turns."""

    action: str
    slots: dict[str, Any] = field(default_factory=dict)
    needs: list[str] = field(default_factory=list)

    QUESTIONS = {
        "person": "Who should I send it to?",
        "text": "What should I say?",
        "card_id": "Which card?",
        "release": "Which release?",
    }

    @property
    def complete(self) -> bool:
        return not self.needs

    def next_question(self) -> str:
        if self.complete:
            return ""
        return self.QUESTIONS.get(self.needs[0], f"What is the {self.needs[0]}?")

    def fill(self, value: str) -> None:
        """Put the answer into the slot currently being asked about."""
        if self.complete or not (value or "").strip():
            return
        self.slots[self.needs.pop(0)] = value.strip()


@dataclass
class Conversation:
    last_card: str = ""
    last_release: str = ""
    last_person: str = ""
    slots: SlotFill | None = None
    # A named multi-step flow in progress, if any. Typed loosely to keep this
    # module free of flow imports.
    flow: Any = None

    def remember(self, intent_name: str, params: dict) -> None:
        if params.get("card_id"):
            self.last_card = params["card_id"]
        if params.get("release"):
            self.last_release = params["release"]
        if params.get("person"):
            self.last_person = params["person"]

    def expects_answer(self) -> bool:
        return self.slots is not None and not self.slots.complete

    def in_flow(self) -> bool:
        return self.flow is not None and not getattr(self.flow, "finished", True)

    def resolve(self, text: str) -> str:
        """Substitute remembered entities for spoken references.

        Returns the text unchanged when there is nothing to substitute, so the
        caller can tell the difference between "no reference" and "reference we
        cannot resolve".
        """
        out = text or ""
        # Do not overwrite an id the user actually said.
        if _EXPLICIT_ID.search(out):
            return out
        if self.last_card and _CARD_REFS.search(out):
            out = _CARD_REFS.sub(self.last_card, out, count=1)
        if self.last_person and _PERSON_REFS.search(out):
            out = _PERSON_REFS.sub(self.last_person, out, count=1)
        return out

    def unresolved_reference(self, text: str) -> bool:
        """True when the user referred back and there is nothing to refer to.

        A sentence naming a card explicitly is never unresolved, however it is
        phrased around that id.
        """
        said = text or ""
        if _EXPLICIT_ID.search(said):
            return False
        return not self.last_card and bool(_CARD_REFS.search(said))

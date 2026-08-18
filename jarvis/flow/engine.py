"""Running a workflow across conversational turns.

The engine holds position, not behaviour: it says what should happen next and
records what came back. Actually calling the worker or the agentic path stays
with the caller, which keeps this testable with no I/O at all.

Every step after the first waits for a yes. A misheard workflow name must not
commit you to six actions unattended.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis.flow.spec import Step, Workflow

# What the engine wants the caller to do next.
RUN = "run"          # execute this step now
ASK = "ask"          # put this question, then feed the answer back
OFFER = "offer"      # put this offer, then feed yes/no back
DONE = "done"        # nothing left


@dataclass
class Instruction:
    action: str
    speech: str = ""
    step: Step | None = None
    slot: str = ""
    request: str = ""


@dataclass
class Flow:
    workflow: Workflow
    slots: dict[str, str] = field(default_factory=dict)
    index: int = 0
    # Set once the current step's offer has been accepted, so the same step is
    # not offered twice.
    accepted: bool = False
    results: list[str] = field(default_factory=list)
    finished: bool = False

    @property
    def step(self) -> Step | None:
        if self.index >= len(self.workflow.steps):
            return None
        return self.workflow.steps[self.index]

    def fill(self, text: str) -> str:
        """Substitute {slots} in a template, leaving unknown ones visible."""
        out = text or ""
        for key, value in self.slots.items():
            out = out.replace("{" + key + "}", str(value))
        return out

    def missing_slot(self) -> str:
        """The first slot this step needs and does not have."""
        step = self.step
        if step is None:
            return ""
        for name in step.needs:
            if not self.slots.get(name):
                return name
        # A placeholder with no value would be spoken literally as "{card}".
        for name in sorted(step.placeholders):
            if not self.slots.get(name):
                return name
        return ""

    def next_instruction(self) -> Instruction:
        """What should happen now."""
        step = self.step
        if step is None:
            self.finished = True
            return Instruction(action=DONE)

        missing = self.missing_slot()
        if missing:
            return Instruction(
                action=ASK,
                speech=self.fill(step.offer) if step.offer and self.accepted else _ask_for(missing),
                slot=missing,
                step=step,
            )

        if step.offer and not self.accepted:
            return Instruction(action=OFFER, speech=self.fill(step.offer), step=step)

        return Instruction(
            action=RUN,
            speech=self.fill(step.say),
            step=step,
            request=self.fill(step.request),
        )

    # ---- transitions -------------------------------------------------

    def accept(self) -> None:
        """The offer was taken."""
        self.accepted = True

    def decline(self) -> None:
        """The offer was refused: the whole flow ends, not just the step.

        Declining "shall I write the AC?" means stop, not skip ahead to the
        test cases that depend on it.
        """
        self.finished = True

    def answer_slot(self, name: str, value: str) -> None:
        if (value or "").strip():
            self.slots[name] = value.strip()

    def record(self, result: str) -> None:
        """The step ran. Move on."""
        self.results.append(result or "")
        self.index += 1
        self.accepted = False
        if self.index >= len(self.workflow.steps):
            self.finished = True

    def seed(self, **context: Any) -> None:
        """Pre-fill slots from what the conversation already knows."""
        for key, value in context.items():
            if value and not self.slots.get(key):
                self.slots[key] = str(value)


_QUESTIONS = {
    "card": "Which card?",
    "store": "Which store are we testing on?",
    "release": "Which release?",
    "person": "Who should I send it to?",
}


def _ask_for(slot: str) -> str:
    return _QUESTIONS.get(slot, f"What is the {slot}?")

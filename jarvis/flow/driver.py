"""Drive a Flow from the daemon's side.

Separated from the engine so the engine stays I/O-free and this holds the only
code that knows how to actually run a step.
"""
from __future__ import annotations

from typing import Callable

from jarvis.flow.engine import ASK, DONE, OFFER, RUN, Flow
from jarvis.router.confirm import interpret
from jarvis.types import Response


def start(flow: Flow, context: dict) -> Response:
    """Seed a new flow and produce its first spoken response."""
    flow.seed(**context)
    return advance(flow)


def reply(flow: Flow, text: str, context: dict) -> Response:
    """Feed one utterance into a running flow."""
    instruction = flow.next_instruction()

    if instruction.action == ASK:
        flow.answer_slot(instruction.slot, text)
        return advance(flow)

    if instruction.action == OFFER:
        verdict = interpret(text)
        if verdict == "yes":
            flow.accept()
            return advance(flow)
        # Anything that is not a yes ends the flow. Skipping ahead would run a
        # step whose input the declined one was supposed to produce.
        flow.decline()
        return Response(speech="Ok boss, stopping there.", ok=True)

    return advance(flow)


def advance(flow: Flow) -> Response:
    """Emit whatever should be said now, without running anything."""
    instruction = flow.next_instruction()

    if instruction.action == DONE:
        return Response(speech="That's the lot, boss.", ok=True)

    if instruction.action in (ASK, OFFER):
        return Response(speech=instruction.speech, awaiting=True, ok=True)

    # RUN: the caller executes and calls record().
    resp = Response(
        speech=instruction.speech or "Working on it.",
        detail=instruction.request,
        tier=3 if instruction.step and instruction.step.kind == "agent" else 1,
        awaiting=True,
    )
    resp.pending = None
    return resp

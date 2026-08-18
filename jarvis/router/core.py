"""handle_transcript — the seam the entire product is tested through."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from jarvis.correct.snap import correct
from jarvis.router.confirm import PendingAction
from jarvis.router.conversation import Conversation, SlotFill, is_closing
from jarvis.router.intents import match
from jarvis.types import Response, RpcError, Vocab


class Worker(Protocol):
    def call(self, method: str, **params) -> dict: ...
    def capabilities(self) -> list[str]: ...


def handle_transcript(
    text: str,
    vocab: Vocab,
    worker: Worker,
    tier3=None,
    store=None,
    domain: str = "mcsl",
    conversation: Conversation | None = None,
) -> Response:
    """Turn a raw transcript into a spoken Response.

    Never raises. Every failure becomes a Response with ok=False.
    """
    try:
        return _handle(text, vocab, worker, tier3, store, domain, conversation)
    except RpcError as exc:
        return Response(speech=str(exc), detail=str(exc), ok=False)
    except Exception as exc:  # never let anything reach the daemon
        return Response(
            speech="Something went wrong handling that.",
            detail=f"{type(exc).__name__}: {exc}",
            ok=False,
        )


def _handle(
    text: str,
    vocab: Vocab,
    worker: Worker,
    tier3=None,
    store=None,
    domain: str = "mcsl",
    conversation: Conversation | None = None,
) -> Response:
    if not text or not text.strip():
        return Response(speech="Didn't catch that.", ok=False)

    if conversation is not None:
        if is_closing(text):
            return Response(speech="Ok boss.", ends=True)

        # An answer to a question Jarvis asked takes precedence over routing:
        # "Ashok" is not a command, it is the reply to "who should I send it to?"
        if conversation.expects_answer():
            return _fill_slot(text, conversation, worker)

        # A bare yes or no is an answer to something, never a command. If it
        # arrives with nothing pending, the question has already been settled
        # -- acknowledging is right; launching an agentic job is not.
        from jarvis.router.confirm import interpret

        if interpret(text) in {"yes", "no"}:
            return Response(speech="Ok.", ok=True)

        if conversation.unresolved_reference(text):
            return Response(speech="Which card do you mean?", ok=False)
        text = conversation.resolve(text)

    result = correct(text, vocab)

    if result.ambiguous:
        heard, guess = result.ambiguous[0]
        return Response(
            speech=f"Did you mean {guess}?",
            detail=f"heard '{heard}'",
            needs_confirm=True,
        )

    matched = match(result.text)
    if matched is None:
        return _tier3(result.text, tier3)

    intent, params = matched

    # "who is the dev for that" names no card. Fill it from what was just
    # discussed, and ask rather than guess when there is nothing.
    if "card_id" in params and not params["card_id"]:
        if conversation is None or not conversation.last_card:
            return Response(speech="Which card do you mean?", ok=False)
        params["card_id"] = conversation.last_card

    if conversation is not None:
        conversation.remember(intent.name, params)

    # An incomplete request starts a dialogue rather than being rejected.
    if intent.method == "__slots__":
        if conversation is None:
            return Response(speech="Who should I send it to, and what should I say?", ok=False)
        conversation.slots = SlotFill(action="send_dm", needs=["person", "text"])
        return Response(speech=conversation.slots.next_question(), awaiting=True)

    try:
        available = worker.capabilities()
    except RpcError as exc:
        return Response(
            speech="I can't reach that project right now.",
            detail=f"worker capabilities failed: {exc}",
            ok=False,
        )

    # Memory intents are answered here, not by the worker: the store belongs
    # to the daemon and is domain-agnostic.
    if intent.method == "__local__":
        return _memory(intent.name, params, store, domain)

    if intent.method not in available:
        return _tier3(result.text, tier3)

    # Write actions never execute here. They are built, read back, and only
    # run after an explicit spoken yes.
    if intent.name == "send_dm":
        return _build_dm(params, worker)
    if intent.name == "post_channel":
        return _build_post(params)

    try:
        payload = worker.call(intent.method, **params)
    except RpcError as exc:
        return Response(speech=str(exc), detail=str(exc), ok=False)

    if not isinstance(payload, dict):
        return Response(
            speech="I got a strange answer back — try again?",
            detail="worker returned a non-dict payload",
            ok=False,
        )

    # "What are my tasks" means both things you told Jarvis and what Trello
    # says. Asking twice would be a worse product than merging once.
    if intent.name == "my_tasks" and store is not None:
        payload = _merge_local_tasks(payload, store, domain)

    return Response(
        speech=payload.get("speech", ""),
        detail=payload.get("detail", ""),
        tier=1,
    )


def _merge_local_tasks(payload: dict, store, domain: str) -> dict:
    """Put your own reminders in front of the Trello list.

    Overdue ones lead: they are the reason you asked.
    """
    local = store.list_tasks(domain=domain)
    if not local:
        return payload

    now = datetime.now()
    overdue = [t for t in local if t.due_at and t.due_at <= now]
    upcoming = [t for t in local if not (t.due_at and t.due_at <= now)]

    parts = []
    if overdue:
        parts.append(f"{len(overdue)} overdue: " + "; ".join(t.text[:60] for t in overdue[:3]))
    if upcoming:
        parts.append(f"{len(upcoming)} of your own: " + "; ".join(t.text[:60] for t in upcoming[:3]))

    mine = ". ".join(parts)
    return {
        "speech": f"{mine}. And from Trello: {payload.get('speech', '')}",
        "detail": "\n".join(
            [f"- {t.text}" + (f" (due {t.due_at:%d %b %H:%M})" if t.due_at else "") for t in local]
            + ["", payload.get("detail", "")]
        ),
    }


def _fill_slot(text: str, conversation: Conversation, worker: Worker) -> Response:
    """Record one answer and either ask the next question or build the action."""
    slots = conversation.slots
    assert slots is not None
    slots.fill(text)

    if not slots.complete:
        return Response(speech=slots.next_question(), awaiting=True)

    conversation.slots = None
    if slots.action == "send_dm":
        return _build_dm({"person": slots.slots["person"], "text": slots.slots["text"]}, worker)
    return Response(speech="I lost track of that, sorry.", ok=False)


def _memory(name: str, params: dict, store, domain: str) -> Response:
    """Tasks and notes. Writes here need no confirmation — they are local,
    private, and trivially undone, unlike a Slack message."""
    if name == "open_app":
        from jarvis.apps import open_app

        ok, said = open_app(params["app"])
        return Response(speech=said, ok=ok)

    if store is None:
        return Response(speech="Memory isn't set up yet.", ok=False)

    from jarvis.memory.when import parse_when, strip_when

    if name == "remind_me":
        raw = params["text"]
        due = parse_when(raw)
        body = strip_when(raw) or raw
        store.add_task(body, domain=domain, due_at=due)
        when = f" at {due.strftime('%H:%M')}" if due else ""
        if due and due.date() != datetime.now().date():
            when = f" tomorrow at {due.strftime('%H:%M')}"
        return Response(speech=f"Noted — {body}{when}.", detail=raw)

    if name == "open_app":
        from jarvis.apps import open_app

        ok, said = open_app(params["app"])
        return Response(speech=said, ok=ok)

    if name == "add_note":
        store.add_note(params["text"], domain=domain)
        return Response(speech="Noted.", detail=params["text"])

    if name == "list_notes":
        notes = store.list_notes(domain=domain, limit=5)
        if not notes:
            return Response(speech="No notes yet.")
        label = "note" if len(notes) == 1 else "notes"
        return Response(
            speech=f"{len(notes)} {label}. " + "; ".join(n.text[:70] for n in notes),
            detail="\n".join(f"- {n.text}" for n in notes),
        )

    return Response(speech="I don't know how to do that yet.", ok=False)


def _build_post(params: dict) -> Response:
    """A channel post is seen by everyone in it, so it is read back like a DM.

    No recipient to resolve -- Slack rejects an unknown channel itself, and
    guessing at a channel name would be worse than reporting the failure.
    """
    channel, body = params["channel"], params["text"]
    speech = f"Posting in {channel}: {body}. Ok?"
    return Response(
        speech=speech,
        detail=f"#{channel}: {body}",
        needs_confirm=True,
        pending=PendingAction(
            method="post_channel",
            params={"channel": channel, "text": body},
            speech=speech,
            detail=body,
        ),
    )


def _build_dm(params: dict, worker: Worker) -> Response:
    """Resolve the recipient and hand back a pending action. Sends nothing."""
    person = worker.call("resolve_person", name=params["person"])

    if person.get("ambiguous"):
        names = ", ".join(p["name"] for p in person["ambiguous"])
        return Response(
            speech=f"More than one match — {names}. Which one?",
            detail=names,
            ok=False,
        )
    if not person.get("id"):
        return Response(speech=f"I don't know who {params['person']} is.", ok=False)

    body = params["text"]
    speech = f"Sending to {person['name']}: {body}. Ok?"
    return Response(
        speech=speech,
        detail=f"{person['name']} ({person['id']}): {body}",
        needs_confirm=True,
        pending=PendingAction(
            method="send_dm",
            params={"user_id": person["id"], "text": body},
            speech=speech,
            detail=body,
        ),
    )


def _tier3(text: str, tier3) -> Response:
    """Hand off to the agentic path.

    The speech deliberately names no mechanism: "Claude Code" is plumbing, not
    something an assistant says out loud.
    """
    if tier3 is None:
        return Response(speech="Ok boss, fetching that now.", detail=text, tier=3)
    return tier3(text)

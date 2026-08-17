# Jarvis Slice 2 Implementation Plan — Write Actions

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Say "DM Ashok saying the toggle is still off", hear Jarvis read it back with the resolved name, say "ok", and have it send. And have "create a GLS carrier store" actually run in Claude Code instead of only announcing it.

**Architecture:** A confirm state machine sits between the router and any action with a side effect. Read intents keep their existing straight-through path. Tier 3 gains real `claude -p` execution, detached, so a long run never blocks the microphone.

**Tech Stack:** unchanged — Python 3.12, existing worker RPC, macOS `say`.

## Global Constraints

- Anything that is not a clear yes is a **no**. Silence, noise, and unparsed speech all cancel. Ambiguity is never consent.
- Confirmation timeout: **30 seconds**.
- No voice-editing of a pending action. Cancel and re-dictate.
- Tier 3 output is **never read aloud verbatim** — a store-creation run is minutes of speech. Summary only.
- A running Tier 3 job must **never block the wake loop**.
- `jarvis/` still must not import `pipeline`, `config`, or `rag`. Only `worker/` may.
- No Domain Expert repo is modified.
- Slack sends are real and irreversible. Every test must use a fake client; no test may call the live API.

---

## File Structure

| Path | Responsibility |
|---|---|
| `jarvis/router/confirm.py` | Pending action + yes/no/timeout state machine |
| `jarvis/router/intents.py` | add `send_dm` intent (modify) |
| `jarvis/router/core.py` | return a pending action instead of executing writes (modify) |
| `jarvis/tier3.py` | detached `claude -p` runner with a summary callback |
| `worker/handlers.py` | add `resolve_person` and `send_dm` (modify) |
| `jarvis/daemon.py` | drive the confirm turn (modify) |

---

### Task 1: Confirm state machine

**Files:** Create `jarvis/router/confirm.py`, `tests/test_confirm.py`

**Interfaces:**
- Produces: `PendingAction(method, params, speech, detail)`, `Confirmation`, `interpret(text) -> Literal["yes","no","unclear"]`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from jarvis.router.confirm import Confirmation, PendingAction, interpret


@pytest.mark.parametrize("text", ["ok", "okay", "yes", "yeah", "yep", "send", "send it", "go", "do it", "confirm", "OK."])
def test_affirmatives(text):
    assert interpret(text) == "yes"


@pytest.mark.parametrize("text", ["no", "nope", "cancel", "stop", "don't", "do not", "forget it", "nevermind"])
def test_negatives(text):
    assert interpret(text) == "no"


@pytest.mark.parametrize("text", ["", "   ", "uh", "hmm", "what", "the toggle is still off", "maybe", "ok so actually change it"])
def test_anything_else_is_unclear(text):
    assert interpret(text) == "unclear"


def test_unclear_cancels_because_ambiguity_is_never_consent():
    c = Confirmation(PendingAction("send_dm", {"user_id": "U1", "text": "hi"}, "Send to Ashok?", ""))
    assert c.resolve("hmm") is False
    assert c.settled is True


def test_yes_confirms():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""))
    assert c.resolve("ok") is True
    assert c.settled is True


def test_no_cancels():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""))
    assert c.resolve("cancel") is False


def test_expired_confirmation_cannot_be_resolved():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""), timeout_seconds=0)
    assert c.expired(now=1.0) is True
    assert c.resolve("ok") is False


def test_a_settled_confirmation_cannot_be_resolved_twice():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""))
    assert c.resolve("ok") is True
    assert c.resolve("ok") is False
```

- [ ] **Step 2: Run it, confirm it fails**

`.venv/bin/pytest tests/test_confirm.py -v` → ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
"""Confirmation gate for anything with a side effect.

The rule, from the spec: anything that is not a clear yes is a no. A
re-dictated command costs five seconds; a Slack message sent to the wrong
colleague cannot be taken back.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

Verdict = Literal["yes", "no", "unclear"]

DEFAULT_TIMEOUT = 30.0

_YES = re.compile(r"^\W*(ok(ay)?|yes|yeah|yep|yup|sure|send( it)?|go( ahead)?|do it|confirm)\W*$", re.I)
_NO = re.compile(r"^\W*(no|nope|nah|cancel|stop|don'?t|do not|forget it|never ?mind)\W*$", re.I)


def interpret(text: str) -> Verdict:
    """Classify a confirmation reply. Only a bare affirmative counts as yes —
    "ok so actually make it the GLS store" is a correction, not consent."""
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
        """True only on an explicit yes within the window. Always settles."""
        if self.settled or self.expired():
            self.settled = True
            return False
        self.settled = True
        return interpret(text) == "yes"
```

- [ ] **Step 4: Run tests** → all pass
- [ ] **Step 5: Commit** `feat: add confirmation gate for side-effecting actions`

---

### Task 2: Person resolution and send_dm on the worker

**Files:** Modify `worker/handlers.py`; create `tests/test_handlers_dm.py`

**Interfaces:**
- Produces worker methods `resolve_person(name) -> {"id","name","ambiguous":[...]}` and `send_dm(user_id, text) -> {"speech","detail"}`

- [ ] **Step 1: Write the failing test** (fake Slack client injected — never the live API)

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))


class _FakeSlack:
    def __init__(self, users=None, fail=None):
        self._users = users if users is not None else []
        self._fail = fail
        self.sent = []

    def search_users(self, query):
        return [u for u in self._users if query.lower() in (u["real_name"] or "").lower()]

    def send_dm(self, user_id, text):
        if self._fail:
            raise RuntimeError(self._fail)
        self.sent.append((user_id, text))
        return "1699999999.0001"


@pytest.fixture
def handlers(monkeypatch):
    import handlers as h
    return h


def test_resolve_person_finds_a_single_match(handlers, monkeypatch):
    fake = _FakeSlack([{"id": "U01", "real_name": "Ashok Kumar", "display_name": "ashok"}])
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    assert handlers.resolve_person("ashok")["id"] == "U01"


def test_resolve_person_reports_ambiguity_rather_than_guessing(handlers, monkeypatch):
    fake = _FakeSlack([
        {"id": "U01", "real_name": "Ashok Kumar", "display_name": "ashok"},
        {"id": "U02", "real_name": "Ashok Verma", "display_name": "ashokv"},
    ])
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    out = handlers.resolve_person("ashok")
    assert out["id"] == ""
    assert len(out["ambiguous"]) == 2


def test_resolve_person_unknown_returns_empty(handlers, monkeypatch):
    monkeypatch.setattr(handlers, "_slack", lambda: _FakeSlack([]))
    assert handlers.resolve_person("nobody")["id"] == ""


def test_send_dm_delivers_and_reports(handlers, monkeypatch):
    fake = _FakeSlack()
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    out = handlers.send_dm("U01", "toggle is still off")
    assert fake.sent == [("U01", "toggle is still off")]
    assert "sent" in out["speech"].lower()


def test_send_dm_surfaces_the_error(handlers, monkeypatch):
    monkeypatch.setattr(handlers, "_slack", lambda: _FakeSlack(fail="not_in_channel"))
    with pytest.raises(RuntimeError):
        handlers.send_dm("U01", "hi")
```

- [ ] **Step 2: Run it, confirm it fails**
- [ ] **Step 3: Implement in `worker/handlers.py`**

```python
def _slack():
    from pipeline.slack_client import SlackClient

    return SlackClient()


def resolve_person(name: str) -> dict[str, Any]:
    """Map a spoken name to a Slack user id.

    Never guesses between two people — a DM to the wrong colleague is not
    recoverable. Ambiguity is returned for the caller to ask about.
    """
    matches = _slack().search_users(name) or []
    if len(matches) == 1:
        m = matches[0]
        return {"id": m.get("id", ""), "name": m.get("real_name") or m.get("display_name", ""), "ambiguous": []}
    if not matches:
        return {"id": "", "name": "", "ambiguous": []}
    return {
        "id": "",
        "name": "",
        "ambiguous": [
            {"id": m.get("id", ""), "name": m.get("real_name") or m.get("display_name", "")}
            for m in matches[:5]
        ],
    }


def send_dm(user_id: str, text: str) -> dict[str, Any]:
    ts = _slack().send_dm(user_id, text)
    return {"speech": "Sent.", "detail": f"to {user_id} at {ts}: {text}"}
```

Register both in `HANDLERS`.

- [ ] **Step 4: Run tests** → all pass
- [ ] **Step 5: Commit** `feat: add slack person resolution and dm sending to the worker`

---

### Task 3: send_dm intent and the router's pending path

**Files:** Modify `jarvis/router/intents.py`, `jarvis/router/core.py`; modify `tests/test_router.py`

**Interfaces:**
- `handle_transcript` may now return a `Response` carrying `needs_confirm=True` and a `pending` attribute.

- [ ] **Step 1: Write the failing test**

```python
def test_send_dm_builds_a_pending_action_and_does_not_send(vocab):
    worker = FakeWorker(methods=["send_dm", "resolve_person"])
    worker.results = {"resolve_person": {"id": "U01", "name": "Ashok Kumar", "ambiguous": []}}
    resp = handle_transcript("DM Ashok saying the toggle is still off", vocab, worker)

    assert resp.needs_confirm is True
    assert "Ashok Kumar" in resp.speech
    assert "toggle is still off" in resp.speech
    assert ("send_dm", {"user_id": "U01", "text": "the toggle is still off"}) not in worker.calls


def test_ambiguous_person_asks_instead_of_sending(vocab):
    worker = FakeWorker(methods=["send_dm", "resolve_person"])
    worker.results = {"resolve_person": {"id": "", "name": "", "ambiguous": [
        {"id": "U01", "name": "Ashok Kumar"}, {"id": "U02", "name": "Ashok Verma"}]}}
    resp = handle_transcript("DM Ashok saying hello", vocab, worker)

    assert resp.needs_confirm is False
    assert resp.ok is False
    assert "Ashok Kumar" in resp.speech and "Ashok Verma" in resp.speech


def test_unknown_person_does_not_produce_a_pending_action(vocab):
    worker = FakeWorker(methods=["send_dm", "resolve_person"])
    worker.results = {"resolve_person": {"id": "", "name": "", "ambiguous": []}}
    resp = handle_transcript("DM Nobody saying hello", vocab, worker)
    assert resp.ok is False
    assert resp.needs_confirm is False
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement** — add to `intents.py`:

```python
def _dm_params(m: re.Match[str]) -> dict:
    return {"person": m.group("person").strip(), "text": m.group("body").strip()}


Intent(
    name="send_dm",
    method="send_dm",
    # "DM Ashok saying the toggle is still off" / "message Ashok that ..."
    pattern=re.compile(
        r"\b(?:dm|d\.m\.|message|tell|ping)\s+(?P<person>[A-Za-z][A-Za-z .']{1,30}?)\s+"
        r"(?:saying|to say|that|:)\s+(?P<body>.+)$",
        re.I,
    ),
    extract=_dm_params,
),
```

and in `core.py`, before the generic worker call:

```python
if intent.name == "send_dm":
    return _build_dm(params, worker)
```

```python
def _build_dm(params: dict, worker: Worker) -> Response:
    """Resolve the person, then hand back a pending action. Never sends here."""
    person = worker.call("resolve_person", name=params["person"])
    if person.get("ambiguous"):
        names = ", ".join(p["name"] for p in person["ambiguous"])
        return Response(speech=f"Which one — {names}?", detail=names, ok=False)
    if not person.get("id"):
        return Response(speech=f"I don't know who {params['person']} is.", ok=False)

    body = params["text"]
    resp = Response(
        speech=f"Sending to {person['name']}: {body}. Ok?",
        detail=f"{person['name']} ({person['id']}): {body}",
        needs_confirm=True,
    )
    resp.pending = PendingAction("send_dm", {"user_id": person["id"], "text": body},
                                 resp.speech, resp.detail)
    return resp
```

Add `pending: Any = None` to `Response` in `jarvis/types.py`.

- [ ] **Step 4: Run tests** → pass
- [ ] **Step 5: Commit** `feat: route DM requests into a pending action instead of sending`

---

### Task 4: Real tier-3 execution

**Files:** Create `jarvis/tier3.py`, `tests/test_tier3.py`

**Interfaces:** `Tier3Runner(domain_path, python=None)` with `.start(text, on_done) -> None`, `.busy -> bool`

- [ ] **Step 1: Write the failing test**

```python
import time

from jarvis.tier3 import Tier3Runner


def test_start_runs_detached_and_calls_back(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/echo", "hello from claude"])
    done = []
    r.start("create a store", done.append)
    for _ in range(100):
        if done:
            break
        time.sleep(0.05)
    assert done and "hello" in done[0]


def test_start_does_not_block(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/sleep", "2"])
    t0 = time.monotonic()
    r.start("slow thing", lambda out: None)
    assert time.monotonic() - t0 < 0.5, "start() must return immediately"
    assert r.busy is True


def test_second_job_is_refused_while_one_is_running(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/sleep", "2"])
    r.start("first", lambda out: None)
    assert r.start("second", lambda out: None) is False


def test_failure_is_reported_not_swallowed(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/sh", "-c", "echo boom >&2; exit 3"])
    done = []
    r.start("x", done.append)
    for _ in range(100):
        if done:
            break
        time.sleep(0.05)
    assert done and "boom" in done[0]
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**

```python
"""Detached `claude -p` execution.

Two rules from the spec: a long run must never block the microphone, and its
output is never read aloud verbatim — a store-creation run is minutes of
speech. The caller gets the raw text and decides what to say.
"""
from __future__ import annotations

import subprocess
import threading
from typing import Callable

TIMEOUT_SECONDS = 600  # 10 minutes, per the spec


class Tier3Runner:
    def __init__(self, domain_path: str, command: list[str] | None = None) -> None:
        self.domain_path = domain_path
        self.command = command
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, text: str, on_done: Callable[[str], None]) -> bool:
        """Returns False if a job is already running."""
        if self.busy:
            return False
        argv = self.command or ["claude", "-p", text]

        def run() -> None:
            try:
                proc = subprocess.run(
                    argv, cwd=self.domain_path, capture_output=True,
                    text=True, timeout=TIMEOUT_SECONDS,
                )
                out = (proc.stdout or "") + (proc.stderr or "")
            except subprocess.TimeoutExpired:
                out = f"timed out after {TIMEOUT_SECONDS}s"
            except Exception as exc:
                out = f"{type(exc).__name__}: {exc}"
            on_done(out.strip())

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True
```

- [ ] **Step 4: Run tests** → pass
- [ ] **Step 5: Commit** `feat: run tier 3 detached so long jobs never block the mic`

---

### Task 5: Wire the confirm turn into the daemon

**Files:** Modify `jarvis/daemon.py`, `tests/test_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pending_action_is_executed_after_a_yes():
    j = _jarvis_with_fakes("DM Ashok saying hello")
    pending = PendingAction("send_dm", {"user_id": "U01", "text": "hello"}, "Send to Ashok? ", "")
    resp = Response(speech="Sending to Ashok Kumar: hello. Ok?", needs_confirm=True)
    resp.pending = pending

    with patch("jarvis.daemon.handle_transcript", return_value=resp), patch("jarvis.daemon.speak"):
        j.transcriber.transcribe.side_effect = ["DM Ashok saying hello", "ok"]
        j.handle_utterance(np.zeros(16000, dtype=np.float32))

    assert ("send_dm", {"user_id": "U01", "text": "hello"}) in j.worker.calls


def test_pending_action_is_dropped_on_anything_but_yes():
    j = _jarvis_with_fakes("DM Ashok saying hello")
    resp = Response(speech="Send? ", needs_confirm=True)
    resp.pending = PendingAction("send_dm", {"user_id": "U01", "text": "hello"}, "", "")

    with patch("jarvis.daemon.handle_transcript", return_value=resp), patch("jarvis.daemon.speak"):
        j.transcriber.transcribe.side_effect = ["DM Ashok saying hello", "hmm"]
        j.handle_utterance(np.zeros(16000, dtype=np.float32))

    assert not any(c[0] == "send_dm" for c in j.worker.calls)
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement** — in `handle_utterance`, after building the response:

```python
if response.needs_confirm and getattr(response, "pending", None):
    return self._run_confirmation(response, capture)
```

```python
def _run_confirmation(self, response: Response, capture) -> Response:
    """Speak the read-back, listen once, execute only on a clear yes."""
    speak(response)
    if capture is not None:
        capture.drain()
        reply = self.transcriber.transcribe(capture.record(CONFIRM_SECONDS), self.vocab)
    else:
        reply = self.transcriber.transcribe(np.zeros(1, dtype=np.float32), self.vocab)
    print(f"  confirm reply: {reply!r}", flush=True)

    confirmation = Confirmation(response.pending)
    if not confirmation.resolve(reply):
        out = Response(speech="Cancelled.", ok=False)
        speak(out)
        return out

    try:
        payload = self.worker.call(response.pending.method, **response.pending.params)
    except RpcError as exc:
        out = Response(speech=str(exc), ok=False)
        speak(out)
        return out

    out = Response(speech=payload.get("speech", "Done."), detail=payload.get("detail", ""))
    speak(out)
    return out
```

with `CONFIRM_SECONDS = 4.0`.

- [ ] **Step 4: Run tests** → pass
- [ ] **Step 5: Commit** `feat: drive the spoken confirmation turn in the daemon`

---

## Verification

- [ ] `.venv/bin/pytest -m "not audio"` all pass
- [ ] No test touches the live Slack API — every DM test uses `_FakeSlack`
- [ ] `jarvis.cli "DM <a real colleague> saying test"` reads back and waits, and does NOT send without a yes
- [ ] A tier-3 command returns immediately and the mic stays live
- [ ] `git -C <each Domain Expert> status --porcelain` shows nothing new

## Out of scope

Voice-editing a pending action, queued tier-3 jobs, barge-in during playback.

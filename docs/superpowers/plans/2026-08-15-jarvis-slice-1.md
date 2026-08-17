# Jarvis Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Say "hey jarvis, status of MCSL three eighty four" (or double-clap and say it) and hear the card's list, assignee, and last comment spoken back.

**Architecture:** A standalone `jarvis` daemon owns audio, transcription, correction, routing, and speech. It never imports a Domain Expert repo. Each Domain Expert gets a warm worker process launched in *its own* venv, speaking newline-delimited JSON over a unix socket. The whole product is testable without a microphone through one seam: `handle_transcript(text, domain) -> Response`.

**Tech Stack:** Python 3.12, `faster-whisper` (STT), `openwakeword` (wake word), `sounddevice` + `numpy` (audio), macOS `say` (TTS), `osascript` (notifications), stdlib `socket`/`json` (RPC), `pytest`.

## Global Constraints

- Jarvis venv MUST use `/opt/homebrew/bin/python3.12`. System default is 3.14.3 and lacks wheels for the ML dependencies.
- `jarvis/` code MUST NOT import `pipeline`, `config`, or `rag` from any Domain Expert repo. Only `worker/` may, and only inside that repo's venv.
- No Domain Expert repo is modified by this plan. The dependency is strictly one-way.
- Slice 1 is **read-only**. No Slack sends, no Trello writes, no SQLite, no schedulers.
- Match confidence thresholds: **≥0.85 snaps silently, 0.60–0.85 asks, <0.60 is no match.**
- Worker idle timeout: **10 minutes** (implemented in slice 1 as a config constant; the reaper lands with the daemon in Task 10).
- Target repo for slice 1: `/Users/madan/Documents/MCSLDomainExpert`, venv at `.venv/bin/python` (3.12.13).
- All commands run from `/Users/madan/Documents/jarvis` unless stated otherwise.

---

## File Structure

| Path | Responsibility |
|---|---|
| `jarvis/types.py` | `Response`, `Vocab`, `RpcError` — shared dataclasses, no logic |
| `jarvis/correct/numbers.py` | Spoken numbers → digits. Pure function |
| `jarvis/correct/snap.py` | Snap tokens to real entity IDs. Pure function |
| `jarvis/workers/client.py` | Unix-socket JSON-RPC client + worker spawn/keepalive |
| `jarvis/router/intents.py` | Intent registry: name, pattern, handler |
| `jarvis/router/core.py` | `handle_transcript` — the testable seam |
| `jarvis/voice/speak.py` | TTS + macOS notification |
| `jarvis/ears/stt.py` | faster-whisper wrapper |
| `jarvis/ears/wake.py` | Clap detector + openWakeWord, one audio stream |
| `jarvis/daemon.py` | Wires everything; entry point |
| `jarvis/cli.py` | Type a command instead of speaking it. Debug + demo tool |
| `worker/main.py` | Runs inside a Domain Expert venv. Socket server |
| `worker/handlers.py` | Tier-1 handlers that touch `pipeline.*` |
| `domains.yaml` | Registry: name, path, python, socket, aliases |

Text-in/text-out core (Tasks 2–6) is built first, then output (7), then input (8–9). After Task 7 there is a working demo via `jarvis/cli.py` — no microphone required.

---

### Task 1: Repo scaffold and toolchain

**Files:**
- Create: `.gitignore`, `requirements.txt`, `pytest.ini`, `jarvis/__init__.py`, `tests/test_scaffold.py`

**Interfaces:**
- Consumes: nothing
- Produces: a working venv at `.venv/` and a green `pytest` run

- [ ] **Step 1: Install the system prerequisite**

```bash
brew install portaudio
```

Expected: portaudio installs, or "already installed". `sounddevice` cannot build without it.

- [ ] **Step 2: Create the venv on Python 3.12**

```bash
cd /Users/madan/Documents/jarvis
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python --version
```

Expected: `Python 3.12.13`. If it prints 3.14.x, the wrong interpreter was used — delete `.venv` and retry with the absolute path.

- [ ] **Step 3: Write `requirements.txt`**

```
faster-whisper>=1.0.3
openwakeword>=0.6.0
sounddevice>=0.4.7
numpy>=1.26,<2.3
PyYAML>=6.0.1
pytest>=8.3.3
```

- [ ] **Step 4: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
models/
*.sock
```

- [ ] **Step 5: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
pythonpath = .
markers =
    audio: requires a microphone or model download (deselect with '-m "not audio"')
```

`pythonpath = .` is required: without it, `.venv/bin/pytest` does not put the project
root on `sys.path` and every `import jarvis` in the test suite fails.

- [ ] **Step 6: Install dependencies**

```bash
.venv/bin/pip install -r requirements.txt
```

Expected: all install. `faster-whisper` pulls `ctranslate2`; on arm64 this is a prebuilt wheel and should not compile.

- [ ] **Step 7: Write the scaffold test**

`tests/test_scaffold.py`:

```python
import sys


def test_python_is_312():
    assert sys.version_info[:2] == (3, 12)


def test_jarvis_package_imports():
    import jarvis

    assert jarvis is not None


def test_audio_stack_imports():
    import sounddevice
    import numpy

    assert sounddevice is not None
    assert numpy is not None
```

- [ ] **Step 8: Create the package marker**

`jarvis/__init__.py`:

```python
"""Jarvis — voice front-end to the Domain Expert platforms."""

__version__ = "0.1.0"
```

- [ ] **Step 9: Run the tests**

```bash
.venv/bin/pytest tests/test_scaffold.py -v
```

Expected: 3 passed.

- [ ] **Step 10: Commit**

```bash
git add .gitignore requirements.txt pytest.ini jarvis/__init__.py tests/test_scaffold.py
git commit -m "chore: scaffold jarvis repo on python 3.12"
```

---

### Task 2: Shared types

**Files:**
- Create: `jarvis/types.py`, `tests/test_types.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Response(speech, detail, tier, ok, needs_confirm)`, `Vocab(cards, people, carriers, zi_ids)`, `RpcError`

- [ ] **Step 1: Write the failing test**

`tests/test_types.py`:

```python
from jarvis.types import Response, Vocab, RpcError


def test_response_defaults():
    r = Response(speech="hello")
    assert r.speech == "hello"
    assert r.detail == ""
    assert r.tier == 1
    assert r.ok is True


def test_response_carries_detail_separately_from_speech():
    r = Response(speech="short", detail="a much longer body for the notification")
    assert r.speech != r.detail


def test_vocab_all_returns_every_entity():
    v = Vocab(cards=["MCSL-384"], people=["Ashok Kumar"], carriers=["gls"], zi_ids=["ZI-691"])
    assert set(v.all()) == {"MCSL-384", "Ashok Kumar", "gls", "ZI-691"}


def test_vocab_empty_by_default():
    assert Vocab().all() == []


def test_rpc_error_is_an_exception():
    assert issubclass(RpcError, Exception)
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_types.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.types'`

- [ ] **Step 3: Implement**

`jarvis/types.py`:

```python
"""Shared dataclasses. No logic beyond trivial accessors."""
from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass
class Vocab:
    """Live entity snapshot used by the correction layer."""

    cards: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    carriers: list[str] = field(default_factory=list)
    zi_ids: list[str] = field(default_factory=list)

    def all(self) -> list[str]:
        return [*self.cards, *self.people, *self.carriers, *self.zi_ids]
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/pytest tests/test_types.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add jarvis/types.py tests/test_types.py
git commit -m "feat: add shared Response and Vocab types"
```

---

### Task 3: Spoken-number normalisation

**Files:**
- Create: `jarvis/correct/__init__.py`, `jarvis/correct/numbers.py`, `tests/test_numbers.py`

**Interfaces:**
- Consumes: nothing
- Produces: `normalize_numbers(text: str) -> str`

Whisper transcribes spoken IDs inconsistently: "three eighty four", "six five three", "three hundred eighty four". All must become digits before entity snapping runs.

**The rule:** within a run of consecutive number-words, if the run contains "hundred" or "thousand", evaluate it arithmetically; otherwise concatenate each fragment's digits.

| Input | Contains hundred? | Output |
|---|---|---|
| three eighty four | no | `3` + `84` = `384` |
| six five three | no | `6` + `5` + `3` = `653` |
| three hundred eighty four | yes | 3×100 + 84 = `384` |

- [ ] **Step 1: Write the failing test**

`tests/test_numbers.py`:

```python
import pytest

from jarvis.correct.numbers import normalize_numbers


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("three eighty four", "384"),
        ("six five three", "653"),
        ("three hundred eighty four", "384"),
        ("six hundred fifty three", "653"),
        ("nineteen", "19"),
        ("eighty", "80"),
        ("zero", "0"),
        ("one two three four", "1234"),
    ],
)
def test_bare_numbers(spoken, expected):
    assert normalize_numbers(spoken) == expected


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("status of MCSL three eighty four", "status of MCSL 384"),
        ("what about ZI six five three", "what about ZI 653"),
        ("status of MCSL-384", "status of MCSL-384"),
        ("no numbers here at all", "no numbers here at all"),
    ],
)
def test_numbers_in_sentences(spoken, expected):
    assert normalize_numbers(spoken) == expected


def test_two_separate_runs_stay_separate():
    assert normalize_numbers("compare three eighty four and six five three") == (
        "compare 384 and 653"
    )


def test_existing_digits_pass_through_untouched():
    assert normalize_numbers("MCSL 384 and ZI 653") == "MCSL 384 and ZI 653"
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_numbers.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.correct'`

- [ ] **Step 3: Create the package marker**

`jarvis/correct/__init__.py`:

```python
"""Transcript correction: numbers, then entity snapping."""
```

- [ ] **Step 4: Implement**

`jarvis/correct/numbers.py`:

```python
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
```

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/pytest tests/test_numbers.py -v
```

Expected: 14 passed.

- [ ] **Step 6: Commit**

```bash
git add jarvis/correct/__init__.py jarvis/correct/numbers.py tests/test_numbers.py
git commit -m "feat: normalize spoken numbers to digits"
```

---

### Task 4: Entity snapping

**Files:**
- Create: `jarvis/correct/snap.py`, `tests/test_snap.py`

**Interfaces:**
- Consumes: `Vocab` from `jarvis.types`, `normalize_numbers` from `jarvis.correct.numbers`
- Produces: `correct(text: str, vocab: Vocab) -> CorrectionResult`, `CorrectionResult(text, ambiguous)`

Whisper hears "muscle three eighty four" and "M C S L 384". Both must become `MCSL-384` when that card exists in the vocabulary.

- [ ] **Step 1: Write the failing test**

`tests/test_snap.py`:

```python
import pytest

from jarvis.correct.snap import correct
from jarvis.types import Vocab


@pytest.fixture
def vocab():
    return Vocab(
        cards=["MCSL-384", "MCSL-390"],
        people=["Ashok Kumar", "Madan Kumar"],
        carriers=["gls", "ups"],
        zi_ids=["ZI-653", "ZI-691"],
    )


@pytest.mark.parametrize(
    "heard",
    [
        "status of MCSL three eighty four",
        "status of muscle three eighty four",
        "status of M C S L 384",
        "status of mcsl384",
    ],
)
def test_card_id_snaps_to_real_card(heard, vocab):
    assert "MCSL-384" in correct(heard, vocab).text


def test_zi_id_snaps(vocab):
    assert "ZI-653" in correct("what about Z I six five three", vocab).text


def test_unknown_card_number_is_not_invented(vocab):
    # MCSL-999 is not in the vocabulary; do not snap it to a real card.
    result = correct("status of MCSL nine nine nine", vocab)
    assert "MCSL-384" not in result.text
    assert "MCSL-390" not in result.text


def test_surrounding_words_are_not_swallowed(vocab):
    # A greedy letter-run would eat "of" and yield "status MCSL-384".
    assert correct("status of MCSL three eighty four", vocab).text == "status of MCSL-384"


def test_near_miss_number_is_left_alone(vocab):
    # Digits 38 match no known card. Leave the text alone; never round to 384.
    result = correct("status of MCSL thirty eight", vocab)
    assert "MCSL-384" not in result.text
    assert "MCSL-390" not in result.text


def test_fuzzy_prefix_with_exact_digits_is_reported_as_ambiguous(vocab):
    # Digits match a real card, but the prefix is neither aliased nor close
    # enough to snap — this is exactly the 0.60-0.85 band.
    result = correct("status of MPQL 384", vocab)
    assert result.ambiguous == [("MPQL 384", "MCSL-384")]
    assert "MCSL-384" not in result.text


def test_text_without_entities_is_unchanged(vocab):
    assert correct("what are my tasks", vocab).text == "what are my tasks"


def test_empty_vocab_does_not_crash():
    assert correct("status of MCSL 384", Vocab()).text == "status of MCSL 384"
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_snap.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.correct.snap'`

- [ ] **Step 3: Implement**

`jarvis/correct/snap.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/pytest tests/test_snap.py -v
```

Expected: 11 passed. (Both `normalize_numbers` and `correct` were verified against
every case in this task's test table before this plan was written.)

- [ ] **Step 5: Commit**

```bash
git add jarvis/correct/snap.py tests/test_snap.py
git commit -m "feat: snap transcript tokens to real entity ids"
```

---

### Task 5: Worker RPC protocol and client

**Files:**
- Create: `jarvis/workers/__init__.py`, `jarvis/workers/client.py`, `tests/test_worker_client.py`

**Interfaces:**
- Consumes: `RpcError` from `jarvis.types`
- Produces: `WorkerClient(socket_path)` with `.call(method: str, **params) -> dict` and `.capabilities() -> list[str]`

Protocol: newline-delimited JSON over an `AF_UNIX` stream socket.

**macOS caps `AF_UNIX` socket paths at 104 bytes.** Pytest's default `tmp_path` lives under the
system `TMPDIR`, which on this machine is deep enough that appending a socket filename exceeds the
limit and raises `OSError: AF_UNIX path too long` — a failure with nothing to do with the code under
test. Tests that bind a socket need a short directory under `/tmp`. Real sockets are unaffected:
`domains.yaml` already uses short paths like `/tmp/jarvis-mcsl.sock`.

```
→ {"id": "1", "method": "card_status", "params": {"card_id": "MCSL-384"}}
← {"id": "1", "ok": true, "result": {"speech": "...", "detail": "..."}}
← {"id": "1", "ok": false, "error": "card not found"}
```

- [ ] **Step 1: Write the failing test**

`tests/test_worker_client.py`:

```python
import json
import socket
import threading

import pytest

from jarvis.types import RpcError
from jarvis.workers.client import WorkerClient


def _fake_server(sock_path, responder):
    """Serve exactly one connection, then stop."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        with conn:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            req = json.loads(buf.decode())
            conn.sendall((json.dumps(responder(req)) + "\n").encode())
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_call_returns_result(tmp_path):
    sock_path = str(tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": True, "result": {"speech": "hi"}})

    client = WorkerClient(sock_path)
    assert client.call("card_status", card_id="MCSL-384") == {"speech": "hi"}


def test_call_sends_method_and_params(tmp_path):
    sock_path = str(tmp_path / "t.sock")
    seen = {}

    def responder(req):
        seen.update(req)
        return {"id": req["id"], "ok": True, "result": {}}

    _fake_server(sock_path, responder)
    WorkerClient(sock_path).call("card_status", card_id="MCSL-384")

    assert seen["method"] == "card_status"
    assert seen["params"] == {"card_id": "MCSL-384"}


def test_error_response_raises(tmp_path):
    sock_path = str(tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": False, "error": "card not found"})

    with pytest.raises(RpcError, match="card not found"):
        WorkerClient(sock_path).call("card_status", card_id="NOPE-1")


def test_missing_socket_raises(tmp_path):
    with pytest.raises(RpcError):
        WorkerClient(str(tmp_path / "absent.sock")).call("card_status")


def test_capabilities_returns_list(tmp_path):
    sock_path = str(tmp_path / "t.sock")
    _fake_server(
        sock_path,
        lambda req: {"id": req["id"], "ok": True, "result": {"methods": ["card_status"]}},
    )
    assert WorkerClient(sock_path).capabilities() == ["card_status"]
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_worker_client.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.workers'`

- [ ] **Step 3: Create the package marker**

`jarvis/workers/__init__.py`:

```python
"""Clients for per-domain worker processes."""
```

- [ ] **Step 4: Implement**

`jarvis/workers/client.py`:

```python
"""Newline-delimited JSON-RPC over a unix socket."""
from __future__ import annotations

import json
import socket
import uuid
from typing import Any

from jarvis.types import RpcError

DEFAULT_TIMEOUT = 30.0


class WorkerClient:
    """One client per domain. Connects per call; the *worker* is what stays warm."""

    def __init__(self, socket_path: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, method: str, **params: Any) -> dict:
        request = {"id": str(uuid.uuid4()), "method": method, "params": params}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                sock.sendall((json.dumps(request) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise RpcError(f"worker closed connection during {method}")
                    buf += chunk
        except OSError as exc:
            raise RpcError(f"cannot reach worker at {self.socket_path}: {exc}") from exc

        # Parsing must also raise RpcError: Task 6's router catches only that
        # type, and its contract is that handle_transcript never raises. A
        # worker returning garbage has to become a spoken error, not a crash.
        try:
            payload = json.loads(buf.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise RpcError(f"malformed response from worker during {method}: {exc}") from exc

        # Valid JSON is not necessarily an object. "[]", "42", and "null" all
        # parse cleanly, then .get() raises AttributeError — escaping as the
        # wrong exception type and breaking the router's never-raises contract.
        if not isinstance(payload, dict):
            raise RpcError(
                f"worker returned {type(payload).__name__}, expected object, during {method}"
            )

        if not payload.get("ok"):
            raise RpcError(payload.get("error", "unknown worker error"))
        return payload.get("result", {})

    def capabilities(self) -> list[str]:
        return self.call("capabilities").get("methods", [])
```

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/pytest tests/test_worker_client.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add jarvis/workers/__init__.py jarvis/workers/client.py tests/test_worker_client.py
git commit -m "feat: add unix-socket json-rpc worker client"
```

---

### Task 6: Router and intent registry

**Files:**
- Create: `jarvis/router/__init__.py`, `jarvis/router/intents.py`, `jarvis/router/core.py`, `tests/test_router.py`

**Interfaces:**
- Consumes: `Response`, `Vocab` from `jarvis.types`; `correct` from `jarvis.correct.snap`; `WorkerClient` from `jarvis.workers.client`
- Produces: `handle_transcript(text, vocab, worker, tier3=None) -> Response` — **the seam the whole product is tested through**

Slice 1 ships three read intents. Anything unmatched returns a tier-3 `Response`; actually running `claude -p` lands in slice 2.

- [ ] **Step 1: Write the failing test**

`tests/test_router.py`:

```python
import pytest

from jarvis.router.core import handle_transcript
from jarvis.types import Response, RpcError, Vocab


class FakeWorker:
    def __init__(
        self,
        result=None,
        error=None,
        methods=("card_status", "my_tasks", "dev_status", "customer_issues"),
    ):
        self.result = result or {"speech": "MCSL-384 is in QA Ready", "detail": "full detail"}
        self.error = error
        self.methods = list(methods)
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if self.error:
            raise RpcError(self.error)
        return self.result

    def capabilities(self):
        return self.methods


@pytest.fixture
def vocab():
    return Vocab(cards=["MCSL-384"], people=["Ashok Kumar"], zi_ids=["ZI-653"])


def test_card_status_routes_to_worker(vocab):
    worker = FakeWorker()
    resp = handle_transcript("status of MCSL three eighty four", vocab, worker)

    assert resp.tier == 1
    assert resp.ok is True
    assert worker.calls == [("card_status", {"card_id": "MCSL-384"})]
    assert "QA Ready" in resp.speech


def test_my_tasks_routes_to_worker(vocab):
    worker = FakeWorker(result={"speech": "you have 3 tasks", "detail": ""})
    resp = handle_transcript("what are my tasks", vocab, worker)

    assert worker.calls == [("my_tasks", {})]
    assert resp.speech == "you have 3 tasks"


def test_dev_status_routes_to_worker(vocab):
    worker = FakeWorker(result={"speech": "Ashok built it", "detail": ""})
    handle_transcript("who built the toggle flow", vocab, worker)

    assert worker.calls[0][0] == "dev_status"


def test_customer_issues_routes_to_worker(vocab):
    worker = FakeWorker(result={"speech": "5 open issues", "detail": ""})
    handle_transcript("any customer issues", vocab, worker)

    assert worker.calls == [("customer_issues", {})]


def test_unmatched_falls_through_to_tier_3(vocab):
    worker = FakeWorker()
    resp = handle_transcript("create a GLS carrier store", vocab, worker)

    assert resp.tier == 3
    assert worker.calls == []


def test_empty_transcript_does_not_reach_tier_3(vocab):
    worker = FakeWorker()
    resp = handle_transcript("   ", vocab, worker)

    assert resp.ok is False
    assert resp.tier == 1
    assert worker.calls == []
    assert "didn't catch" in resp.speech.lower()


def test_worker_error_is_spoken_not_swallowed(vocab):
    worker = FakeWorker(error="Trello 401 unauthorized")
    resp = handle_transcript("status of MCSL 384", vocab, worker)

    assert resp.ok is False
    assert "401" in resp.speech


def test_unsupported_capability_falls_to_tier_3(vocab):
    worker = FakeWorker(methods=["card_status"])  # no my_tasks
    resp = handle_transcript("what are my tasks", vocab, worker)

    assert resp.tier == 3
    assert worker.calls == []


def test_ambiguous_entity_asks_instead_of_guessing():
    worker = FakeWorker()
    vocab = Vocab(cards=["MCSL-384", "MCSL-390"])
    resp = handle_transcript("status of MCSL thirty eight", vocab, worker)

    if resp.needs_confirm:
        assert "did you mean" in resp.speech.lower()
        assert worker.calls == []
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_router.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.router'`

- [ ] **Step 3: Create the package marker**

`jarvis/router/__init__.py`:

```python
"""Intent matching and tier dispatch."""
```

- [ ] **Step 4: Write the intent registry**

`jarvis/router/intents.py`:

```python
"""Intent registry. Each entry maps a spoken pattern to a worker method."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Intent:
    name: str
    method: str
    pattern: re.Pattern[str]
    extract: Callable[[re.Match[str]], dict]


def _card_params(m: re.Match[str]) -> dict:
    return {"card_id": m.group("card")}


def _no_params(m: re.Match[str]) -> dict:
    return {}


def _query_params(m: re.Match[str]) -> dict:
    return {"query": m.group(0).strip()}


def _release_params(m: re.Match[str]) -> dict:
    return {"release": m.group("release")}


_CARD_ID = r"(?P<card>[A-Z]{2,6}-\d{1,5})"
# Releases are lists, not cards: "SL MCSL 386: Iteration backlog". The board's
# addressable card id is ZI-NNN; MCSL-NNN names the release that contains them.
_RELEASE_ID = r"(?P<release>MCSL[\s-]?\d{2,4})"

INTENTS: list[Intent] = [
    Intent(
        name="release_status",
        method="release_status",
        pattern=re.compile(
            rf"\b(?:what'?s? in|status of|state of|show me)\b.*?{_RELEASE_ID}", re.I
        ),
        extract=_release_params,
    ),
    Intent(
        name="card_status",
        method="card_status",
        pattern=re.compile(rf"\b(?:status|state|what'?s? happening)\b.*?{_CARD_ID}", re.I),
        extract=_card_params,
    ),
    Intent(
        name="my_tasks",
        method="my_tasks",
        pattern=re.compile(r"\b(?:my|the)\s+tasks?\b|\bwhat am i (?:working on|doing)\b", re.I),
        extract=_no_params,
    ),
    Intent(
        name="dev_status",
        method="dev_status",
        pattern=re.compile(r"\bwho (?:built|wrote|developed)\b|\bdev(?:eloper)? status\b", re.I),
        extract=_query_params,
    ),
    Intent(
        name="customer_issues",
        method="customer_issues",
        pattern=re.compile(
            r"\b(?:any )?(?:customer|merchant|support)\s+(?:issues?|tickets?|problems?)\b"
            r"|\bopen (?:zendesk|ZI) issues?\b",
            re.I,
        ),
        extract=_no_params,
    ),
]


def match(text: str) -> tuple[Intent, dict] | None:
    """Return the first matching intent and its extracted params."""
    for intent in INTENTS:
        m = intent.pattern.search(text)
        if m:
            return intent, intent.extract(m)
    return None
```

- [ ] **Step 5: Write the router core**

`jarvis/router/core.py`:

```python
"""handle_transcript — the seam the entire product is tested through."""
from __future__ import annotations

from typing import Protocol

from jarvis.correct.snap import correct
from jarvis.router.intents import match
from jarvis.types import Response, RpcError, Vocab


class Worker(Protocol):
    def call(self, method: str, **params) -> dict: ...
    def capabilities(self) -> list[str]: ...


def handle_transcript(text: str, vocab: Vocab, worker: Worker, tier3=None) -> Response:
    """Turn a raw transcript into a spoken Response.

    Never raises. Every failure becomes a Response with ok=False.
    """
    if not text or not text.strip():
        return Response(speech="Didn't catch that.", ok=False)

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

    try:
        available = worker.capabilities()
    except RpcError as exc:
        return Response(speech=f"Worker isn't responding: {exc}", ok=False)

    if intent.method not in available:
        return _tier3(result.text, tier3)

    try:
        payload = worker.call(intent.method, **params)
    except RpcError as exc:
        return Response(speech=str(exc), detail=str(exc), ok=False)

    return Response(
        speech=payload.get("speech", ""),
        detail=payload.get("detail", ""),
        tier=1,
    )


def _tier3(text: str, tier3) -> Response:
    if tier3 is None:
        return Response(
            speech="Running that in Claude Code, few minutes.",
            detail=text,
            tier=3,
        )
    return tier3(text)
```

- [ ] **Step 6: Run the tests**

```bash
.venv/bin/pytest tests/test_router.py -v
```

Expected: 9 passed.

- [ ] **Step 7: Commit**

```bash
git add jarvis/router/ tests/test_router.py
git commit -m "feat: add router with intent registry and tier-3 fallthrough"
```

---

### Task 7: The MCSL worker

**Files:**
- Create: `worker/__init__.py`, `worker/handlers.py`, `worker/main.py`, `domains.yaml`, `tests/test_worker_contract.py`

**Interfaces:**
- Consumes: `pipeline.trello_client.TrelloClient` from the target repo (inside that repo's venv only)
- Produces: a socket server answering `capabilities`, `card_status`, `my_tasks`, `dev_status`, `vocab`

`TrelloCard` carries `list_id` and `member_ids`, **not** names. Speaking a card status therefore needs `get_lists()` for the list name and `get_card_members()` for the assignee.

- [ ] **Step 1: Write the domain registry**

`domains.yaml`:

```yaml
domains:
  mcsl:
    path: /Users/madan/Documents/MCSLDomainExpert
    python: /Users/madan/Documents/MCSLDomainExpert/.venv/bin/python
    socket: /tmp/jarvis-mcsl.sock
    aliases: [mcsl, muscle, multi carrier]
    release_pattern: '^SL MCSL (\d+):'
    release_token: MCSL
  fedex:
    path: /Users/madan/Documents/Fed-Ex-automation/FedexDomainExpert
    python: /Users/madan/Documents/Fed-Ex-automation/FedexDomainExpert/.venv/bin/python
    socket: /tmp/jarvis-fedex.sock
    aliases: [fedex, fed ex]
    release_pattern: '^SL v([\d.]+) FedexApp'
  aupost:
    path: /Users/madan/Documents/AU_Post_DomainExpert/AUPostDomainExpert
    python: /Users/madan/Documents/AU_Post_DomainExpert/AUPostDomainExpert/.venv/bin/python
    socket: /tmp/jarvis-aupost.sock
    aliases: [au post, australia post, aupost]
    release_pattern: '^SL AuPost v([\d.]+):'
```

### What the live board actually looks like

The first version of this plan assumed cards are named `MCSL-384`. **They are not.** Verified against
the real board:

- **Lists are releases:** `SL MCSL 386: Iteration backlog`, `SL AuPost v1.0.39: Iteration backlog`,
  `SL v2.3.123 FedexApp: Iteration backlog`
- **Cards are issues:** `From SL: ZI-691 — MCSL does not apply cutoff time to FedEx checkout rate request`
- So `MCSL 384` addresses a **list**, and the addressable card ID is **`ZI-NNN`**.
  `get_card("MCSL-384")` returns HTTP 400 — there is no such card.
- **All three repos share one board** (the same `TRELLO_BOARD_ID` in every `.env`). Domains are
  distinguished by list-name prefix, which is why `release_pattern` lives in this file. The per-domain
  worker split still matters for RAG, wiki, and codebase queries — each repo has its own vectorstore —
  but not for Trello.

### Why the handlers avoid `get_cards_in_list`

`TrelloClient._build_trello_card` issues **three extra API calls per card** (comments, attachments,
checklists). The board holds 83 lists and 2,382 cards, so a full walk is roughly 7,000 requests and
takes 55–70 minutes. Two rules follow:

1. **Scope to the newest release list per domain**, found via `release_pattern`. That is 5–20 cards.
2. **Fetch only the fields being used.** `vocab()` needs names, so it calls the cards endpoint once
   per list with `fields=name,idMembers` — one request, not `1 + 3n`. Per-card comments are fetched
   only for the single card a `card_status` query actually names.

- [ ] **Step 2: Write the failing contract test**

`tests/test_worker_contract.py`:

```python
"""Contract test: spin the real MCSL worker and assert the protocol holds.

This is the test that catches MCSL/FedEx/AU Post drift.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from jarvis.workers.client import WorkerClient

REPO = Path(__file__).resolve().parent.parent


def _domain(name="mcsl"):
    return yaml.safe_load((REPO / "domains.yaml").read_text())["domains"][name]


@pytest.fixture(scope="module")
def mcsl_worker():
    cfg = _domain("mcsl")
    if not Path(cfg["python"]).exists():
        pytest.skip(f"venv missing: {cfg['python']}")

    sock = cfg["socket"]
    if os.path.exists(sock):
        os.unlink(sock)

    proc = subprocess.Popen(
        [cfg["python"], str(REPO / "worker" / "main.py"),
         "--repo", cfg["path"], "--socket", sock],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    for _ in range(100):
        if os.path.exists(sock):
            break
        if proc.poll() is not None:
            pytest.fail(f"worker died: {proc.stderr.read().decode()}")
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("worker did not create its socket within 10s")

    yield WorkerClient(sock)

    proc.terminate()
    proc.wait(timeout=5)


def test_capabilities_includes_card_status(mcsl_worker):
    assert "card_status" in mcsl_worker.capabilities()


def test_vocab_returns_expected_keys(mcsl_worker):
    v = mcsl_worker.call("vocab")
    assert set(v) >= {"cards", "people", "carriers", "zi_ids"}
    assert isinstance(v["cards"], list)


def test_unknown_method_returns_error_not_crash(mcsl_worker):
    from jarvis.types import RpcError

    with pytest.raises(RpcError):
        mcsl_worker.call("no_such_method")


def test_worker_survives_a_failed_call(mcsl_worker):
    from jarvis.types import RpcError

    with pytest.raises(RpcError):
        mcsl_worker.call("no_such_method")
    assert "card_status" in mcsl_worker.capabilities()
```

- [ ] **Step 3: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_worker_contract.py -v
```

Expected: FAIL — `worker/main.py` does not exist, so the worker dies immediately.

- [ ] **Step 4: Create the package marker**

`worker/__init__.py`:

```python
"""Per-domain worker. Runs inside a Domain Expert venv, never in the jarvis venv."""
```

- [ ] **Step 5: Write the handlers**

`worker/handlers.py`:

```python
"""Tier-1 handlers. These are the only code allowed to touch pipeline.*

Two rules govern every handler here, both forced by the real board's scale
(83 lists, 2382 cards):
  1. Scope to this domain's release lists, newest first. Never walk the board.
  2. Fetch only the fields in use. TrelloClient._build_trello_card issues three
     extra API calls per card, so get_cards_in_list costs 1 + 3n requests.
     Use _raw_cards for anything that needs names or membership only.
"""
from __future__ import annotations

import re
from typing import Any

# Injected by main.py from domains.yaml before any handler runs.
RELEASE_PATTERN = ""
RELEASE_TOKEN = ""

_lists_cache: list = []


def _trello():
    from pipeline.trello_client import TrelloClient

    return TrelloClient()


def _lists(client) -> list:
    global _lists_cache
    if not _lists_cache:
        _lists_cache = client.get_lists()
    return _lists_cache


def _version_key(raw: str) -> tuple:
    """Order '386' and '1.0.39' correctly."""
    return tuple(int(p) for p in raw.split(".") if p.isdigit())


def _release_lists(client) -> list:
    """This domain's release lists, newest first."""
    if not RELEASE_PATTERN:
        return []
    rx = re.compile(RELEASE_PATTERN)
    matched = []
    for lst in _lists(client):
        m = rx.search(lst.name)
        if m:
            matched.append((_version_key(m.group(1)), lst))
    matched.sort(key=lambda pair: pair[0], reverse=True)
    return [lst for _, lst in matched]


def _raw_cards(client, list_id: str, fields: str = "name,idMembers") -> list[dict]:
    """One API call. get_cards_in_list would issue 1 + 3n.

    Deliberate use of the private _get: TrelloClient exposes no field-limited
    card fetch, and Global Constraints forbid modifying the Domain Expert repo
    to add one. Revisit if _get's signature drifts.
    """
    data = client._get(f"lists/{list_id}/cards", fields=fields)
    return data if isinstance(data, list) else []


def _short(name: str) -> str:
    """'From SL: ZI-691 - title' -> 'ZI-691'."""
    m = re.search(r"\b([A-Z]{2,4}-\d{1,5})\b", name)
    return m.group(1) if m else name[:40]


def card_status(card_id: str) -> dict[str, Any]:
    """Status of an issue card. Cards are named 'From SL: ZI-691 - <title>',
    so the ZI id is matched against the name rather than passed to get_card —
    Trello's card endpoint wants its own id or shortLink and 400s on 'ZI-691'.
    """
    client = _trello()
    token = card_id.upper()

    for lst in _release_lists(client):
        for raw in _raw_cards(client, lst.id, fields="name"):
            if token in raw.get("name", "").upper():
                return _describe(client, raw, lst.name)

    return {"speech": f"Couldn't find {card_id} in the current releases.", "detail": ""}


def _describe(client, raw: dict, list_name: str) -> dict[str, Any]:
    name = raw.get("name", "")
    title = name.split("—")[-1].strip() if "—" in name else name
    card_id = raw.get("id", "")

    members = client.get_card_members(card_id) if card_id else []
    who = (members[0].get("fullName") or members[0].get("username")) if members else "nobody"

    comments = client.get_card_comments(card_id) if card_id else []
    latest = comments[0] if comments else ""

    speech = f"{title[:90]} is in {list_name}, assigned to {who}."
    if latest:
        speech += f" Last comment: {latest[:160]}"
    return {"speech": speech, "detail": f"{name}\nList: {list_name}\nMembers: {who}\n\n{latest}"}


def release_status(release: str) -> dict[str, Any]:
    """Summarise a release list, e.g. 'MCSL 386' -> 'SL MCSL 386: Iteration backlog'."""
    client = _trello()
    wanted = re.sub(r"[^0-9.]", "", release)
    if not wanted:
        return {"speech": f"Didn't recognise the release {release}.", "detail": ""}

    for lst in _release_lists(client):
        if wanted in lst.name.replace(" ", ""):
            cards = _raw_cards(client, lst.id, fields="name")
            names = [c.get("name", "") for c in cards]
            speech = f"{lst.name} has {len(names)} cards"
            if names:
                speech += ": " + "; ".join(_short(n) for n in names[:4])
            return {"speech": speech, "detail": "\n".join(names)}

    return {"speech": f"No release list matching {release}.", "detail": ""}


def my_tasks() -> dict[str, Any]:
    """Cards assigned to the authenticated Trello member in the current releases."""
    client = _trello()
    # Deliberate use of the private _get: TrelloClient exposes no public
    # "who am I" call, and Global Constraints forbid modifying the Domain
    # Expert repo to add one.
    me = client._get("members/me", fields="id")["id"]

    mine = []
    for lst in _release_lists(client)[:3]:
        for raw in _raw_cards(client, lst.id):
            if me in (raw.get("idMembers") or []):
                mine.append((_short(raw.get("name", "")), lst.name))

    if not mine:
        return {"speech": "Nothing assigned to you in the current releases.", "detail": ""}

    speech = f"You have {len(mine)} cards. " + ", ".join(f"{n} in {l}" for n, l in mine[:5])
    return {"speech": speech, "detail": "\n".join(f"{n} — {l}" for n, l in mine)}


def dev_status(query: str) -> dict[str, Any]:
    """Answer from the RAG store over wiki plus codebase."""
    from rag.vectorstore import search_filtered

    docs = search_filtered(query, k=3) or []
    if not docs:
        return {"speech": "Nothing in the knowledge base on that.", "detail": ""}

    top = docs[0]
    body = getattr(top, "page_content", str(top))
    source = getattr(top, "metadata", {}).get("source", "wiki")
    return {"speech": body[:240], "detail": f"source: {source}\n\n{body}"}


def customer_issues() -> dict[str, Any]:
    """Open ZI issues from the newest Zendesk intake file in the wiki.

    Reads the wiki directly rather than through RAG. The RAG path in
    requirement_research.py filters on a category string that wiki_loader.py
    never emits, so it returns nothing; reading the source file is both
    correct today and independent of that fix.
    """
    import re
    from pathlib import Path

    import config

    zdir = Path(config.WIKI_PATH) / "zendesk"
    files = sorted(zdir.glob("20*.md")) if zdir.is_dir() else []
    if not files:
        return {"speech": "No Zendesk intake files found.", "detail": ""}

    body = files[-1].read_text(errors="ignore")
    rows = re.findall(r"^\|\s*(ZI-\d+)\s*\|\s*([^|]+?)\s*\|", body, re.M)
    if not rows:
        return {"speech": f"No open issues in {files[-1].stem}.", "detail": body[:800]}

    speech = f"{len(rows)} open issues in {files[-1].stem}. " + "; ".join(
        f"{zi}: {title[:70]}" for zi, title in rows[:3]
    )
    detail = "\n".join(f"{zi} — {title}" for zi, title in rows)
    return {"speech": speech, "detail": detail}


def vocab() -> dict[str, Any]:
    """Live entity snapshot for the correction layer.

    Scoped to the three newest release lists — one API call each. A full board
    walk would be ~7000 requests and take the better part of an hour.
    """
    client = _trello()
    releases = _release_lists(client)

    ids: set[str] = set()
    for lst in releases[:3]:
        for raw in _raw_cards(client, lst.id, fields="name"):
            for m in re.finditer(r"\b([A-Z]{2,4}-\d{1,5})\b", raw.get("name", "")):
                ids.add(m.group(1))

    # Release tokens so "status of MCSL 386" can snap to a real release.
    # Only emitted where releases are plain integers; AuPost/FedEx use dotted
    # versions that the ID pattern cannot represent, so they get none.
    if RELEASE_TOKEN:
        rx = re.compile(RELEASE_PATTERN)
        for lst in releases:
            m = rx.search(lst.name)
            if m and m.group(1).isdigit():
                ids.add(f"{RELEASE_TOKEN}-{m.group(1)}")

    people = [m.get("fullName", "") for m in client.get_board_members() if m.get("fullName")]

    return {
        "cards": sorted(ids),
        "people": sorted(set(people)),
        "carriers": ["gls", "ups", "fedex", "dhl", "usps", "india post", "canada post"],
        "zi_ids": _zi_ids(),
    }


def _zi_ids() -> list[str]:
    """Parse valid ZI ranges from the wiki's zendesk intake frontmatter."""
    import re
    from pathlib import Path

    import config

    zdir = Path(config.WIKI_PATH) / "zendesk"
    if not zdir.is_dir():
        return []

    ids: set[str] = set()
    for path in zdir.glob("*.md"):
        head = path.read_text(errors="ignore")[:1200]
        m = re.search(r"new_ids_assigned:\s*\"?ZI-(\d+)\s*→\s*ZI-(\d+)", head)
        if m:
            for n in range(int(m.group(1)), int(m.group(2)) + 1):
                ids.add(f"ZI-{n}")
    return sorted(ids)


HANDLERS = {
    "card_status": card_status,
    "release_status": release_status,
    "my_tasks": my_tasks,
    "dev_status": dev_status,
    "customer_issues": customer_issues,
    "vocab": vocab,
}
```

- [ ] **Step 6: Write the worker server**

`worker/main.py`:

```python
"""Socket server. Launched with a Domain Expert's own python, inside its own repo."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import traceback


def _bootstrap(repo: str) -> None:
    """Put the target repo on sys.path and load its .env before importing handlers."""
    sys.path.insert(0, repo)
    os.chdir(repo)
    env = os.path.join(repo, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _dispatch(handlers, method: str, params: dict) -> dict:
    if method == "capabilities":
        return {"methods": sorted(handlers)}
    if method not in handlers:
        raise ValueError(f"unknown method: {method}")
    return handlers[method](**params)


def serve(repo: str, sock_path: str, release_pattern: str = "", release_token: str = "") -> None:
    _bootstrap(repo)
    import handlers  # noqa: E402  (must follow _bootstrap)

    # Domain-specific config from domains.yaml. Handlers read these to decide
    # which lists are this domain's releases; without a pattern they see none.
    handlers.RELEASE_PATTERN = release_pattern
    handlers.RELEASE_TOKEN = release_token
    HANDLERS = handlers.HANDLERS

    if os.path.exists(sock_path):
        os.unlink(sock_path)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(8)
    print(f"jarvis worker ready: {repo} -> {sock_path}", flush=True)

    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                try:
                    buf = b""
                    while not buf.endswith(b"\n"):
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    if not buf:
                        continue
                    req = json.loads(buf.decode())
                    result = _dispatch(HANDLERS, req.get("method", ""), req.get("params", {}))
                    reply = {"id": req.get("id"), "ok": True, "result": result}
                except Exception as exc:  # a bad call must never kill the worker
                    traceback.print_exc()
                    reply = {"id": locals().get("req", {}).get("id"), "ok": False, "error": str(exc)}
                conn.sendall((json.dumps(reply) + "\n").encode())
    finally:
        if os.path.exists(sock_path):
            os.unlink(sock_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--release-pattern", default="")
    parser.add_argument("--release-token", default="")
    args = parser.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    serve(args.repo, args.socket, args.release_pattern, args.release_token)
```

- [ ] **Step 7: Add PyYAML to the test path and run the contract test**

```bash
.venv/bin/pytest tests/test_worker_contract.py -v
```

Expected: 4 passed. If the worker dies, its stderr is printed by the fixture — the usual cause is missing Trello credentials in the target repo's `.env`.

- [ ] **Step 8: Verify a real card end to end**

```bash
.venv/bin/python -c "
from jarvis.workers.client import WorkerClient
import subprocess, time, os, yaml
cfg = yaml.safe_load(open('domains.yaml'))['domains']['mcsl']
p = subprocess.Popen([cfg['python'], 'worker/main.py', '--repo', cfg['path'], '--socket', cfg['socket']])
time.sleep(3)
print(WorkerClient(cfg['socket']).call('vocab')['cards'][:5])
p.terminate()
"
```

Expected: a list of real card IDs from your board, e.g. `['MCSL-380', 'MCSL-381', ...]`. If empty, card names on the board do not start with a hyphenated token and `vocab()` needs its parsing adjusted to match.

- [ ] **Step 9: Commit**

```bash
git add worker/ domains.yaml tests/test_worker_contract.py
git commit -m "feat: add mcsl worker with card_status, my_tasks, dev_status, vocab"
```

---

### Task 8: Voice output and the CLI demo

**Files:**
- Create: `jarvis/voice/__init__.py`, `jarvis/voice/speak.py`, `jarvis/cli.py`, `tests/test_speak.py`

**Interfaces:**
- Consumes: `Response` from `jarvis.types`
- Produces: `speak(response: Response, notify: bool = True) -> None`, `say(text: str) -> None`, `notify(title: str, body: str) -> None`

After this task there is a working demo with no microphone: type a command, hear the answer.

- [ ] **Step 1: Write the failing test**

`tests/test_speak.py`:

```python
from unittest.mock import patch

from jarvis.types import Response
from jarvis.voice.speak import notify, say, speak


def test_say_shells_out_to_macos_say():
    with patch("subprocess.run") as run:
        say("hello there")
    assert run.call_args[0][0][0] == "say"
    assert "hello there" in run.call_args[0][0]


def test_say_ignores_empty_text():
    with patch("subprocess.run") as run:
        say("")
    run.assert_not_called()


def test_notify_uses_osascript():
    with patch("subprocess.run") as run:
        notify("Jarvis", "MCSL-384 is in QA Ready")
    assert run.call_args[0][0][0] == "osascript"


def test_notify_escapes_double_quotes():
    with patch("subprocess.run") as run:
        notify("Jarvis", 'he said "hello"')
    script = run.call_args[0][0][2]
    assert '\\"hello\\"' in script


def test_speak_says_speech_and_notifies_with_detail():
    resp = Response(speech="short line", detail="the long body")
    with patch("jarvis.voice.speak.say") as s, patch("jarvis.voice.speak.notify") as n:
        speak(resp)
    s.assert_called_once_with("short line")
    assert "the long body" in n.call_args[0][1]


def test_speak_falls_back_to_speech_when_detail_is_empty():
    resp = Response(speech="only this")
    with patch("jarvis.voice.speak.say"), patch("jarvis.voice.speak.notify") as n:
        speak(resp)
    assert "only this" in n.call_args[0][1]
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_speak.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.voice'`

- [ ] **Step 3: Create the package marker**

`jarvis/voice/__init__.py`:

```python
"""Speech output and desktop notifications."""
```

- [ ] **Step 4: Implement**

`jarvis/voice/speak.py`:

```python
"""macOS speech and notifications. Both are free and local."""
from __future__ import annotations

import subprocess

from jarvis.types import Response

VOICE = "Daniel"  # swap for any voice in `say -v '?'`
RATE = 190


def say(text: str) -> None:
    if not text or not text.strip():
        return
    # `--` ends option parsing. Without it, spoken text beginning with a dash
    # is read as a flag and `say` refuses the whole utterance:
    #   say -v Daniel "-x hello"  ->  say: invalid option -- x
    # Trello titles and comments routinely start with "-".
    subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "--", text], check=False)


def notify(title: str, body: str) -> None:
    safe_body = body.replace("\\", "\\\\").replace('"', '\\"')
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def speak(response: Response, notify_user: bool = True) -> None:
    """Say the short line; put the full text on screen so a mishear is recoverable."""
    say(response.speech)
    if notify_user:
        notify("Jarvis", (response.detail or response.speech)[:400])
```

- [ ] **Step 5: Write the CLI**

`jarvis/cli.py`:

```python
"""Type commands instead of speaking them. The demo and debug entry point.

    .venv/bin/python -m jarvis.cli "status of MCSL 384"
    .venv/bin/python -m jarvis.cli          # interactive
"""
from __future__ import annotations

import sys

from jarvis.router.core import handle_transcript
from jarvis.types import Vocab
from jarvis.voice.speak import speak
from jarvis.workers.manager import WorkerManager


def main() -> None:
    manager = WorkerManager()
    worker = manager.get("mcsl")
    raw = worker.call("vocab")
    vocab = Vocab(**{k: raw.get(k, []) for k in ("cards", "people", "carriers", "zi_ids")})

    if len(sys.argv) > 1:
        lines = [" ".join(sys.argv[1:])]
    else:
        lines = iter(lambda: input("you> "), "")

    for line in lines:
        response = handle_transcript(line, vocab, worker)
        print(f"jarvis> [tier {response.tier}] {response.speech}")
        speak(response)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the tests**

```bash
.venv/bin/pytest tests/test_speak.py -v
```

Expected: 6 passed. (`jarvis/cli.py` depends on `WorkerManager` from Task 9 and is not runnable until then.)

- [ ] **Step 7: Commit**

```bash
git add jarvis/voice/ jarvis/cli.py tests/test_speak.py
git commit -m "feat: add macos speech, notifications, and cli entry point"
```

---

### Task 9: Worker lifecycle manager

**Files:**
- Create: `jarvis/workers/manager.py`, `tests/test_worker_manager.py`

**Interfaces:**
- Consumes: `WorkerClient` from `jarvis.workers.client`, `domains.yaml`
- Produces: `WorkerManager(config_path=None)` with `.get(domain) -> WorkerClient`, `.shutdown() -> None`, `.resolve_alias(text) -> str | None`

Spawns a worker on first use, keeps it warm, restarts it once if it dies.

- [ ] **Step 1: Write the failing test**

`tests/test_worker_manager.py`:

```python
import pytest

from jarvis.workers.manager import WorkerManager

CONFIG = """
domains:
  mcsl:
    path: /tmp/fake-mcsl
    python: /usr/bin/python3
    socket: /tmp/jarvis-test-mcsl.sock
    aliases: [mcsl, muscle]
    card_prefix: MCSL
  fedex:
    path: /tmp/fake-fedex
    python: /usr/bin/python3
    socket: /tmp/jarvis-test-fedex.sock
    aliases: [fedex, fed ex]
    card_prefix: ""
"""


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "domains.yaml"
    p.write_text(CONFIG)
    return str(p)


def test_lists_configured_domains(config):
    assert set(WorkerManager(config).domains()) == {"mcsl", "fedex"}


def test_unknown_domain_raises(config):
    with pytest.raises(KeyError):
        WorkerManager(config).get("nope")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("switch to fedex", "fedex"),
        ("switch to fed ex please", "fedex"),
        ("muscle status of 384", "mcsl"),
        ("what are my tasks", None),
    ],
)
def test_resolve_alias(config, text, expected):
    assert WorkerManager(config).resolve_alias(text) == expected


def test_socket_path_comes_from_config(config):
    assert WorkerManager(config)._config("mcsl")["socket"] == "/tmp/jarvis-test-mcsl.sock"
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_worker_manager.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.workers.manager'`

- [ ] **Step 3: Implement**

`jarvis/workers/manager.py`:

```python
"""Spawn, reuse, and reap per-domain workers."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import yaml

from jarvis.types import RpcError
from jarvis.workers.client import WorkerClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IDLE_TIMEOUT_SECONDS = 600  # 10 minutes, per the spec
STARTUP_TIMEOUT_SECONDS = 20


class WorkerManager:
    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = config_path or str(REPO_ROOT / "domains.yaml")
        self._cfg = yaml.safe_load(Path(self.config_path).read_text())["domains"]
        self._procs: dict[str, subprocess.Popen] = {}
        self._clients: dict[str, WorkerClient] = {}

    def domains(self) -> list[str]:
        return list(self._cfg)

    def _config(self, domain: str) -> dict:
        if domain not in self._cfg:
            raise KeyError(f"unknown domain: {domain}")
        return self._cfg[domain]

    def resolve_alias(self, text: str) -> str | None:
        """Find a domain named anywhere in the text. Longest alias wins."""
        lowered = text.lower()
        best: tuple[int, str] | None = None
        for name, cfg in self._cfg.items():
            for alias in cfg.get("aliases", []):
                if alias in lowered and (best is None or len(alias) > best[0]):
                    best = (len(alias), name)
        return best[1] if best else None

    def get(self, domain: str) -> WorkerClient:
        cfg = self._config(domain)
        proc = self._procs.get(domain)
        if proc is not None and proc.poll() is None:
            return self._clients[domain]
        return self._spawn(domain, cfg)

    def _spawn(self, domain: str, cfg: dict) -> WorkerClient:
        sock = cfg["socket"]
        if os.path.exists(sock):
            os.unlink(sock)

        proc = subprocess.Popen(
            [cfg["python"], str(REPO_ROOT / "worker" / "main.py"),
             "--repo", cfg["path"], "--socket", sock],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        deadline = time.time() + STARTUP_TIMEOUT_SECONDS
        while time.time() < deadline:
            if os.path.exists(sock):
                break
            if proc.poll() is not None:
                err = proc.stderr.read().decode() if proc.stderr else ""
                raise RpcError(f"{domain} worker died on startup: {err[-500:]}")
            time.sleep(0.1)
        else:
            proc.kill()
            raise RpcError(f"{domain} worker did not start within {STARTUP_TIMEOUT_SECONDS}s")

        self._procs[domain] = proc
        self._clients[domain] = WorkerClient(sock)
        return self._clients[domain]

    def shutdown(self) -> None:
        for proc in self._procs.values():
            proc.terminate()
        for proc in self._procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._procs.clear()
        self._clients.clear()
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/pytest tests/test_worker_manager.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run the full demo — no microphone needed**

```bash
.venv/bin/python -m jarvis.cli "status of MCSL three eighty four"
```

Expected: prints `jarvis> [tier 1] MCSL-384 is in ... assigned to ...` and speaks it aloud. Replace 384 with a card that exists on your board.

- [ ] **Step 6: Commit**

```bash
git add jarvis/workers/manager.py tests/test_worker_manager.py
git commit -m "feat: add worker lifecycle manager with alias resolution"
```

---

### Task 10: Speech to text

**Files:**
- Create: `jarvis/ears/__init__.py`, `jarvis/ears/stt.py`, `tests/test_stt.py`

**Interfaces:**
- Consumes: `Vocab` from `jarvis.types`
- Produces: `Transcriber(model_size="small.en")` with `.transcribe(audio: np.ndarray) -> str`, `build_initial_prompt(vocab) -> str`

The `initial_prompt` biases decoding toward your jargon *before* the correction layer runs — the cheapest accuracy win available.

- [ ] **Step 1: Write the failing test**

`tests/test_stt.py`:

```python
import numpy as np
import pytest

from jarvis.ears.stt import Transcriber, build_initial_prompt
from jarvis.types import Vocab


def test_initial_prompt_includes_card_ids():
    prompt = build_initial_prompt(Vocab(cards=["MCSL-384"], people=["Ashok Kumar"]))
    assert "MCSL-384" in prompt
    assert "Ashok Kumar" in prompt


def test_initial_prompt_includes_baseline_jargon():
    prompt = build_initial_prompt(Vocab())
    for term in ("Trello", "toggle", "rate shopping"):
        assert term in prompt


def test_initial_prompt_is_bounded():
    huge = Vocab(cards=[f"MCSL-{i}" for i in range(500)])
    assert len(build_initial_prompt(huge)) <= 900


def test_transcriber_defers_model_load():
    t = Transcriber()
    assert t._model is None


@pytest.mark.audio
def test_transcribe_silence_returns_empty():
    t = Transcriber()
    silence = np.zeros(16000, dtype=np.float32)
    assert t.transcribe(silence).strip() == ""
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_stt.py -v -m "not audio"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.ears'`

- [ ] **Step 3: Create the package marker**

`jarvis/ears/__init__.py`:

```python
"""Audio input: wake detection and transcription."""
```

- [ ] **Step 4: Implement**

`jarvis/ears/stt.py`:

```python
"""faster-whisper wrapper with domain-biased decoding."""
from __future__ import annotations

import numpy as np

from jarvis.types import Vocab

SAMPLE_RATE = 16000
MAX_PROMPT_CHARS = 900

BASELINE_JARGON = (
    "MCSL, FedEx, AU Post, Trello, Zendesk, Slack, Shopify, "
    "toggle, rate shopping, carrier, packaging, QA Ready, ZI"
)


def build_initial_prompt(vocab: Vocab) -> str:
    """Bias whisper's decoder toward real entity names."""
    parts = [BASELINE_JARGON]
    for group in (vocab.people, vocab.carriers, vocab.cards, vocab.zi_ids):
        if group:
            parts.append(", ".join(group))
    prompt = ", ".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS].rsplit(",", 1)[0]
    return prompt


class Transcriber:
    """Loads the model lazily so importing this module stays cheap."""

    def __init__(self, model_size: str = "small.en") -> None:
        self.model_size = model_size
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, audio: np.ndarray, vocab: Vocab | None = None) -> str:
        model = self._ensure_model()
        segments, _ = model.transcribe(
            audio,
            language="en",
            initial_prompt=build_initial_prompt(vocab) if vocab else BASELINE_JARGON,
            vad_filter=True,
            beam_size=1,
        )
        return " ".join(s.text for s in segments).strip()
```

- [ ] **Step 5: Run the fast tests**

```bash
.venv/bin/pytest tests/test_stt.py -v -m "not audio"
```

Expected: 4 passed, 1 deselected.

- [ ] **Step 6: Download the model and run the audio test once**

```bash
.venv/bin/pytest tests/test_stt.py -v -m audio
```

Expected: PASS. First run downloads ~460MB of `small.en` weights, so allow a few minutes.

- [ ] **Step 7: Commit**

```bash
git add jarvis/ears/__init__.py jarvis/ears/stt.py tests/test_stt.py
git commit -m "feat: add faster-whisper transcription with jargon biasing"
```

---

### Task 11: Wake detection

**Files:**
- Create: `jarvis/ears/wake.py`, `tests/test_wake.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `ClapDetector(sample_rate, ratio, cooldown)` with `.feed(chunk) -> bool`, `WakeListener(on_wake)` with `.run()`

The clap detector is the one idea worth taking from `hectorg2211/jarvis`: a spike-ratio test against a rolling baseline, plus a cooldown. Two claps within a window fire the wake.

- [ ] **Step 1: Write the failing test**

`tests/test_wake.py`:

```python
import numpy as np

from jarvis.ears.wake import ClapDetector


def _quiet(n=1600):
    return np.full(n, 0.01, dtype=np.float32)


def _spike(n=1600):
    a = np.full(n, 0.01, dtype=np.float32)
    a[:80] = 0.9
    return a


def test_quiet_audio_never_wakes():
    d = ClapDetector()
    assert not any(d.feed(_quiet()) for _ in range(20))


def test_single_clap_does_not_wake():
    d = ClapDetector()
    for _ in range(10):
        d.feed(_quiet())
    assert d.feed(_spike()) is False


def test_two_claps_wake():
    d = ClapDetector()
    for _ in range(10):
        d.feed(_quiet())
    d.feed(_spike())
    d.feed(_quiet())
    assert d.feed(_spike()) is True


def test_two_claps_too_far_apart_do_not_wake():
    d = ClapDetector(double_window_chunks=2)
    for _ in range(10):
        d.feed(_quiet())
    d.feed(_spike())
    for _ in range(6):
        d.feed(_quiet())
    assert d.feed(_spike()) is False


def test_cooldown_suppresses_immediate_retrigger():
    d = ClapDetector()
    for _ in range(10):
        d.feed(_quiet())
    d.feed(_spike())
    d.feed(_quiet())
    assert d.feed(_spike()) is True
    d.feed(_spike())
    assert d.feed(_spike()) is False
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_wake.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.ears.wake'`

- [ ] **Step 3: Implement**

`jarvis/ears/wake.py`:

```python
"""Two wake paths on one audio stream: double-clap and 'hey jarvis'."""
from __future__ import annotations

import collections
from typing import Callable

import numpy as np

from jarvis.ears.stt import SAMPLE_RATE  # single source of truth, defined in stt.py

CHUNK = 1280  # 80ms at 16kHz — openwakeword's expected frame size


class ClapDetector:
    """Spike-ratio detector against a rolling baseline, with a double-clap window."""

    def __init__(
        self,
        ratio: float = 8.0,
        double_window_chunks: int = 8,
        cooldown_chunks: int = 12,
        baseline_len: int = 25,
    ) -> None:
        self.ratio = ratio
        self.double_window_chunks = double_window_chunks
        self.cooldown_chunks = cooldown_chunks
        self._baseline: collections.deque[float] = collections.deque(maxlen=baseline_len)
        self._since_first_clap: int | None = None
        self._cooldown = 0

    def feed(self, chunk: np.ndarray) -> bool:
        """Return True when a double-clap completes on this chunk."""
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        baseline = float(np.mean(self._baseline)) if self._baseline else 0.0

        if self._cooldown > 0:
            self._cooldown -= 1
            self._baseline.append(peak)
            return False

        is_spike = baseline > 0 and peak > baseline * self.ratio

        if self._since_first_clap is not None:
            self._since_first_clap += 1
            if self._since_first_clap > self.double_window_chunks:
                self._since_first_clap = None

        if not is_spike:
            self._baseline.append(peak)
            return False

        if self._since_first_clap is None:
            self._since_first_clap = 0
            return False

        self._since_first_clap = None
        self._cooldown = self.cooldown_chunks
        return True


class WakeListener:
    """Runs the mic and calls on_wake() when either path fires."""

    def __init__(self, on_wake: Callable[[], None], use_wakeword: bool = True) -> None:
        self.on_wake = on_wake
        self.clap = ClapDetector()
        self.use_wakeword = use_wakeword
        self._oww = None

    def _ensure_wakeword(self):
        if self._oww is None and self.use_wakeword:
            from openwakeword.model import Model

            self._oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        return self._oww

    def run(self) -> None:
        import sounddevice as sd

        oww = self._ensure_wakeword()

        def callback(indata, frames, time_info, status):
            mono = indata[:, 0].astype(np.float32)
            if self.clap.feed(mono):
                self.on_wake()
                return
            if oww is not None:
                scores = oww.predict((mono * 32767).astype(np.int16))
                if any(v > 0.5 for v in scores.values()):
                    self.on_wake()

        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, blocksize=CHUNK, callback=callback
        ):
            while True:
                sd.sleep(1000)
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/pytest tests/test_wake.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ears/wake.py tests/test_wake.py
git commit -m "feat: add double-clap and hey-jarvis wake detection"
```

---

### Task 12: Daemon wiring

**Files:**
- Create: `jarvis/daemon.py`, `tests/test_daemon.py`
- Modify: `README.md` (create)

**Interfaces:**
- Consumes: everything from Tasks 2–11
- Produces: `Jarvis(domain="mcsl")` with `.handle_utterance(audio) -> Response`, `.run() -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_daemon.py`:

```python
from unittest.mock import MagicMock, patch

import numpy as np

from jarvis.daemon import Jarvis
from jarvis.types import Vocab


def _jarvis_with_fakes(transcript, worker_result=None):
    j = Jarvis.__new__(Jarvis)
    j.domain = "mcsl"
    j.vocab = Vocab(cards=["MCSL-384"])
    j.transcriber = MagicMock()
    j.transcriber.transcribe.return_value = transcript
    j.worker = MagicMock()
    j.worker.capabilities.return_value = [
        "card_status", "my_tasks", "dev_status", "customer_issues",
    ]
    j.worker.call.return_value = worker_result or {"speech": "in QA Ready", "detail": ""}
    j.manager = MagicMock()
    return j


def test_utterance_produces_a_tier_1_response():
    j = _jarvis_with_fakes("status of MCSL three eighty four")
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert resp.tier == 1
    assert resp.speech == "in QA Ready"


def test_empty_transcript_is_not_spoken_to_the_worker():
    j = _jarvis_with_fakes("")
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert resp.ok is False
    j.worker.call.assert_not_called()


def test_domain_switch_changes_sticky_domain():
    j = _jarvis_with_fakes("switch to fedex")
    j.manager.resolve_alias.return_value = "fedex"
    with patch("jarvis.daemon.speak"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert j.domain == "fedex"


def test_response_is_always_spoken():
    j = _jarvis_with_fakes("status of MCSL 384")
    with patch("jarvis.daemon.speak") as spoken:
        j.handle_utterance(np.zeros(16000, dtype=np.float32))
    spoken.assert_called_once()
```

- [ ] **Step 2: Run it to confirm failure**

```bash
.venv/bin/pytest tests/test_daemon.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.daemon'`

- [ ] **Step 3: Implement**

`jarvis/daemon.py`:

```python
"""The always-on process. Wires ears, router, workers, and voice together."""
from __future__ import annotations

import numpy as np
import sounddevice as sd

from jarvis.ears.stt import SAMPLE_RATE, Transcriber
from jarvis.ears.wake import WakeListener
from jarvis.router.core import handle_transcript
from jarvis.types import Response, Vocab
from jarvis.voice.speak import say, speak
from jarvis.workers.manager import WorkerManager

CAPTURE_SECONDS = 6.0
SWITCH_PHRASES = ("switch to", "use ", "jarvis,")


class Jarvis:
    def __init__(self, domain: str = "mcsl") -> None:
        self.manager = WorkerManager()
        self.domain = domain
        self.worker = self.manager.get(domain)
        self.vocab = self._load_vocab()
        self.transcriber = Transcriber()

    def _load_vocab(self) -> Vocab:
        raw = self.worker.call("vocab")
        return Vocab(**{k: raw.get(k, []) for k in ("cards", "people", "carriers", "zi_ids")})

    def _switch_domain(self, target: str) -> None:
        self.domain = target
        self.worker = self.manager.get(target)
        self.vocab = self._load_vocab()

    def handle_utterance(self, audio: np.ndarray) -> Response:
        text = self.transcriber.transcribe(audio, self.vocab)

        target = self.manager.resolve_alias(text) if text else None
        if target and target != self.domain and any(p in text.lower() for p in SWITCH_PHRASES):
            self._switch_domain(target)
            response = Response(speech=f"Switched to {target}.")
            speak(response)
            return response

        response = handle_transcript(text, self.vocab, self.worker)
        speak(response)
        return response

    def _capture(self) -> np.ndarray:
        frames = int(CAPTURE_SECONDS * SAMPLE_RATE)
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        return audio[:, 0]

    def _on_wake(self) -> None:
        say("Yes?")
        self.handle_utterance(self._capture())

    def run(self) -> None:
        print(f"jarvis listening — domain: {self.domain}")
        try:
            WakeListener(self._on_wake).run()
        except KeyboardInterrupt:
            pass
        finally:
            self.manager.shutdown()


def main() -> None:
    Jarvis().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/pytest tests/test_daemon.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the whole suite**

```bash
.venv/bin/pytest -v -m "not audio"
```

Expected: all pass. The contract test in Task 7 spawns a real MCSL worker; it skips cleanly if that venv is absent.

- [ ] **Step 6: Write the README**

`README.md`:

```markdown
# Jarvis

Voice front-end to the MCSL, FedEx, and AU Post Domain Expert platforms.

## Setup

    brew install portaudio
    /opt/homebrew/bin/python3.12 -m venv .venv
    .venv/bin/pip install -r requirements.txt

## Run

    .venv/bin/python -m jarvis.daemon          # voice
    .venv/bin/python -m jarvis.cli "status of MCSL 384"   # typed, for debugging

Wake with a double clap or by saying "hey jarvis".

## Test

    .venv/bin/pytest -m "not audio"   # fast
    .venv/bin/pytest                  # includes model download and mic tests

## Design

See `docs/superpowers/specs/2026-08-15-jarvis-voice-assistant-design.md`.
Slice 1 is read-only: card status, my tasks, dev status. No writes, no memory, no watchers.
```

- [ ] **Step 7: Grant microphone access and smoke-test by voice**

```bash
.venv/bin/python -m jarvis.daemon
```

macOS will prompt for microphone access on first run — grant it, then restart. Clap twice, wait for "Yes?", and say "status of MCSL three eighty four".

Expected: the card's list and assignee spoken back, plus a notification.

- [ ] **Step 8: Commit**

```bash
git add jarvis/daemon.py tests/test_daemon.py README.md
git commit -m "feat: wire wake, stt, router, and voice into the daemon"
```

---

## Verification

Slice 1 is done when all of these hold:

- [ ] `.venv/bin/pytest -m "not audio"` — all pass
- [ ] `.venv/bin/pytest -m audio` — all pass
- [ ] `.venv/bin/python -m jarvis.cli "status of MCSL <real card>"` speaks the right list and assignee
- [ ] Double-clap wakes it; "hey jarvis" wakes it
- [ ] An unmatched command returns a tier-3 response instead of an error
- [ ] Silence produces "Didn't catch that" and never reaches the worker
- [ ] Killing the worker mid-session produces a spoken error, not a traceback
- [ ] No Domain Expert repo has been modified: `git -C /Users/madan/Documents/MCSLDomainExpert status --porcelain` shows nothing new from this work

## Spec coverage note

Spec §4.1 lists five read intents. Four ship in slice 1: `card_status`, `my_tasks`,
`dev_status`, `customer_issues`.

The fifth — **"what changed"** — is deferred to slice 3, not overlooked. It is defined as a diff
*since the last check*, which requires persisted state (a last-seen timestamp or commit SHA). Slice 1
has no storage layer by design. Implementing it here would mean either a throwaway state file or
answering a different question than the spec asks.

## Deferred to later slices

Slice 2: write actions, read-back confirmation, real `claude -p` tier-3 execution.
Slice 3: SQLite tasks, notes, reminders.
Slice 4: watchers — wiki git, Trello diff, Slack replies, reminder scheduler.
Phase B: FedEx as domain #2. Phase C: AU Post.

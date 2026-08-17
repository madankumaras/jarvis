# Jarvis — Voice Assistant for the Domain Expert Platforms

**Date:** 2026-08-15
**Status:** Design approved, ready for planning
**Scope:** Phase A (MCSL) specified in full. Phases B and C outlined.

---

## 1. Purpose

A always-on voice assistant that does three things:

1. **Listens** — wake on double-clap or "hey jarvis", understand spoken commands.
2. **Works** — run real actions: read Trello card status, send Slack DMs, create carrier stores, generate AC/TC.
3. **Suggests** — proactively surface reminders, new Zendesk issues, Trello changes, and Slack replies.

It is a **voice front-end to the three existing Domain Expert repos**, not a new QA platform. Nearly all
capability already exists; what is missing is ears, a mouth, persistent memory, and a scheduler.

### Target repos

| Domain | Path |
|---|---|
| MCSL | `~/Documents/MCSLDomainExpert` |
| FedEx | `~/Documents/Fed-Ex-automation/FedexDomainExpert` |
| AU Post | `~/Documents/AU_Post_DomainExpert/AUPostDomainExpert` |

These three are structural siblings — each has 14 skills, `CLAUDE.md`, `config.py`, `pipeline/`,
`rag/`, `ingest/`, `trello_client.py`, `slack_client.py`. **They have drifted.** MCSL has
`notify_toggle_enablement`, `detect_toggle_details`, `check_toggle_reply`; FedEx and AU Post do not.
AU Post lacks `upload_file_to_slack_channel` and `send_dm_to_user`. `post_results` has a different
signature in AU Post. The design must not assume a shared function set.

---

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Repo location | **Standalone** `~/Documents/jarvis` | Must serve all three Domain Experts; cannot live inside one |
| Domain scope | **Sticky context, one at a time** | No cross-domain fan-out — not needed, materially simpler |
| Language | **English only** | Enables `faster-whisper small.en`: faster and more accurate than multilingual |
| Wake | **Double-clap AND "hey jarvis"** | Two detectors, one audio stream. Clap for busy hands, wake word for flow |
| Write actions | **Spoken read-back, then explicit "ok"** | A mis-sent Slack DM is not undoable |
| Ambiguous confirmation | **Cancels** | Fail closed. Re-dictating costs 5s; a wrong DM does not |
| Heavy work engine | **`claude -p` headless CLI** | Already installed (v2.1.217). Loads each repo's `CLAUDE.md` + 14 skills. Not the GUI app |
| Summarisation | **Haiku via API key** | Local `llama3.2:3b` noticeably worse for ~₹25/month saved |
| Intent classification model | **None** | Rules + Tier-3 catch-all. Cut from the design as unnecessary |
| Zendesk | **Wiki RAG + git watcher now**, direct API later | No credentials needed today; design accommodates the API later |
| Ollama | **Embeddings only** | Already serving `nomic-embed-text`; no chat model needed |

---

## 3. Architecture

Two kinds of process. Jarvis is the voice layer and knows nothing about MCSL. Each Domain Expert gets
a warm worker that knows nothing about audio.

```
┌─ jarvisd (own venv, always on) ──────────────────┐
│  mic → clap detector ─┐                          │
│        openWakeWord  ─┴→ faster-whisper small.en │
│                              ↓                    │
│                        correction layer           │
│                     (snap jargon to real IDs)     │
│                              ↓                    │
│                          router ──────┐           │
│                              ↓         ↓          │
│                          TTS ← ─── memory (SQLite)│
│                                        ↑          │
│                                   scheduler       │
└────────────────┬─────────────────────────────────┘
                 │ unix socket, JSON-RPC
     ┌───────────┼───────────┐
     ▼           ▼           ▼
 mcsl-worker  fedex-worker  aupost-worker
 (own venv)   (own venv)    (own venv)
     │
     ├─ Tier 1: import pipeline.* directly     → <1s, free
     ├─ Tier 2: Haiku summarisation            → ~$0.0001, only for unbounded text
     └─ Tier 3: subprocess `claude -p` in repo → minutes, subscription auth
```

### Why workers exist

All three repos have a top-level `config.py` and a `pipeline/` package. **Two of them cannot be
imported into one Python process** — `sys.modules["config"]` collides and `import pipeline` resolves
to whichever repo won the `sys.path` race. Each repo also has its own venv and `.env`.

One warm worker per domain solves imports, venvs, `.env`, and config collision at once. Because it
stays warm, Tier 1 stays sub-second after first start; a subprocess-per-call would cost 300–400ms
every time.

Workers are **lazy and disposable**: only the sticky domain's worker runs, old ones idle out after
10 minutes without a call, a dead worker is restarted once and the call retried.

### The three router outcomes

1. **Tier-1 intent matched** → RPC to worker → speak. Sub-second.
2. **Write intent matched** → build action, speak read-back, await "ok", then RPC.
3. **No match** → hand the raw sentence to Tier 3 in the sticky domain.

Outcome 3 is load-bearing: **Phase A does not need a complete intent list.** Ship eight intents;
everything else still works, just slower. Intents become an optimisation, not a prerequisite.

### Capability probing

Each worker reports its capabilities on startup (`has notify_toggle_enablement`, `has send_dm_to_user`, …).
The router only offers Tier-1 intents that domain actually supports; unsupported ones fall silently to
Tier 3. This is how the MCSL/FedEx/AU Post drift stops being a problem.

---

## 4. Components

```
jarvis/
  daemon.py        # entry point — the always-on process
  domains.yaml     # registry: name, path, aliases, venv, card prefix
  ears/            # clap detector, openWakeWord, faster-whisper STT
  voice/           # TTS (Piper or macOS `say`)
  correct/         # jargon snapping
  router/          # intent registry + tier dispatch + confirm state machine
  memory/          # SQLite
  watch/           # scheduler jobs
  workers/         # RPC client
worker/            # the per-domain worker, run inside each repo's venv
tests/
```

### 4.1 Phase A intent set

**Reads — answer immediately, no confirm**

| Utterance | Action |
|---|---|
| "status of MCSL-384" | `trello_client.get_card` → list, assignee, last comment |
| "what are my tasks" | SQLite + Trello cards assigned to user |
| "who built the toggle flow" / "what's the dev status" | RAG over wiki + codebase |
| "any customer issues" | RAG over `Zendesk Support Summaries` → open ZI-IDs by feature area |
| "what changed" | wiki git diff since last check |

**Writes — read-back, await "ok"**

| Utterance | Action |
|---|---|
| "DM Ashok saying the toggle is still off" | `slack_client.send_dm` |
| "remind me to check ZI-653 orders at 4" | SQLite + scheduler |
| "note that GLS store needs re-toggling" | SQLite |

**Everything else** → Tier 3. *"Create a GLS carrier store and give me full env"* needs no intent —
`claude -p` loads `mcsl-shopify-store-actions` and runs the existing 8-phase pipeline.

### 4.2 Correction layer

The component that decides whether this feels magic or broken. Two deterministic stages:

**Stage 1 — bias the decode.** `faster-whisper` accepts an `initial_prompt`. Seed it with domain
vocabulary ("MCSL, ZI, Trello, Ashok, FedEx, GLS, rate shopping, toggle, QA Ready") so it mis-hears
less before any correction runs.

**Stage 2 — snap to real entities.** Normalise spoken numbers ("three eighty four" → `384`), then
fuzzy-match tokens against a live vocabulary snapshot refreshed periodically by the worker:

- card IDs from the current release list
- Slack usernames from `search_users`
- carrier codes from the carrier registries
- **ZI-IDs parsed from wiki frontmatter** — `new_ids_assigned: "ZI-691 → ZI-695"` yields exact valid ranges
- toggle keys from `detect_toggle_details`

Match confidence uses normalised edit distance against the vocabulary snapshot. **≥0.85 snaps
silently; 0.60–0.85 asks ("did you mean MCSL-384?"); below 0.60 is treated as no match** and the
sentence falls to Tier 3. These thresholds are starting values to be tuned against `command_log`
during slice 1, not fixed constants.

### 4.3 Memory

Four SQLite tables, no ORM:

| Table | Columns |
|---|---|
| `tasks` | id, domain, text, due_at, status, ref — *reminders are tasks with a `due_at`* |
| `notes` | id, domain, text, created_at |
| `vocab` | domain, entity_type, value, refreshed_at |
| `command_log` | raw transcript, corrected text, intent, tier, outcome, ts |

`command_log` earns its place twice over: it seeds correction test cases from real mis-hears, and
after a week it shows which commands fall through to Tier 3 most often — so **the intent list grows
from evidence rather than guesswork.**

### 4.4 Watchers (Phase A ships the scheduler; jobs land in slice 4)

| Job | Source | Cost |
|---|---|---|
| Reminders due | SQLite | free |
| New Zendesk issues | `mcsl-wiki` git — new commits in `wiki/zendesk/` | free |
| Trello changes | poll release list, diff against last snapshot | free |
| Slack replies | existing `check_toggle_reply` | free |

Zendesk today is a **pull-based archive, not a live feed**: 22 dated intake files in
`~/Documents/mcsl-wiki/wiki/zendesk/`, updated by batch commits per release intake, already ingested
into RAG as category `Zendesk Support Summaries`, with per-ticket detail in `zendesk/summaries/<n>.md`.
No API credentials exist. Watching the git repo covers everything except "a ticket arrived ten minutes
ago", which the release-batch workflow does not need. Direct API can be added later as a second source
without changing the design.

---

## 5. Data flow

### Read — "hey jarvis, status of MCSL three eighty four"

```
rolling 3s audio buffer (always on, never leaves the machine)
  → wake fires (clap OR "hey jarvis")
  → capture until 800ms silence, 15s hard cap
  → faster-whisper small.en + domain initial_prompt
      "status of MCSL three eighty four"
  → correction: numbers → 384; snap → MCSL-384 (conf 0.94)
  → router: intent=card_status, domain=mcsl (sticky)
  → RPC → mcsl-worker → trello_client.get_card
  → template → "MCSL-384 is in QA Ready, Ashok, last comment 2 hours ago"
  → TTS + macOS notification carrying the same text
  → command_log row
```

The notification is not decorative: if the speech is misheard, the text remains on screen, removing
the "sorry, what?" loop.

### Write — "DM Ashok saying rate shopping toggle is still off"

```
  … same through correction …
  → router: intent=send_dm, resolved user = Ashok Kumar (U01ABC)
  → BUILDS the action. Does not send.
  → TTS: "Sending to Ashok Kumar: 'rate shopping toggle is still off'. Ok?"
  → mic reopens automatically — no wake word needed
  → 30s timeout
```

**Anything that is not a clear yes is a no.** "ok", "send", "yes", "go" execute. Silence, noise, "uh",
or anything unparsed **cancels**. Ambiguity is never consent.

Phase A has **no voice-editing of a pending action** ("no, make it the GLS store"). That is a state
machine with real complexity for modest gain. Cancel and re-dictate.

### Tier 3 — "create a GLS carrier store and give me full env"

```
  → no intent match
  → TTS: "running that in Claude Code, few minutes"
  → spawn `claude -p` in ~/Documents/MCSLDomainExpert, detached
  → mic stays live — Jarvis is NOT blocked
  → on completion: Haiku one-line summary → TTS + notification
```

Tier 3 output is **never read aloud verbatim** — a store-creation run is minutes of speech. Summary
only; full text in the notification and log. The long task must never block the mic, or Jarvis is
useless during the ten minutes it matters most.

---

## 6. Error handling

| Failure | Response |
|---|---|
| Worker died | restart once, retry, then "mcsl worker isn't responding" |
| Empty/garbage transcript | "didn't catch that" — **never** fall through to Tier 3 on noise |
| Entity below confidence | ask "did you mean MCSL-384?" — do not guess |
| Tier 3 exceeds 10 min | kill, notify with partial output |
| Trello/Slack API error | speak the actual error, do not swallow |
| No network | serve from vocab cache where possible, else say so |

Plus a global **"stop"** that kills any in-flight action and closes the mic, from any state.

---

## 7. Testing

The design rule that makes voice testable:

```python
handle_transcript(text: str, domain: str) -> Response
```

Everything above that line (mic, clap, wake, STT) and below it (TTS) is swappable. **The entire
product is testable without a microphone.**

| Test | Scope |
|---|---|
| Correction, table-driven | seeded from real mis-hears in `command_log`; each new mangle becomes a case |
| Router intent matching | table-driven, including near-misses that *should* fall to Tier 3 |
| Templates | fixed-shape data → expected spoken string |
| Worker RPC | fake worker over the socket, assert message shape |
| **Contract test per domain** | spin each real worker, assert capability probe answers and one read intent works — **this catches MCSL/FedEx/AU Post drift** |
| Confirm state machine | yes / no / ambiguous / timeout, with "ambiguous cancels" asserted explicitly |
| Audio | a handful of real WAVs; slow, run separately; the only thing catching STT regressions |

---

## 8. Cost

| Tier | Engine | Billing |
|---|---|---|
| Wake, STT, TTS | openWakeWord, faster-whisper, Piper — all local | free |
| Tier 1 | direct `pipeline/` calls + f-string templates | free |
| Tier 2 | Haiku, only for unbounded text | ~$0.0001/call |
| Tier 3 | `claude -p` | existing Claude Code subscription, **not metered API tokens** |
| Memory, scheduler, watchers | SQLite, APScheduler, git polling | free |

**Idle cost: zero.** For contrast, `sambuild04/screen-voice-agent` uses the OpenAI Realtime API at
~$0.006/min just to listen — roughly $250/month left running. That cost profile is the reason its
architecture was not copied.

---

## 9. Build order

### Slices within Phase A

1. **Listen + do** — clap/wake → STT → route → speak. Read-only intents. Establishes the ears and
   mouth that everything else reuses.
2. **Work for me** — write actions with the confirm gate.
3. **Remember** — SQLite tasks and notes, surviving restart.
4. **Suggest** — the watcher loop: reminders, wiki git, Trello diff, Slack replies.

### Phases across domains

| Phase | What | Why this order |
|---|---|---|
| **A** | Full Jarvis against **MCSL only** — with the domain seam built in from day one, never retrofitted | MCSL is the most developed repo (toggles, carrier registries, onboarding). A seam that survives here survives anywhere |
| **B** | Add **FedEx** as domain #2 | The real test of the seam. Expect 2–4 wrong assumptions. Finding them at #2 is cheap; at #3 it is not |
| **C** | Add **AU Post** | Nearly free if B went well. If it is not, B taught the wrong lesson |

Phase A holds all the work; B and C are mostly config plus fixing what A assumed.

### Domain selection

- **Explicit** — "Jarvis, FedEx — status of that card"
- **Sticky** — "switch to FedEx", everything routes there until switched back
- **Inferred from card prefix** — nice-to-have; goes wrong in ways that are annoying to debug. Not Phase A.

---

## 10. Reference projects

| Repo | Verdict |
|---|---|
| `hectorg2211/jarvis` | Worth ~100 lines: double-clap detection via spike ratio, cooldown, auto-select loudest mic. No LLM, no STT. Its ElevenLabs TTS is a paid dependency that is not needed |
| `sambuild04/screen-voice-agent` | Architecturally instructive — the reactive-loop / watcher-loop split maps onto "work for me" vs "suggest me". Cost profile is wrong (Electron + OpenAI Realtime, ~$0.006/min idle). Take the pattern, not the stack |

---

## 11. Out of scope

- Cross-domain queries in a single command
- Voice-editing of a pending action
- Screen capture or vision
- Direct Zendesk API (deferred; design accommodates it as an additional watcher source)
- Any change to the four-tab dashboard split mandated by `CLAUDE.md`
- Any change to `pipeline/` — the dependency is strictly one-way

---

## 12. Open items

- **Trello card ID prefixes for FedEx and AU Post** are unknown. Needed only for prefix inference,
  which is out of Phase A scope. Confirm during Phase B.
- **Piper vs macOS `say`** for TTS — decide by ear during slice 1. Both are free and local; the seam
  makes swapping trivial.

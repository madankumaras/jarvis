# Jarvis Slice 4 Implementation Plan — Watchers

**Goal:** Jarvis tells you things without being asked. A reminder fires at 10:00. A new Zendesk intake lands and it says which ZI-IDs appeared. Ashok replies and it tells you.

**Architecture:** A scheduler on a background thread runs a handful of jobs on intervals. Each job is a pure-ish function returning announcements; the scheduler speaks them. All dedup and last-check state lives in SQLite, so a restart does not re-announce yesterday's news.

## Global Constraints

- **Never interrupt a live turn.** If a wake is being handled, announcements queue and are spoken after.
- **Never announce the same thing twice**, across restarts. Dedup keys live in SQLite.
- **A failing job must never take down the daemon or the other jobs.**
- **Quiet by default:** nothing is announced between 22:00 and 08:00; it is held.
- No new API credentials. Zendesk comes via the wiki git repo, as designed.
- `jarvis/` still must not import `pipeline`, `config`, or `rag`. Worker calls only.
- No Domain Expert repo is modified.

## File Structure

| Path | Responsibility |
|---|---|
| `jarvis/memory/store.py` | add `seen` and `watch_state` tables (modify) |
| `jarvis/watch/scheduler.py` | interval scheduler with a testable `tick(now)` |
| `jarvis/watch/jobs.py` | the four watchers |
| `jarvis/daemon.py` | own the scheduler, gate announcements on turn state (modify) |
| `worker/handlers.py` | add `zendesk_since` and `release_card_ids` (modify) |

---

### Task 1 — dedup and last-check state

`seen(kind, key, ts)` with a unique index on `(kind, key)`; `watch_state(key, value)`.

Without these, every restart replays the last week of Zendesk intakes at you.

### Task 2 — scheduler

`Scheduler.add(name, interval, fn)`, `tick(now)` returning announcements, `start()/stop()`
for the thread. `tick` is the seam: the whole scheduler is testable with no threads and
no sleeping.

Jobs that raise are logged and skipped — one broken watcher must not silence the rest.

### Task 3 — the four jobs

| Job | Interval | Source |
|---|---|---|
| due reminders | 60s | `store.due_tasks()`, then mark done |
| new Zendesk issues | 15min | worker `zendesk_since` — newest wiki intake file |
| Trello movement | 10min | worker `release_card_ids`, diffed against `watch_state` |
| Slack replies | 5min | worker `read_replies` for anyone DM'd recently |

### Task 4 — wire into the daemon

The daemon holds a `busy` flag around a turn. Announcements arriving while busy are
queued and drained afterwards.

## Out of scope

Barge-in, per-job quiet-hour overrides, watching the other two domains concurrently
(sticky domain only, as designed).

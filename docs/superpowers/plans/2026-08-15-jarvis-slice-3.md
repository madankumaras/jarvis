# Jarvis Slice 3 Implementation Plan — Memory

**Goal:** "remind me to check ZI-653 orders at 4" survives a restart and fires at 4. "note that the GLS store needs re-toggling" is remembered and recallable. "what are my tasks" merges what you told Jarvis with what Trello says.

**Architecture:** One SQLite file owned by the daemon. Four tables. A pure time parser kept separate from storage so it can be tested exhaustively without a database.

## Global Constraints

- SQLite lives at `~/.jarvis/jarvis.db`, created on first use. Never inside a Domain Expert repo.
- Reminders are **tasks with a `due_at`** — not a separate concept.
- All timestamps stored UTC ISO-8601; all parsing and display in local time.
- The time parser is pure: `parse_when(text, now)` — `now` is always injected, never read from the clock inside. Otherwise the tests are unrepeatable.
- `jarvis/` still must not import `pipeline`, `config`, or `rag`.
- No Domain Expert repo is modified.

## File Structure

| Path | Responsibility |
|---|---|
| `jarvis/memory/when.py` | spoken time → datetime. Pure |
| `jarvis/memory/store.py` | SQLite: tasks, notes, command_log |
| `jarvis/router/intents.py` | add `remind_me`, `add_note`, `list_notes` (modify) |
| `jarvis/router/core.py` | dispatch memory intents locally, not to the worker (modify) |
| `jarvis/daemon.py` | own the Store, log every command (modify) |

---

### Task 1 — `when.py`, spoken time parsing

Cases that must work, chosen from how the request was actually phrased:

| Spoken | Meaning |
|---|---|
| "at 4" | today 16:00 if still ahead, else tomorrow 16:00 |
| "at 4pm" / "at 4 pm" | same |
| "at 9am" | today 09:00 or tomorrow |
| "in 20 minutes" | now + 20m |
| "in 2 hours" | now + 2h |
| "tomorrow" | tomorrow 09:00 |
| "tomorrow morning" | tomorrow 09:00 |
| "tonight" | today 20:00 |
| "in an hour" | now + 1h |
| (none present) | `None` — a task with no due time |

**"at 4" resolves to 16:00, not 04:00.** A QA engineer saying "remind me at 4" means the afternoon. 4am reminders would be a bug, not a feature.

### Task 2 — `store.py`, SQLite

```sql
tasks(id, domain, text, created_at, due_at, status, ref)
notes(id, domain, text, created_at)
command_log(id, ts, raw, corrected, intent, tier, ok)
```

`command_log` earns its place twice: it seeds correction test cases from real mis-hears, and after a week it shows which commands fall through to tier 3 most often — so the intent list grows from evidence rather than guesswork.

### Task 3 — memory intents

- `remind me to X at Y` → task with due_at
- `note that X` / `remember that X` → note
- `what are my notes` → recent notes
- `what are my tasks` → merge SQLite tasks with the worker's Trello answer

### Task 4 — wire into the daemon

Own a `Store`, log every command, merge local tasks into `my_tasks`.

## Out of scope

The scheduler that *fires* reminders — that is slice 4, along with the other watchers. Slice 3 stores them and can list what is due.

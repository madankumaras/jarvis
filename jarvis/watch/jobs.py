"""The four watchers.

Each returns a list of things to say, or an empty list. Each is responsible for
its own deduplication via the store, so the scheduler can call them freely and
a restart never replays yesterday's news.

Every job swallows its own errors and returns nothing rather than raising. A
watcher that cannot reach Trello should be quiet, not fatal.
"""
from __future__ import annotations

import traceback
from datetime import datetime

MAX_LISTED = 3


def due_reminders_job(store) -> list[str]:
    """Announce reminders whose time has come, then close them."""
    try:
        due = store.due_tasks(now=datetime.now())
    except Exception:
        traceback.print_exc()
        return []

    said: list[str] = []
    for task in due:
        said.append(f"Reminder: {task.text}")
        try:
            store.complete_task(task.id)
        except Exception:
            traceback.print_exc()
    return said


def zendesk_job(worker, store) -> list[str]:
    """Announce ZI ids appearing in the newest wiki intake that we have not
    mentioned before."""
    try:
        latest = worker.call("zendesk_latest")
    except Exception:
        return []

    ids = latest.get("ids") or []
    fresh = store.unseen("zendesk", ids)
    if not fresh:
        return []

    titles = latest.get("titles") or {}
    for zi in fresh:
        store.mark_seen("zendesk", zi)

    head = "; ".join(f"{zi}: {titles.get(zi, '')[:60]}" for zi in fresh[:MAX_LISTED])
    extra = f" and {len(fresh) - MAX_LISTED} more" if len(fresh) > MAX_LISTED else ""
    label = "issue" if len(fresh) == 1 else "issues"
    return [f"{len(fresh)} new customer {label} in {latest.get('file', 'the latest intake')}. {head}{extra}"]


def trello_movement_job(worker, store, domain: str) -> list[str]:
    """Announce cards that appeared in the current release since last check.

    The first run records a baseline and says nothing: announcing every card on
    startup would be noise, not news.
    """
    key = f"trello:{domain}"
    try:
        current = worker.call("release_card_ids")
    except Exception:
        return []

    ids = current.get("ids") or []
    previous_raw = store.get_state(key)
    store.set_state(key, ",".join(ids))

    if not previous_raw:
        return []

    previous = set(filter(None, previous_raw.split(",")))
    added = [i for i in ids if i not in previous]
    if not added:
        return []

    label = "card" if len(added) == 1 else "cards"
    return [
        f"{len(added)} new {label} in {current.get('release', 'the current release')}: "
        + ", ".join(added[:MAX_LISTED])
    ]


def slack_replies_job(worker, store) -> list[str]:
    """Poll for replies from anyone we have DM'd.

    `dm_sent` is written when a DM goes out, so this polls only conversations
    Jarvis actually started.
    """
    try:
        people = store.dm_recipients()
    except Exception:
        traceback.print_exc()
        return []

    said: list[str] = []
    for person in people:
        try:
            out = worker.call("read_replies", person=person)
        except Exception:
            continue

        speech = (out or {}).get("speech", "")
        if not speech or "no reply" in speech.lower():
            continue

        # Dedup on the reply text itself: the same answer must not be
        # re-announced every five minutes.
        key = f"{person}:{(out.get('detail') or speech)[:120]}"
        if store.has_seen("dm_reply", key):
            continue
        store.mark_seen("dm_reply", key)
        said.append(speech)

    return said

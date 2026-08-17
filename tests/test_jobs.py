from datetime import datetime, timedelta

import pytest

from jarvis.memory.store import Store
from jarvis.watch.jobs import (
    due_reminders_job,
    slack_replies_job,
    trello_movement_job,
    zendesk_job,
)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "w.db"))


class FakeWorker:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method not in self.results:
            raise KeyError(method)
        return self.results[method]

    def capabilities(self):
        return list(self.results)


# ---- due reminders ---------------------------------------------------

def test_a_due_reminder_is_announced_and_closed(store):
    store.add_task("check the GLS store", domain="mcsl",
                   due_at=datetime.now() - timedelta(minutes=1))
    said = due_reminders_job(store)
    assert len(said) == 1
    assert "check the GLS store" in said[0]
    assert store.list_tasks() == [], "an announced reminder must be closed"


def test_a_future_reminder_is_not_announced(store):
    store.add_task("later", domain="mcsl", due_at=datetime.now() + timedelta(hours=2))
    assert due_reminders_job(store) == []
    assert len(store.list_tasks()) == 1


def test_a_reminder_is_never_announced_twice(store):
    store.add_task("once", domain="mcsl", due_at=datetime.now() - timedelta(minutes=1))
    assert due_reminders_job(store) != []
    assert due_reminders_job(store) == []


def test_a_task_with_no_due_date_is_never_announced(store):
    store.add_task("someday", domain="mcsl")
    assert due_reminders_job(store) == []


# ---- zendesk ---------------------------------------------------------

ZENDESK = {
    "zendesk_latest": {
        "file": "2026-08-11",
        "ids": ["ZI-691", "ZI-692"],
        "titles": {"ZI-691": "cutoff time not applied", "ZI-692": "HS code wrong"},
    }
}


def test_new_zendesk_issues_are_announced(store):
    said = zendesk_job(FakeWorker(ZENDESK), store)
    assert len(said) == 1
    assert "ZI-691" in said[0] and "ZI-692" in said[0]
    assert "cutoff time" in said[0]


def test_the_same_zendesk_issues_are_not_announced_twice(store):
    w = FakeWorker(ZENDESK)
    assert zendesk_job(w, store) != []
    assert zendesk_job(w, store) == []


def test_zendesk_dedup_survives_a_restart(tmp_path):
    path = str(tmp_path / "z.db")
    assert zendesk_job(FakeWorker(ZENDESK), Store(path)) != []
    assert zendesk_job(FakeWorker(ZENDESK), Store(path)) == []


def test_only_the_genuinely_new_zendesk_ids_are_announced(store):
    store.mark_seen("zendesk", "ZI-691")
    said = zendesk_job(FakeWorker(ZENDESK), store)
    assert "ZI-692" in said[0]
    assert "ZI-691" not in said[0]


def test_a_worker_failure_is_swallowed_by_the_job(store):
    assert zendesk_job(FakeWorker({}), store) == []


# ---- trello movement -------------------------------------------------

def test_the_first_trello_check_only_records_a_baseline(store):
    """Announcing every card on first run would be noise, not news."""
    w = FakeWorker({"release_card_ids": {"release": "SL MCSL 386", "ids": ["ZI-691", "ZI-692"]}})
    assert trello_movement_job(w, store, domain="mcsl") == []
    assert store.get_state("trello:mcsl") != ""


def test_a_card_added_since_the_last_check_is_announced(store):
    w = FakeWorker({"release_card_ids": {"release": "SL MCSL 386", "ids": ["ZI-691"]}})
    trello_movement_job(w, store, domain="mcsl")

    w.results["release_card_ids"] = {"release": "SL MCSL 386", "ids": ["ZI-691", "ZI-700"]}
    said = trello_movement_job(w, store, domain="mcsl")
    assert len(said) == 1
    assert "ZI-700" in said[0]
    assert "ZI-691" not in said[0]


def test_no_change_announces_nothing(store):
    w = FakeWorker({"release_card_ids": {"release": "SL MCSL 386", "ids": ["ZI-691"]}})
    trello_movement_job(w, store, domain="mcsl")
    assert trello_movement_job(w, store, domain="mcsl") == []


def test_a_removed_card_is_not_announced_as_new(store):
    w = FakeWorker({"release_card_ids": {"release": "R", "ids": ["ZI-1", "ZI-2"]}})
    trello_movement_job(w, store, domain="mcsl")
    w.results["release_card_ids"] = {"release": "R", "ids": ["ZI-1"]}
    assert trello_movement_job(w, store, domain="mcsl") == []


# ---- slack replies ---------------------------------------------------

def test_a_reply_from_someone_dmd_is_announced(store):
    store.mark_seen("dm_sent", "Ashok Kumar")
    w = FakeWorker({"read_replies": {"speech": "Ashok Kumar said: done", "detail": "done"}})
    said = slack_replies_job(w, store)
    assert len(said) == 1
    assert "done" in said[0]


def test_nobody_dmd_means_nothing_to_poll(store):
    w = FakeWorker({"read_replies": {"speech": "x", "detail": "x"}})
    assert slack_replies_job(w, store) == []
    assert w.calls == []


def test_the_same_reply_is_not_announced_twice(store):
    store.mark_seen("dm_sent", "Ashok Kumar")
    w = FakeWorker({"read_replies": {"speech": "Ashok Kumar said: done", "detail": "done"}})
    assert slack_replies_job(w, store) != []
    assert slack_replies_job(w, store) == []


def test_no_reply_yet_announces_nothing(store):
    store.mark_seen("dm_sent", "Ashok Kumar")
    w = FakeWorker({"read_replies": {"speech": "No reply from Ashok Kumar yet.", "detail": ""}})
    assert slack_replies_job(w, store) == []

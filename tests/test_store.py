from datetime import datetime, timedelta

import pytest

from jarvis.memory.store import Store

NOW = datetime(2026, 8, 15, 10, 30)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def test_add_and_list_a_task(store):
    store.add_task("check ZI-653 orders", domain="mcsl")
    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].text == "check ZI-653 orders"
    assert tasks[0].domain == "mcsl"
    assert tasks[0].due_at is None
    assert tasks[0].status == "open"


def test_a_task_survives_reopening_the_database(tmp_path):
    """The whole point of slice 3: memory outlives the process."""
    path = str(tmp_path / "t.db")
    Store(path).add_task("remember this", domain="mcsl")
    assert Store(path).list_tasks()[0].text == "remember this"


def test_tasks_are_scoped_by_domain(store):
    store.add_task("mcsl thing", domain="mcsl")
    store.add_task("fedex thing", domain="fedex")
    assert len(store.list_tasks(domain="mcsl")) == 1
    assert store.list_tasks(domain="mcsl")[0].text == "mcsl thing"
    assert len(store.list_tasks()) == 2


def test_completing_a_task_hides_it(store):
    tid = store.add_task("do it", domain="mcsl")
    store.complete_task(tid)
    assert store.list_tasks() == []
    assert len(store.list_tasks(include_done=True)) == 1


def test_completing_an_unknown_id_is_harmless(store):
    store.complete_task(999)
    assert store.list_tasks() == []


def test_due_tasks_returns_only_what_is_ripe(store):
    store.add_task("soon", domain="mcsl", due_at=NOW + timedelta(minutes=5))
    store.add_task("later", domain="mcsl", due_at=NOW + timedelta(hours=5))
    store.add_task("no due date", domain="mcsl")

    due = store.due_tasks(now=NOW + timedelta(minutes=10))
    assert [t.text for t in due] == ["soon"]


def test_due_tasks_ignores_completed_ones(store):
    tid = store.add_task("soon", domain="mcsl", due_at=NOW)
    store.complete_task(tid)
    assert store.due_tasks(now=NOW + timedelta(hours=1)) == []


def test_due_at_round_trips_without_drifting(store):
    when = NOW + timedelta(hours=3)
    store.add_task("x", domain="mcsl", due_at=when)
    assert store.list_tasks()[0].due_at == when


def test_notes_are_stored_newest_first(store):
    store.add_note("first", domain="mcsl")
    store.add_note("second", domain="mcsl")
    assert [n.text for n in store.list_notes()] == ["second", "first"]


def test_notes_are_scoped_by_domain(store):
    store.add_note("a", domain="mcsl")
    store.add_note("b", domain="fedex")
    assert [n.text for n in store.list_notes(domain="fedex")] == ["b"]


def test_command_log_records_what_was_heard_and_what_happened(store):
    store.log_command(raw="status of muscle 384", corrected="status of MCSL-384",
                      intent="card_status", tier=1, ok=True)
    rows = store.recent_commands()
    assert rows[0].raw == "status of muscle 384"
    assert rows[0].corrected == "status of MCSL-384"
    assert rows[0].intent == "card_status"


def test_command_log_is_the_evidence_for_which_intents_to_add(store):
    """After a week, the commands that fell through to tier 3 most often are
    the ones worth promoting to a fast local intent."""
    for _ in range(3):
        store.log_command(raw="create a store", corrected="create a store",
                          intent="", tier=3, ok=True)
    store.log_command(raw="status of ZI-691", corrected="status of ZI-691",
                      intent="card_status", tier=1, ok=True)

    assert store.tier3_counts() == [("create a store", 3)]


def test_the_database_directory_is_created_on_demand(tmp_path):
    nested = tmp_path / "deep" / "deeper" / "t.db"
    Store(str(nested)).add_task("x", domain="mcsl")
    assert nested.exists()


# --- watcher bookkeeping: without this, a restart replays last week at you ---

def test_seen_is_remembered(store):
    assert store.has_seen("zendesk", "ZI-691") is False
    store.mark_seen("zendesk", "ZI-691")
    assert store.has_seen("zendesk", "ZI-691") is True


def test_marking_seen_twice_is_harmless(store):
    store.mark_seen("zendesk", "ZI-691")
    store.mark_seen("zendesk", "ZI-691")
    assert store.has_seen("zendesk", "ZI-691") is True


def test_seen_is_scoped_by_kind(store):
    store.mark_seen("zendesk", "X")
    assert store.has_seen("trello", "X") is False


def test_unseen_filters_and_preserves_order(store):
    store.mark_seen("zendesk", "ZI-692")
    assert store.unseen("zendesk", ["ZI-691", "ZI-692", "ZI-693"]) == ["ZI-691", "ZI-693"]


def test_seen_survives_reopening(tmp_path):
    path = str(tmp_path / "s.db")
    Store(path).mark_seen("zendesk", "ZI-691")
    assert Store(path).has_seen("zendesk", "ZI-691") is True


def test_watch_state_round_trips(store):
    assert store.get_state("trello:mcsl", "none") == "none"
    store.set_state("trello:mcsl", "ZI-1,ZI-2")
    assert store.get_state("trello:mcsl") == "ZI-1,ZI-2"


def test_watch_state_overwrites(store):
    store.set_state("k", "a")
    store.set_state("k", "b")
    assert store.get_state("k") == "b"

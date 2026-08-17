from datetime import datetime

import pytest

from jarvis.watch.scheduler import QUIET_END_HOUR, QUIET_START_HOUR, Scheduler

MIDDAY = datetime(2026, 8, 17, 12, 0, 0)


def test_a_job_runs_on_its_first_tick():
    s = Scheduler()
    s.add("a", 60, lambda: ["hello"])
    assert s.tick(MIDDAY) == ["hello"]


def test_a_job_does_not_run_again_before_its_interval():
    s = Scheduler()
    s.add("a", 60, lambda: ["hello"])
    s.tick(MIDDAY)
    assert s.tick(MIDDAY.replace(second=30)) == []


def test_a_job_runs_again_after_its_interval():
    s = Scheduler()
    s.add("a", 60, lambda: ["hello"])
    s.tick(MIDDAY)
    assert s.tick(MIDDAY.replace(minute=1)) == ["hello"]


def test_jobs_have_independent_schedules():
    s = Scheduler()
    s.add("fast", 60, lambda: ["fast"])
    s.add("slow", 600, lambda: ["slow"])
    assert set(s.tick(MIDDAY)) == {"fast", "slow"}
    assert s.tick(MIDDAY.replace(minute=2)) == ["fast"]


def test_a_job_returning_nothing_announces_nothing():
    s = Scheduler()
    s.add("a", 60, lambda: [])
    assert s.tick(MIDDAY) == []


def test_a_failing_job_does_not_silence_the_others():
    """One broken watcher must not take the rest down with it."""
    s = Scheduler()

    def broken():
        raise RuntimeError("trello is down")

    s.add("broken", 60, broken)
    s.add("working", 60, lambda: ["still here"])
    assert s.tick(MIDDAY) == ["still here"]


def test_a_failing_job_is_retried_on_the_next_interval():
    s = Scheduler()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return ["recovered"]

    s.add("flaky", 60, flaky)
    assert s.tick(MIDDAY) == []
    assert s.tick(MIDDAY.replace(minute=1)) == ["recovered"]


@pytest.mark.parametrize("hour", [22, 23, 0, 3, 7])
def test_nothing_is_announced_during_quiet_hours(hour):
    s = Scheduler()
    s.add("a", 60, lambda: ["wake up"])
    assert s.tick(datetime(2026, 8, 17, hour, 30)) == []


@pytest.mark.parametrize("hour", [8, 12, 21])
def test_announcements_flow_outside_quiet_hours(hour):
    s = Scheduler()
    s.add("a", 60, lambda: ["hello"])
    assert s.tick(datetime(2026, 8, 17, hour, 30)) == ["hello"]


def test_quiet_hours_hold_rather_than_discard():
    """A reminder that came due at 3am should still be delivered at 8am, not
    silently dropped.

    The job reports once and then goes quiet, which is how the real watchers
    behave -- they dedup against the `seen` table rather than re-announcing.
    """
    s = Scheduler()
    emitted = []

    def once():
        if emitted:
            return []
        emitted.append(1)
        return ["overnight news"]

    s.add("a", 3600, once)
    assert s.tick(datetime(2026, 8, 17, 3, 0)) == []
    assert s.tick(datetime(2026, 8, 17, 8, 5)) == ["overnight news"]


def test_quiet_hour_boundaries_are_what_the_constants_say():
    assert QUIET_START_HOUR == 22
    assert QUIET_END_HOUR == 8


def test_pending_announcements_can_be_drained_manually():
    """The daemon drains these after a turn finishes, so a watcher never
    interrupts a conversation."""
    s = Scheduler()
    s.add("a", 60, lambda: ["news"])
    s.tick(datetime(2026, 8, 17, 3, 0))       # held by quiet hours
    assert s.drain() == ["news"]
    assert s.drain() == []

"""Interval scheduler for the watchers.

`tick(now)` is the seam: the whole scheduler is testable with no threads and no
sleeping. `start()` is a thin loop around it.
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

# Nothing is spoken between these hours. Announcements are HELD, not dropped —
# a reminder that came due at 3am should still arrive at 8am.
QUIET_START_HOUR = 22
QUIET_END_HOUR = 8

LOOP_SLEEP_SECONDS = 5.0


@dataclass
class Job:
    name: str
    interval: float
    fn: Callable[[], list[str]]
    last_run: datetime | None = None


def in_quiet_hours(now: datetime) -> bool:
    return now.hour >= QUIET_START_HOUR or now.hour < QUIET_END_HOUR


@dataclass
class Scheduler:
    jobs: list[Job] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def add(self, name: str, interval: float, fn: Callable[[], list[str]]) -> None:
        self.jobs.append(Job(name=name, interval=interval, fn=fn))

    def tick(self, now: datetime | None = None) -> list[str]:
        """Run every job whose interval has elapsed. Returns what to say now.

        A job that raises is logged and left alone — its schedule is not
        advanced, so it retries on the next interval rather than being
        permanently poisoned. One broken watcher must not silence the rest.
        """
        now = now or datetime.now()
        fresh: list[str] = []

        for job in self.jobs:
            if job.last_run is not None and (now - job.last_run).total_seconds() < job.interval:
                continue
            try:
                out = job.fn() or []
            except Exception:
                traceback.print_exc()
                continue
            job.last_run = now
            fresh.extend(out)

        self.pending.extend(fresh)

        if in_quiet_hours(now):
            return []
        return self.drain()

    def drain(self) -> list[str]:
        """Take everything held so far. Used by the daemon after a turn ends,
        so a watcher never interrupts a conversation."""
        out, self.pending = self.pending, []
        return out

    # ---- thread plumbing ---------------------------------------------

    def start(self, on_announce: Callable[[list[str]], None]) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    said = self.tick()
                    if said:
                        on_announce(said)
                except Exception:
                    traceback.print_exc()
                self._stop.wait(LOOP_SLEEP_SECONDS)

        self._stop.clear()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=LOOP_SLEEP_SECONDS + 1)
            self._thread = None

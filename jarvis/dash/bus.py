"""Event bus between the daemon and any connected dashboards.

Server-Sent Events over stdlib HTTP, not WebSockets: the dashboard only ever
displays, so a one-way stream needs no extra dependency and no handshake.

The bus must never block or break the voice loop. Publishing to a full or dead
subscriber queue drops the event rather than raising -- a stalled browser tab is
not a reason for Jarvis to stop listening.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field

# Deep enough to ride out a slow render, shallow enough that a dead tab cannot
# grow without bound. Amplitude arrives ~12x a second.
QUEUE_MAX = 256


@dataclass
class Event:
    kind: str
    data: dict

    def encode(self) -> bytes:
        """One SSE frame."""
        payload = json.dumps({"kind": self.kind, **self.data}, default=str)
        return f"event: {self.kind}\ndata: {payload}\n\n".encode()


@dataclass
class Bus:
    _subs: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # The last event of each kind, replayed to a tab that connects late so it
    # does not sit blank until the next thing happens.
    _latest: dict[str, Event] = field(default_factory=dict)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            self._subs.append(q)
            replay = list(self._latest.values())
        for event in replay:
            try:
                q.put_nowait(event)
            except queue.Full:
                break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, kind: str, **data) -> None:
        """Fan out to every subscriber. Never raises, never blocks."""
        event = Event(kind=kind, data=data)
        with self._lock:
            if kind != "level":       # replaying stale amplitude is pointless
                self._latest[kind] = event
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass                  # a stalled tab loses frames, not the daemon


# One bus per process. The daemon publishes; the HTTP server subscribes.
BUS = Bus()


def heartbeat(bus: Bus, stop: threading.Event, interval: float = 15.0) -> None:
    """Keep proxies and browsers from closing an idle stream."""
    while not stop.is_set():
        bus.publish("ping", t=time.time())
        stop.wait(interval)

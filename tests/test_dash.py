import json
import queue
import threading
import time
import urllib.request

import pytest

from jarvis.dash.bus import QUEUE_MAX, Bus, Event
from jarvis.dash.server import Dashboard


# ---- bus -------------------------------------------------------------

def test_an_event_encodes_as_one_sse_frame():
    raw = Event("state", {"state": "listening"}).encode().decode()
    assert raw.startswith("event: state\ndata: ")
    assert raw.endswith("\n\n")
    payload = json.loads(raw.split("data: ", 1)[1])
    assert payload == {"kind": "state", "state": "listening"}


def test_a_subscriber_receives_published_events():
    b = Bus()
    q = b.subscribe()
    b.publish("state", state="speaking")
    assert q.get_nowait().data == {"state": "speaking"}


def test_every_subscriber_receives_the_event():
    b = Bus()
    a, c = b.subscribe(), b.subscribe()
    b.publish("turn", who="you", text="hi")
    assert a.get_nowait().kind == "turn"
    assert c.get_nowait().kind == "turn"


def test_a_late_subscriber_gets_the_last_state_replayed():
    """A tab opened mid-session should not sit blank until something happens."""
    b = Bus()
    b.publish("state", state="listening")
    b.publish("context", release="MCSL 385")
    q = b.subscribe()
    kinds = {q.get_nowait().kind for _ in range(2)}
    assert kinds == {"state", "context"}


def test_amplitude_is_not_replayed():
    """Replaying a stale mic level would show a frozen ring."""
    b = Bus()
    b.publish("level", peak=0.5)
    q = b.subscribe()
    with pytest.raises(queue.Empty):
        q.get_nowait()


def test_a_full_subscriber_queue_drops_frames_rather_than_raising():
    """A stalled browser tab is not a reason for the voice loop to fail."""
    b = Bus()
    q = b.subscribe()
    for i in range(QUEUE_MAX + 50):
        b.publish("level", peak=i)          # must not raise
    assert q.qsize() <= QUEUE_MAX


def test_publishing_with_no_subscribers_is_harmless():
    Bus().publish("state", state="idle")


def test_unsubscribing_stops_delivery():
    b = Bus()
    q = b.subscribe()
    b.unsubscribe(q)
    b.publish("state", state="idle")
    with pytest.raises(queue.Empty):
        q.get_nowait()
    assert b.subscribers == 0


def test_unsubscribing_twice_is_harmless():
    b = Bus()
    q = b.subscribe()
    b.unsubscribe(q)
    b.unsubscribe(q)


def test_publish_is_threadsafe():
    b = Bus()
    q = b.subscribe()
    threads = [threading.Thread(target=lambda: [b.publish("t", i=i) for i in range(40)])
               for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert q.qsize() > 0


# ---- server ----------------------------------------------------------

@pytest.fixture
def dash():
    d = Dashboard(bus=Bus(), port=8911)
    assert d.start(), "could not bind test port"
    yield d
    d.stop()


def test_the_page_is_served(dash):
    with urllib.request.urlopen(dash.url, timeout=5) as r:
        body = r.read().decode()
    assert r.status == 200
    assert "EventSource" in body, "served page is not the live dashboard"


def test_an_unknown_path_is_404(dash):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(dash.url + "nope", timeout=5)
    assert e.value.code == 404


def test_the_event_stream_delivers_a_published_event(dash):
    got = []

    def read():
        # The heartbeat publishes a `ping` immediately, so skip past anything
        # that is not the event under test.
        with urllib.request.urlopen(dash.url + "events", timeout=8) as r:
            for _ in range(80):
                line = r.readline()
                if line.startswith(b"data: "):
                    payload = json.loads(line[6:])
                    if payload.get("kind") == "state":
                        got.append(payload)
                        return

    t = threading.Thread(target=read, daemon=True)
    t.start()
    for _ in range(50):
        if dash.bus.subscribers:
            break
        time.sleep(0.05)
    dash.bus.publish("state", state="listening", line="go ahead")
    t.join(6)
    assert got and got[0]["state"] == "listening"


def test_it_binds_only_to_localhost(dash):
    """The transcript of everything you say has no business on the network."""
    from jarvis.dash.server import HOST

    assert HOST in ("127.0.0.1", "localhost")


def test_a_taken_port_is_reported_not_fatal(dash):
    """A dashboard that cannot start must not stop Jarvis from listening."""
    second = Dashboard(bus=Bus(), port=dash.port)
    assert second.start() is False


def test_the_browser_is_opened_only_once(dash, monkeypatch):
    calls = []
    monkeypatch.setattr("webbrowser.open", lambda url: calls.append(url))
    dash.open_once()
    dash.open_once()
    dash.open_once()
    assert len(calls) == 1


def test_a_browser_failure_does_not_raise(dash, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda url: (_ for _ in ()).throw(RuntimeError("no browser")))
    dash.open_once()

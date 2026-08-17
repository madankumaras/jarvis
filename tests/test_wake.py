import pytest
import numpy as np

from jarvis.ears.wake import ClapDetector


def _quiet(n=1600):
    return np.full(n, 0.01, dtype=np.float32)


def _spike(n=1600):
    a = np.full(n, 0.01, dtype=np.float32)
    a[:80] = 0.9
    return a


def _warm(d, n=10):
    """Build a rolling baseline from quiet audio."""
    for _ in range(n):
        d.feed(_quiet())


def test_quiet_audio_never_wakes():
    d = ClapDetector()
    assert not any(d.feed(_quiet()) for _ in range(20))


def test_single_clap_does_not_wake():
    d = ClapDetector()
    _warm(d)
    assert d.feed(_spike()) is False


def test_two_claps_wake():
    d = ClapDetector()
    _warm(d)
    d.feed(_spike())
    d.feed(_quiet())
    assert d.feed(_spike()) is True


def test_two_claps_too_far_apart_do_not_wake():
    d = ClapDetector(double_window_chunks=2)
    _warm(d)
    d.feed(_spike())
    for _ in range(6):
        d.feed(_quiet())
    assert d.feed(_spike()) is False


def test_cooldown_suppresses_immediate_retrigger():
    d = ClapDetector()
    _warm(d)
    d.feed(_spike())
    d.feed(_quiet())
    assert d.feed(_spike()) is True
    d.feed(_spike())
    assert d.feed(_spike()) is False


def test_steady_loud_audio_does_not_wake():
    """A spike is relative to the rolling baseline, so sustained loudness --
    music, a fan, a long sentence -- must not read as clapping."""
    d = ClapDetector()
    loud = np.full(1600, 0.8, dtype=np.float32)
    assert not any(d.feed(loud) for _ in range(40))


def test_empty_chunk_does_not_crash():
    d = ClapDetector()
    assert d.feed(np.array([], dtype=np.float32)) is False


# --- Capture: the seam that lets wake speak and record from one stream ---

import queue as _queue

from jarvis.ears.wake import Capture, WakeListener


def _queue_of(*chunks):
    q = _queue.Queue()
    for c in chunks:
        q.put(c)
    return q


def test_capture_record_returns_exactly_the_requested_samples():
    from jarvis.ears.stt import SAMPLE_RATE

    q = _queue_of(*[np.full(1280, 0.5, dtype=np.float32) for _ in range(20)])
    audio = Capture(q).record(1.0)
    assert len(audio) == SAMPLE_RATE


def test_capture_drain_discards_buffered_audio():
    q = _queue_of(_quiet(), _quiet(), _quiet())
    c = Capture(q)
    c.drain()
    assert q.empty()


def test_capture_drain_on_empty_queue_does_not_block():
    c = Capture(_queue.Queue())
    c.drain()  # must return, not hang


class _FakeStream:
    """Stands in for sd.InputStream: feeds scripted chunks through the callback
    exactly as PortAudio would, on a background thread.

    After the script is exhausted it keeps emitting quiet audio, because a real
    microphone never stops. Without that the run loop blocks forever on an empty
    queue and a failing assertion becomes a hang instead of a failure.
    """

    def __init__(self, chunks, callback, chunk_delay=0.004):
        self._chunks = chunks
        self._callback = callback
        self._stop = False
        # A real mic delivers in real time. Pumping instantly would let the
        # post-turn drain() -- which correctly discards audio buffered while
        # Jarvis was talking -- also swallow chunks scripted to arrive later.
        self._delay = chunk_delay

    def __enter__(self):
        import threading
        import time

        def pump():
            for c in self._chunks:
                if self._stop:
                    return
                self._callback(c.reshape(-1, 1), len(c), None, None)
                time.sleep(self._delay)
            while not self._stop:
                q = _quiet()
                self._callback(q.reshape(-1, 1), len(q), None, None)
                time.sleep(self._delay)

        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        return False


def _run_with_timeout(listener, seconds=10.0):
    """Run listener.run() on a worker thread. Returns the exception it ended
    with, or None if it was still running when the timeout expired."""
    import threading

    box = {}

    def go():
        try:
            listener.run()
        except BaseException as exc:  # SystemExit is not an Exception
            box["exc"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    return box.get("exc")


def test_run_calls_on_wake_on_the_main_thread_with_a_capture(monkeypatch):
    """Regression: on_wake used to be invoked inside the PortAudio callback,
    where it blocked on `say` and opened a second input stream. Neither is
    allowed, and wake silently did nothing. It must now run on the caller's
    thread and receive a Capture."""
    import sys
    import threading
    import types

    chunks = [_quiet() for _ in range(10)] + [_spike(), _quiet(), _spike()]
    chunks += [_quiet() for _ in range(30)]

    seen = {}
    main_thread = threading.current_thread()

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.InputStream = lambda **kw: _FakeStream(chunks, kw["callback"])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    def on_wake(capture, source=None):
        seen["thread"] = threading.current_thread()
        seen["capture"] = capture
        raise SystemExit  # stop the run loop

    class _Spy(WakeListener):
        def run(self):
            seen["run_thread"] = threading.current_thread()
            super().run()

    wl = _Spy(on_wake, use_wakeword=False)
    exc = _run_with_timeout(wl)

    assert isinstance(exc, SystemExit), "on_wake was never reached"
    assert seen["thread"] is not main_thread  # run() itself is on a worker thread here
    assert seen["thread"] is seen["run_thread"], (
        "on_wake must run on run()'s thread, not the audio callback's"
    )
    assert isinstance(seen["capture"], Capture)


def test_wake_listener_fired_detects_a_double_clap():
    seen = []
    wl = WakeListener(lambda c, src=None: seen.append(c), use_wakeword=False)
    for _ in range(10):
        wl.fired(_quiet())
    wl.fired(_spike())
    wl.fired(_quiet())
    assert wl.fired(_spike()) is True


def test_wake_listener_fired_is_quiet_on_silence():
    wl = WakeListener(lambda c, src=None: None, use_wakeword=False)
    assert not any(wl.fired(_quiet()) for _ in range(20))


# --- regression: works once, then goes deaf ---

def test_reset_clears_a_baseline_polluted_by_our_own_speech():
    """Jarvis's reply is loud and the mic hears it. Without reset(), that audio
    lands in the rolling baseline and a real clap can no longer beat the 8x
    ratio -- wake works exactly once, then goes deaf."""
    d = ClapDetector()
    _warm(d)

    # our own reply blaring into the mic for ~2s
    loud = np.full(1600, 0.85, dtype=np.float32)
    for _ in range(25):
        d.feed(loud)

    # a genuine double clap is now ignored
    d.feed(_spike())
    d.feed(_quiet())
    assert d.feed(_spike()) is False, "baseline should be poisoned before reset"

    d.reset()
    _warm(d)
    d.feed(_spike())
    d.feed(_quiet())
    assert d.feed(_spike()) is True, "reset must restore sensitivity"


def test_second_wake_fires_after_a_handled_turn(monkeypatch):
    """End to end through run(): two separate double-claps, with loud self-speech
    and a slow handler in between, must both wake."""
    import sys
    import types

    loud = np.full(1600, 0.85, dtype=np.float32)
    chunks = (
        [_quiet()] * 10
        + [_spike(), _quiet(), _spike()]        # wake #1
        + [loud] * 30                            # our reply (120ms at 4ms/chunk)
        + [_quiet()] * 40                        # room settles; baseline rebuilds
        + [_spike(), _quiet(), _spike()]        # wake #2
        + [_quiet()] * 20
    )

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.InputStream = lambda **kw: _FakeStream(chunks, kw["callback"])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    wakes = []

    def on_wake(capture, source=None):
        # Model a real turn: Jarvis speaks and waits for the answer while the
        # mic keeps running. The `loud` chunks above are pumped during this
        # sleep, so they land in the queue and are discarded by drain() --
        # exactly as our own voice is in production.
        import time

        wakes.append(True)
        if len(wakes) >= 2:
            raise SystemExit
        # Long enough to cover the 120ms of `loud`, short enough that clap #2
        # still arrives after the turn rather than being drained with it.
        time.sleep(0.15)

    wl = WakeListener(on_wake, use_wakeword=False, settle=0)
    exc = _run_with_timeout(wl)

    assert isinstance(exc, SystemExit), f"only {len(wakes)} wake(s) fired before timeout"
    assert len(wakes) == 2, f"expected two wakes, got {len(wakes)}"


def test_exception_in_on_wake_does_not_kill_the_listener(monkeypatch):
    import sys
    import types

    chunks = (
        [_quiet()] * 10
        + [_spike(), _quiet(), _spike()]
        + [_quiet()] * 10
        + [_spike(), _quiet(), _spike()]
        + [_quiet()] * 20
    )
    fake_sd = types.ModuleType("sounddevice")
    fake_sd.InputStream = lambda **kw: _FakeStream(chunks, kw["callback"])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    calls = []

    def on_wake(capture, source=None):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("worker exploded")
        raise SystemExit

    wl = WakeListener(on_wake, use_wakeword=False, settle=0)
    exc = _run_with_timeout(wl)

    assert isinstance(exc, SystemExit), f"listener stopped after {len(calls)} call(s)"
    assert len(calls) == 2, "listener must survive a failing turn"


# --- measured against a real mic: floor ~0.006, transients to 8.4 ---

def _at(level, n=1280):
    return np.full(n, level, dtype=np.float32)


def test_tiny_sounds_in_a_silent_room_do_not_wake():
    """In a near-silent room almost anything is 8x the floor. Without an
    absolute min_peak, a page turn would wake Jarvis."""
    d = ClapDetector()
    for _ in range(25):
        d.feed(_at(0.0005))          # very quiet room
    d.feed(_at(0.01))                 # 20x the floor, but inaudibly quiet
    d.feed(_at(0.0005))
    assert d.feed(_at(0.01)) is False


def test_one_huge_transient_does_not_blind_the_detector():
    """Measured: this mic peaks at 8.4 on a transient while the floor is 0.006.
    A mean baseline would jump to ~0.34 and ignore claps for two seconds; the
    median must shrug it off."""
    d = ClapDetector()
    for _ in range(25):
        d.feed(_at(0.006))
    d.feed(_at(8.4))                  # door slam / our own speech
    # Let the pairing window (8 chunks) and the cooldown expire, so we are
    # testing baseline recovery rather than the transient's own clap pair.
    for _ in range(25):
        d.feed(_at(0.006))
    assert d.baseline() < 0.05, f"floor drifted to {d.baseline():.4f}"

    d.feed(_at(0.5))
    d.feed(_at(0.006))
    assert d.feed(_at(0.5)) is True, "a real clap must still register"


def test_a_transient_then_a_clap_within_the_window_does_wake():
    """Documented consequence of pair-matching: any two loud transients within
    ~640ms read as a double clap. A door slam followed by a cough will wake it."""
    d = ClapDetector()
    for _ in range(25):
        d.feed(_at(0.006))
    d.feed(_at(8.4))
    d.feed(_at(0.006))
    assert d.feed(_at(0.5)) is True


def test_baseline_is_zero_before_any_audio():
    assert ClapDetector().baseline() == 0.0


def test_the_settle_delay_defaults_to_letting_speakers_go_quiet():
    """`say` returns when it hands text to the speech engine, not when the
    speakers stop. Draining immediately leaves our own voice in the queue,
    which then reads as a fresh wake."""
    from jarvis.ears.wake import SETTLE_SECONDS

    assert SETTLE_SECONDS > 0
    assert WakeListener(lambda c, src=None: None, use_wakeword=False).settle == SETTLE_SECONDS


def test_the_first_sound_after_startup_can_register():
    """is_spike requires baseline > 0, so an unprimed deque silently discards
    the very first clap -- which is the one you just made."""
    d = ClapDetector()
    d.prime()
    assert d.baseline() > 0
    d.feed(_spike())
    d.feed(_quiet())
    assert d.feed(_spike()) is True


def test_reset_leaves_the_baseline_primed():
    d = ClapDetector()
    _warm(d)
    d.reset()
    assert d.baseline() > 0


def test_a_new_listener_starts_primed():
    wl = WakeListener(lambda c, src=None: None, use_wakeword=False)
    assert wl.clap.baseline() > 0


def test_priming_does_not_override_a_real_baseline():
    d = ClapDetector()
    for _ in range(5):
        d.feed(_at(0.02))
    before = d.baseline()
    d.prime()
    assert d.baseline() == before


# --- wake mode, and not waking on our own voice ---

def test_wakeword_only_ignores_claps():
    """A clap detector only knows loudness: any two loud sounds inside the
    pairing window qualify. The wake word matches a pattern instead."""
    wl = WakeListener(lambda c, s=None: None, mode="wakeword")
    assert wl.use_clap is False
    for _ in range(10):
        wl.fired(_quiet())
    wl.fired(_spike())
    wl.fired(_quiet())
    assert wl.fired(_spike()) is False


def test_clap_only_does_not_load_the_wakeword_model():
    wl = WakeListener(lambda c, s=None: None, mode="clap")
    assert wl.use_wakeword is False
    assert wl._ensure_wakeword() is None


def test_both_enables_each_path():
    wl = WakeListener(lambda c, s=None: None, mode="both")
    assert wl.use_clap and wl.use_wakeword


def test_the_default_mode_is_the_accurate_one():
    from jarvis.ears.wake import WAKE_MODE

    assert WAKE_MODE == "wakeword"


def test_muted_audio_never_wakes(monkeypatch):
    """Tier-3 completions and watcher announcements speak from background
    threads. Without muting, Jarvis's own long sentence wakes Jarvis."""
    import sys
    import types

    chunks = [_quiet()] * 10 + [_spike(), _quiet(), _spike()] + [_quiet()] * 20
    fake_sd = types.ModuleType("sounddevice")
    fake_sd.InputStream = lambda **kw: _FakeStream(chunks, kw["callback"])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    wakes = []
    wl = WakeListener(lambda c, s=None: wakes.append(1), mode="clap", settle=0)
    wl.muted.set()
    _run_with_timeout(wl, seconds=1.5)
    assert wakes == [], "muted audio must not wake"


def test_unmuting_restores_waking(monkeypatch):
    import sys
    import types

    chunks = [_quiet()] * 10 + [_spike(), _quiet(), _spike()] + [_quiet()] * 20
    fake_sd = types.ModuleType("sounddevice")
    fake_sd.InputStream = lambda **kw: _FakeStream(chunks, kw["callback"])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    def on_wake(capture, source=None):
        raise SystemExit

    wl = WakeListener(on_wake, mode="clap", settle=0)
    assert isinstance(_run_with_timeout(wl, seconds=3.0), SystemExit)

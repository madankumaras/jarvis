"""Two wake paths on one audio stream: double-clap and "hey jarvis".

Design note: the PortAudio callback does nothing but enqueue audio. Detection,
speech, and capture all happen on the main thread, reading from the same queue.
An earlier version called on_wake() directly from the callback, which both
blocked it (subprocess `say`, ~1s) and opened a second input stream from inside
the first one's callback. Neither is allowed, and wake silently did nothing.
"""
from __future__ import annotations

import collections
import queue
import threading
import time
import traceback
from typing import Callable

import numpy as np

from jarvis.ears.stt import SAMPLE_RATE  # single source of truth, defined in stt.py

import os

CHUNK = 1280  # 80ms at 16kHz — openwakeword's expected frame size
# JARVIS_WAKE_DEBUG=1 prints what the detector sees on every loud chunk, so a
# "it is not waking" report can be turned into numbers.
DEBUG_WAKE = bool(os.environ.get("JARVIS_WAKE_DEBUG"))

# Measured in a real session: genuine claps land at 0.36-10.9, while ambient
# noise reaches 0.08-0.19. A pair of 0.185 and 0.084 was enough to trigger a
# false wake at the old 0.08 floor. 0.25 separates the two populations.
# Raise it if you still get phantom wakes; lower it if soft claps are missed.
CLAP_MIN_PEAK = float(os.environ.get("JARVIS_CLAP_MIN_PEAK", "0.25"))

# Which wake paths are live. A clap detector only knows loudness, so any two
# loud sounds inside the pairing window qualify; the wake word matches a
# specific acoustic pattern and rejects nearly everything else. Default to the
# accurate one.
#   JARVIS_WAKE_MODE=wakeword   "hey jarvis" only  (default)
#   JARVIS_WAKE_MODE=clap       double-clap only
#   JARVIS_WAKE_MODE=both       either
WAKE_MODE = os.environ.get("JARVIS_WAKE_MODE", "wakeword").strip().lower()
# How long to let the speakers fall quiet before trusting the mic again.
SETTLE_SECONDS = 0.6
WAKEWORD_THRESHOLD = 0.5


class ClapDetector:
    """Spike-ratio detector against a rolling baseline, with a double-clap window.

    The ratio is relative, not absolute, so sustained loudness (music, a fan,
    a long sentence) never reads as a clap — only a sharp transient does.
    """

    def __init__(
        self,
        ratio: float = 8.0,
        double_window_chunks: int = 8,
        cooldown_chunks: int = 12,
        baseline_len: int = 25,
        min_peak: float = CLAP_MIN_PEAK,
    ) -> None:
        self.ratio = ratio
        self.min_peak = min_peak
        self.double_window_chunks = double_window_chunks
        self.cooldown_chunks = cooldown_chunks
        self._baseline: collections.deque[float] = collections.deque(maxlen=baseline_len)
        self._since_first_clap: int | None = None
        self._cooldown = 0

    def baseline(self) -> float:
        """Median, not mean.

        Measured on a real MacBook mic: a quiet room sits around 0.006, but
        transients (a door, our own speech) reach 8.4 — three orders of
        magnitude higher. With a mean, one such sample drags a 25-sample
        baseline to ~0.34 and blinds the detector for two seconds. The median
        ignores outliers, which is exactly what a noise floor should do.
        """
        return float(np.median(self._baseline)) if self._baseline else 0.0

    def feed(self, chunk: np.ndarray) -> bool:
        """Return True when a double-clap completes on this chunk."""
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        baseline = self.baseline()

        if self._cooldown > 0:
            self._cooldown -= 1
            self._baseline.append(peak)
            return False

        # Both tests must pass: well above the noise floor, and loud in
        # absolute terms. The ratio alone misfires in a silent room, where
        # any small sound is 8x a near-zero floor.
        is_spike = (
            baseline > 0
            and peak > baseline * self.ratio
            and peak > self.min_peak
        )

        if self._since_first_clap is not None:
            self._since_first_clap += 1
            if self._since_first_clap > self.double_window_chunks:
                self._since_first_clap = None

        if not is_spike:
            self._baseline.append(peak)
            return False

        if self._since_first_clap is None:
            self._since_first_clap = 0
            return False

        self._since_first_clap = None
        self._cooldown = self.cooldown_chunks
        return True

    def prime(self, level: float = 0.006) -> None:
        """Seed the baseline with a plausible quiet-room floor.

        is_spike requires baseline > 0, so with an empty deque the very first
        sound after startup or reset can never register — a wasted clap.
        0.006 is the measured median of a quiet room on this mic.
        """
        if not self._baseline:
            self._baseline.append(level)

    def reset(self) -> None:
        """Forget the rolling baseline and any half-finished clap.

        Called after a wake is handled. During handling, Jarvis speaks — and
        the mic hears it. Letting that audio into the baseline raises it so
        far that a real clap can no longer beat the 8x ratio, and wake goes
        deaf after the first interaction.
        """
        self._baseline.clear()
        self._since_first_clap = None
        self._cooldown = 0
        self.prime()


class Capture:
    """Handed to the wake callback so it can speak, then record, from the one
    live stream. Without drain(), Jarvis records its own greeting."""

    def __init__(self, audio_queue: "queue.Queue[np.ndarray]") -> None:
        self._q = audio_queue

    def drain(self) -> None:
        """Discard everything buffered so far — e.g. the greeting we just spoke."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def record(self, seconds: float) -> np.ndarray:
        need = int(seconds * SAMPLE_RATE)
        parts: list[np.ndarray] = []
        got = 0
        while got < need:
            chunk = self._q.get()
            parts.append(chunk)
            got += len(chunk)
        return np.concatenate(parts)[:need]


class WakeListener:
    """Owns the microphone. Calls on_wake(capture, source) on the main thread.

    `source` is "clap" or "wakeword:<name>", so a spurious wake can be
    attributed rather than guessed at.
    """

    def __init__(
        self,
        on_wake: Callable[["Capture", str], None],
        use_wakeword: bool | None = None,
        settle: float = SETTLE_SECONDS,
        use_clap: bool | None = None,
        mode: str | None = None,
    ) -> None:
        mode = (mode or WAKE_MODE) if (use_wakeword is None and use_clap is None) else None
        if mode is not None:
            use_wakeword = mode in {"wakeword", "both"}
            use_clap = mode in {"clap", "both"}

        self.on_wake = on_wake
        self.clap = ClapDetector()
        self.clap.prime()
        self.use_wakeword = True if use_wakeword is None else use_wakeword
        self.use_clap = True if use_clap is None else use_clap
        self.settle = settle
        self.source = ""
        self._oww = None
        # Set while Jarvis is speaking. Its own voice reaches the mic, and a
        # long reply otherwise reads as a wake. Tier-3 completions and watcher
        # announcements happen on background threads, so this cannot rely on
        # the turn-scoped settle.
        self.muted = threading.Event()

    def _ensure_wakeword(self):
        if self._oww is None and self.use_wakeword:
            from openwakeword.model import Model

            self._oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        return self._oww

    def reset(self) -> None:
        """Clear detector state after a turn, so the next wake starts clean."""
        self.clap.reset()
        if self._oww is not None:
            self._oww.reset()

    def fired(self, chunk: np.ndarray) -> bool:
        """True if either wake path triggers on this chunk.

        Sets self.source to whichever path fired, so a spurious wake can be
        attributed instead of guessed at.
        """
        if not self.use_clap:
            if self._oww is not None:
                pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16)
                hits = [k for k, v in self._oww.predict(pcm).items() if v > WAKEWORD_THRESHOLD]
                if hits:
                    self.source = f"wakeword:{hits[0]}"
                    return True
            return False

        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        floor = self.clap.baseline()
        needs = max(floor * self.clap.ratio, self.clap.min_peak)
        pending_before = self.clap._since_first_clap is not None

        if self.clap.feed(chunk):
            self.source = "clap"
            if DEBUG_WAKE:
                print(f"  [wake] CLAP-2 peak={peak:.4f} floor={floor:.4f}", flush=True)
            return True

        if DEBUG_WAKE:
            if self.clap._since_first_clap is not None and not pending_before:
                print(
                    f"  [wake] clap-1 peak={peak:.4f} floor={floor:.4f} "
                    f"needs={needs:.4f} — waiting for the second",
                    flush=True,
                )
            elif peak > needs * 0.5:
                state = "cooldown" if self.clap._cooldown else "below threshold"
                print(
                    f"  [wake] loud   peak={peak:.4f} floor={floor:.4f} "
                    f"needs={needs:.4f} ({state})",
                    flush=True,
                )
        if self._oww is not None:
            # Clip before scaling. Measured peaks on this mic reach 8.4, and
            # 8.4 * 32767 wraps int16 into garbage — the wake word was being
            # fed noise every time the room got loud.
            pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16)
            scores = self._oww.predict(pcm)
            hits = [k for k, v in scores.items() if v > WAKEWORD_THRESHOLD]
            if hits:
                self.source = f"wakeword:{hits[0]}"
                return True
        return False

    def run(self) -> None:
        import sounddevice as sd

        self._ensure_wakeword()
        audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        capture = Capture(audio_queue)

        def callback(indata, frames, time_info, status):
            # Enqueue only. Anything slower belongs on the main thread.
            audio_queue.put(indata[:, 0].copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            blocksize=CHUNK,
            dtype="float32",
            callback=callback,
        ):
            while True:
                chunk = audio_queue.get()
                if self.muted.is_set():
                    # Our own voice. Discard it and keep the detectors clean
                    # rather than letting it accumulate into a wake.
                    self.reset()
                    continue
                if not self.fired(chunk):
                    continue
                try:
                    self.on_wake(capture, self.source)
                except Exception:
                    # A failed turn must never take the listener down with it.
                    traceback.print_exc()
                finally:
                    # Handling a wake takes ~15s of greeting, capture, lookup
                    # and reply. The mic ran the whole time, so the queue now
                    # holds a backlog of Jarvis's own voice. Throw it away and
                    # start the detectors clean, or the next clap is measured
                    # against a baseline inflated by our own speech.
                    #
                    # `say` returns when it has handed the text to the speech
                    # engine, not when the speakers go quiet. Draining
                    # immediately leaves the tail of our own voice in the
                    # queue, which then reads as a fresh wake. Settle first.
                    if self.settle:
                        time.sleep(self.settle)
                    capture.drain()
                    self.reset()

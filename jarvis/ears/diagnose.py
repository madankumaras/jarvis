"""Live wake-detection meter.

    .venv/bin/python -m jarvis.ears.diagnose

Prints one line per 80ms of audio so you can see what the detector sees.
Clap and watch: CLAP-1 on the first, WAKE on the second. If nothing moves,
the numbers say why — the noise floor, the threshold, and your peak are all
on screen.
"""
from __future__ import annotations

import sys
import time

import numpy as np

from jarvis.ears.wake import CHUNK, ClapDetector
from jarvis.ears.stt import SAMPLE_RATE

BAR_WIDTH = 30


def _bar(value: float, ceiling: float) -> str:
    filled = int(min(value / ceiling, 1.0) * BAR_WIDTH) if ceiling > 0 else 0
    return "#" * filled + "." * (BAR_WIDTH - filled)


def main(seconds: float = 30.0) -> None:
    import sounddevice as sd

    det = ClapDetector()
    device = sd.query_devices(kind="input")["name"]
    print(f"listening on: {device}")
    print(f"ratio={det.ratio}x over the median noise floor, min_peak={det.min_peak}")
    print("clap twice. ctrl-c to stop.\n")
    print(f"{'peak':>8} {'floor':>8} {'needs':>8}  {'level':<30} event")

    stats = {"n": 0, "claps": 0, "wakes": 0}

    def cb(indata, frames, t, status):
        chunk = indata[:, 0]
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        floor = det.baseline()
        needs = max(floor * det.ratio, det.min_peak)
        pending_before = det._since_first_clap is not None

        woke = det.feed(chunk)
        stats["n"] += 1

        event = ""
        if woke:
            event = "*** WAKE ***"
            stats["wakes"] += 1
        elif det._since_first_clap is not None and not pending_before:
            event = "clap 1 — waiting for the second"
            stats["claps"] += 1
        elif peak > needs and needs > 0:
            event = "loud, but not a clap pattern"

        if event or stats["n"] % 12 == 0:
            print(f"{peak:8.4f} {floor:8.4f} {needs:8.4f}  {_bar(peak, max(needs * 2, 0.2)):<30} {event}")

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, blocksize=CHUNK, dtype="float32", callback=cb
        ):
            time.sleep(seconds)
    except KeyboardInterrupt:
        pass

    print(f"\n{stats['claps']} first-claps detected, {stats['wakes']} wakes")
    if stats["wakes"] == 0 and stats["claps"] == 0:
        print("Nothing registered. Your claps are not beating the 'needs' column —")
        print("lower min_peak or ratio in jarvis/ears/wake.py.")
    elif stats["wakes"] == 0:
        print("First claps registered but never a second. Clap faster —")
        print("the pair must land within ~640ms — or raise double_window_chunks.")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 30.0)

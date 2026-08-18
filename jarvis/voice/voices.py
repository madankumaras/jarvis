"""List and audition the installed voices.

    .venv/bin/python -m jarvis.voice.voices           # list, then read a sample
    .venv/bin/python -m jarvis.voice.voices --list    # list only, no audio
    .venv/bin/python -m jarvis.voice.voices Rishi     # audition one

Pick one and set it:

    JARVIS_VOICE=Rishi .venv/bin/python -m jarvis.daemon
"""
from __future__ import annotations

import re
import subprocess
import sys

# `say -v '?'` mixes real speech synthesisers with novelty voices that sing or
# buzz. None of these are usable for an assistant.
NOVELTY = {
    "Bad", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Good", "Jester",
    "Organ", "Superstar", "Trinoids", "Whisper", "Wobble", "Zarvox", "Albert",
    "Junior", "Kathy", "Fred", "Ralph", "Grandma", "Grandpa", "Eddy", "Flo",
    "Reed", "Rocko", "Sandy", "Shelley",
}

SAMPLE = "In MCSL 385 you have 3 tickets assigned to you."


def english_voices() -> list[tuple[str, str]]:
    """Installed English voices worth using, as (name, locale)."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    except OSError:
        return []

    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s+(en_[A-Z]{2})\s", line)
        if not m:
            continue
        name, locale = m.group(1), m.group(2)
        if name in NOVELTY or name in seen:
            continue
        seen.add(name)
        found.append((name, locale))
    # Indian English first: it matches the accent of the person speaking to it.
    return sorted(found, key=lambda v: (v[1] != "en_IN", v[0]))


def main() -> None:
    args = [a for a in sys.argv[1:]]
    voices = english_voices()
    if not voices:
        print("No English voices found. Is this macOS?")
        return

    if args and args[0] not in {"--list", "-l"}:
        name = args[0]
        print(f"{name}: {SAMPLE}")
        subprocess.run(["say", "-v", name, "-r", "190", "--", SAMPLE], check=False)
        return

    print(f"{len(voices)} usable English voices:\n")
    for name, locale in voices:
        print(f"  {name:<12} {locale}")
    print(f"\nSet one with:  JARVIS_VOICE=<name> .venv/bin/python -m jarvis.daemon")

    if args and args[0] in {"--list", "-l"}:
        return

    print("\nReading a sample in each. Ctrl-C to stop.\n")
    for name, locale in voices:
        print(f"  {name} ({locale})")
        subprocess.run(["say", "-v", name, "-r", "190", "--", SAMPLE], check=False)


if __name__ == "__main__":
    main()

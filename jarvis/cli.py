"""Type commands instead of speaking them. The demo and debug entry point.

    .venv/bin/python -m jarvis.cli "status of MCSL 384"
    .venv/bin/python -m jarvis.cli          # interactive
"""
from __future__ import annotations

import sys

from jarvis.memory.store import Store
from jarvis.router.core import handle_transcript
from jarvis.types import Vocab
from jarvis.voice.speak import speak
from jarvis.workers.manager import WorkerManager


def main() -> None:
    manager = WorkerManager()
    worker = manager.get("mcsl")
    store = Store()
    raw = worker.call("vocab")
    vocab = Vocab(**{k: raw.get(k, []) for k in ("cards", "people", "carriers", "zi_ids")})

    if len(sys.argv) > 1:
        lines = [" ".join(sys.argv[1:])]
    else:
        lines = iter(lambda: input("you> "), "")

    for line in lines:
        response = handle_transcript(line, vocab, worker, store=store, domain="mcsl")
        # Logged here too: the CLI is where phrasings get tried out, so it is
        # the richest source of evidence for which intent to add next.
        try:
            store.log_command(raw=line, corrected=line, intent="",
                              tier=response.tier, ok=response.ok)
        except Exception:
            pass
        print(f"jarvis> [tier {response.tier}] {response.speech}")
        speak(response)


if __name__ == "__main__":
    main()

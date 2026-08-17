"""macOS speech and notifications. Both are free and local."""
from __future__ import annotations

import subprocess

from jarvis.types import Response

VOICE = "Daniel"  # swap for any voice in `say -v '?'`
RATE = 190


def say(text: str) -> None:
    if not text or not text.strip():
        return
    # `--` ends option parsing. Without it, spoken text beginning with a dash
    # is read as a flag and `say` refuses the whole utterance:
    #   say -v Daniel "-x hello"  ->  say: invalid option -- x
    # Trello titles and comments routinely start with "-".
    subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "--", text], check=False)


def notify(title: str, body: str) -> None:
    safe_body = body.replace("\\", "\\\\").replace('"', '\\"')
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def speak(response: Response, notify_user: bool = True) -> None:
    """Say the short line; put the full text on screen so a mishear is recoverable."""
    say(response.speech)
    if notify_user:
        notify("Jarvis", (response.detail or response.speech)[:400])

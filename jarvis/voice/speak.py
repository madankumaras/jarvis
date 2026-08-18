"""macOS speech and notifications. Both are free and local."""
from __future__ import annotations

import os
import re
import subprocess

from jarvis.types import Response

# Any installed macOS voice: `.venv/bin/python -m jarvis.voice.voices` lists
# the usable ones and reads a sample in each. Tara (en_IN) is the default:
# Jarvis reads out carrier names, colleague names and ticket titles, and an
# Indian English voice pronounces them correctly where en_GB mangles them.
VOICE = os.environ.get("JARVIS_VOICE", "Tara")
RATE = int(os.environ.get("JARVIS_VOICE_RATE", "190"))


# Trello comments are written for reading, not speaking. macOS `say` reads
# emoji aloud by their Unicode names, so a comment containing 🧪 ✅ → •
# becomes "test tube", "white heavy check mark", "rightwards arrow",
# "bullet" -- which is what "it talked another language" actually was.
_SPEAKABLE = [
    (re.compile(r"[\u2014\u2013]"), ", "),           # em/en dash -> a pause
    (re.compile(r"\u2192|->"), " to "),              # arrows
    (re.compile(r"[\u2018\u2019]"), "'"),            # smart quotes
    (re.compile(r"[\u201c\u201d]"), ""),
    # Before the markdown rule: that strips the "#" this one needs.
    (re.compile(r"\[#(\d+)\]?"), r" ticket \1"),     # "[#399431]" reads as noise
    (re.compile(r"[*_`#>|]+"), " "),                 # markdown scaffolding
    (re.compile(r"https?://\S+"), " a link "),
    # Anything left outside basic Latin: emoji, pictographs, box drawing.
    (re.compile(r"[^\x00-\x7F]+"), " "),
    (re.compile(r"\s+"), " "),
]


def speakable(text: str) -> str:
    """Strip what a screen reads but a voice should not.

    Emoji and pictographs are removed rather than transliterated: there is no
    useful spoken form of 🧪 in the middle of a sentence about a carrier bug.
    """
    out = text or ""
    for pattern, replacement in _SPEAKABLE:
        out = pattern.sub(replacement, out)
    return out.strip(" ,.;:-")


def say(text: str) -> None:
    text = speakable(text)
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

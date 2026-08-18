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
# JARVIS_SPEECH_DEBUG=1 prints the exact string handed to `say`, so "it spoke
# another language" can be read rather than guessed at.
DEBUG_SPEECH = bool(os.environ.get("JARVIS_SPEECH_DEBUG"))


# Trello comments are written for reading, not speaking. macOS `say` reads
# emoji aloud by their Unicode names, so a comment containing 🧪 ✅ → •
# becomes "test tube", "white heavy check mark", "rightwards arrow",
# "bullet" -- which is what "it talked another language" actually was.
_SPEAKABLE = [
    (re.compile(r"[\u2014\u2013]"), ", "),           # em/en dash -> a pause
    (re.compile(r"\u2192|->"), " to "),              # arrows
    (re.compile(r"[\u2018\u2019]"), "'"),            # smart quotes
    (re.compile(r"[\u201c\u201d]"), ""),
    # An embedded image is not worth speaking at all. Trello comments are full
    # of "![image.webp](https://trello.com/1/cards/.../download/image.webp)",
    # which was read out as bracket-and-filename noise.
    (re.compile(r"!\[[^\]]*\]\s*[\(\[][^\)\]]*[\)\]]"), " an image "),
    (re.compile(r"!\[[^\]]*\]"), " an image "),
    # Before the markdown rule: that strips the "#" this one needs.
    (re.compile(r"\[#(\d+)\]?"), r" ticket \1"),     # "[#399431]" reads as noise
    (re.compile(r"\[([^\]]{1,60})\]\s*[\(\[][^\)\]]*[\)\]]"), r" \1 "),  # [text](url)
    (re.compile(r"[\[\]]+"), " "),
    (re.compile(r"[*_`#>|]+"), " "),                 # markdown scaffolding
    (re.compile(r"https?://\S+"), " a link "),
    # Anything left outside basic Latin: emoji, pictographs, box drawing.
    (re.compile(r"[^\x00-\x7F]+"), " "),
    (re.compile(r"\s+"), " "),
]


# Code identifiers are everywhere in these cards, and every TTS reads them as
# noise -- "bkg_ref_id" becomes "bkg ref id", which in an Indian English voice
# lands somewhere between gibberish and another language. Expanded to the words
# a person would actually say.
_ABBREV = {
    "bkg": "booking", "ref": "reference", "id": "I D", "ids": "I Ds",
    "qty": "quantity", "addr": "address", "cfg": "config", "env": "environment",
    "req": "request", "res": "response", "auth": "auth", "api": "A P I",
    "url": "U R L", "uuid": "U U ID", "csv": "C S V", "json": "jason",
    "sku": "S K U", "hs": "H S", "eta": "E T A", "sla": "S L A",
    "pr": "P R", "qa": "Q A", "tc": "test case", "ac": "acceptance criteria",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM = re.compile(r"\b([A-Z]{2,4})\b")
_WORD = re.compile(r"[A-Za-z]+")


def _expand_identifiers(text: str) -> str:
    """Make code-shaped tokens pronounceable.

    Three passes: split camelCase into words, expand known abbreviations, and
    space out short all-caps acronyms so they are read as letters rather than
    attempted as words. Longer all-caps strings are left alone -- they are
    usually pronounceable, and spelling out eight letters is worse.
    """
    out = _CAMEL.sub(" ", text)

    def _word(m: re.Match[str]) -> str:
        w = m.group(0)
        return _ABBREV.get(w.lower(), w)

    out = _WORD.sub(_word, out)
    # After abbreviation expansion, so "API" is not spaced twice.
    return _ACRONYM.sub(lambda m: " ".join(m.group(1)), out)


def speakable(text: str) -> str:
    """Strip what a screen reads but a voice should not.

    Emoji and pictographs are removed rather than transliterated: there is no
    useful spoken form of 🧪 in the middle of a sentence about a carrier bug.
    """
    out = text or ""
    for pattern, replacement in _SPEAKABLE:
        out = pattern.sub(replacement, out)
    out = _expand_identifiers(out)
    return re.sub(r"\s+", " ", out).strip(" ,.;:-")


def say(text: str) -> None:
    text = speakable(text)
    if not text or not text.strip():
        return
    # `--` ends option parsing. Without it, spoken text beginning with a dash
    # is read as a flag and `say` refuses the whole utterance:
    #   say -v Daniel "-x hello"  ->  say: invalid option -- x
    # Trello titles and comments routinely start with "-".
    if DEBUG_SPEECH:
        print(f"  [say] {text!r}", flush=True)
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

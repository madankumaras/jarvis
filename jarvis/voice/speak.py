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
    # Carriers and domain terms whose vowels would otherwise have them read as
    # words: "UPS" as a word, "USPS" as one syllable of nonsense.
    "ups": "U P S", "usps": "U S P S", "dhl": "D H L", "gls": "G L S",
    "tnt": "T N T", "zi": "Z I", "kg": "K G", "lbs": "pounds", "cm": "centimetres",
    "aupost": "Au Post", "mcsl": "M C S L", "wms": "W M S", "sl": "S L",
    "eu": "E U", "us": "U S", "uk": "U K", "in": "in", "iata": "I A T A",
    # Currency codes all contain a vowel, so the rule below would attempt them
    # as words -- "Inr", "Aud". The name is what a person says anyway.
    "inr": "rupees", "usd": "dollars", "eur": "euros", "gbp": "pounds",
    "aud": "Australian dollars", "cad": "Canadian dollars", "aed": "dirhams",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM = re.compile(r"\b([A-Z]{2,6})\b")
_WORD = re.compile(r"[A-Za-z]+")
# A vowel means it can be attempted as a word. Measured against 28 real tokens:
# after the known domain terms above are handled, this rule is correct on every
# remaining one -- MCSL, DHL, GLS, TNT, KG, CSV spelled; USE, LIST, ACCOUNT,
# PICKUP, SCHEDULED, TRUE, NULL, ERROR, OPEN, DONE, GET said as words. It fails
# safe: an odd pronunciation beats eight spelled-out letters.
_VOWEL = re.compile(r"[AEIOUY]")
# Extensions read aloud as "dot m d". The name alone is the useful part.
_CODE_EXT = re.compile(
    r"\.(?:md|py|js|ts|tsx|json|ya?ml|txt|csv|sql|sh|html?|xml|log|env|ini|toml)\b",
    re.I,
)
# A random-looking tail on an identifier, as Shopify puts on every store slug:
# "mypostautomation-gs01o4wy". Reading it out is noise, and the readable half
# is what identifies the store to a person.
_RANDOM_TAIL = re.compile(r"(?<=[a-z]{3})-(?=[a-z0-9]{6,12}\b)(?=[a-z0-9]*\d)[a-z0-9]+\b")

# A long run of digits is an identifier, and `say` reads it as one enormous
# cardinal: 548419010 becomes "five hundred forty-eight million four hundred
# nineteen thousand and ten". Seven digits is the threshold because everything
# shorter is a real quantity -- 120000 characters, 1470 pixels, release 385 --
# and those are better read as numbers.
_LONG_DIGITS = re.compile(r"(?<![\d.])(\d{7,})(?![\d.])")
# "1470x801" is a size, not a word. Said as "by", it is a sentence.
_DIMENSIONS = re.compile(r"(?<=\d)\s*[x×]\s*(?=\d)", re.I)


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

    # After abbreviation expansion, so "API" is not spaced twice. Only tokens
    # with no vowel are spelled out: spacing every capitalised run turned
    # USE_SCHEDULED_PICKUP into "U S E SCHEDULED PICKUP" and LIST into "L I S T".
    def _caps(m: re.Match[str]) -> str:
        token = m.group(1)
        # Left exactly as it is, not capitalised: `say` reads ACCOUNT and
        # PARSPL the same either way, and rewriting them loses a service code
        # verbatim for no audible gain.
        if _VOWEL.search(token):
            return token
        return " ".join(token)

    return _ACRONYM.sub(_caps, out)


def speakable(text: str) -> str:
    """Strip what a screen reads but a voice should not.

    Emoji and pictographs are removed rather than transliterated: there is no
    useful spoken form of 🧪 in the middle of a sentence about a carrier bug.
    """
    out = text or ""
    for pattern, replacement in _SPEAKABLE:
        out = pattern.sub(replacement, out)
    # Before identifier expansion, which would otherwise spell out the tail of a
    # store slug and read ".md" as "dot m d".
    out = _RANDOM_TAIL.sub("", out)
    out = _CODE_EXT.sub("", out)
    out = _DIMENSIONS.sub(" by ", out)
    out = _LONG_DIGITS.sub(lambda m: " ".join(m.group(1)), out)
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

"""Remove the noises of speech, and nothing else.

This is about what Jarvis says, not about what it understands. Before fetching
anything it reads the subject back to you -- "give me a minute, looking into
..." -- and reading your own "um, so, like" back sounds broken. The tier-3
prompt and the dashboard transcript carry the same text.

It is *not* needed for intent matching, which was checked rather than assumed:
the intent patterns search rather than anchor, so "um so go through this doc"
already matched `read_document` and "hmm, dm Ashok saying ..." already matched
`send_dm`. Filler removal changed no routing decision in that test.

The list is deliberately short. Several words that look like filler are
load-bearing here and must survive:

    "ok"            a bare "ok" is what confirms a Slack DM
    "right", "fine" "is that right" is what switches the screen to judging
    "just"          "just show me mine" means only mine
    "like"          fine as filler, but too close to comparison phrasing
    "actually"      "ok so actually make it the GLS store" is a correction,
                    and must not decay into a bare affirmative

So the rule is: strip genuine disfluencies always, strip a couple of discourse
markers only where they open a sentence that has other words in it, and never
return an empty string -- an utterance that is nothing but filler is still
evidence that something was said, and the thin-utterance guard decides that.
"""
from __future__ import annotations

import re

# Sounds, not words. Safe to remove anywhere.
#
# The surrounding commas go with the filler. Removing the word alone leaves
# "what is the, testing plan", and a comma is a pause -- so the sentence is read
# back with a stutter exactly where the filler used to be, which is the thing
# this module exists to stop.
_FILLER_WORDS = r"u+m+|u+h+|e+r+m*|a+h+|e+h+|h+m+|m+h+m+|mm+"
_PADDING_WORDS = r"you know|i mean|kind of|sort of|how do i say|what do you call it"

_DISFLUENCY = re.compile(rf"[,\s]*\b(?:{_FILLER_WORDS})\b[,\s]*", re.I)
_PADDING = re.compile(rf"[,\s]*\b(?:{_PADDING_WORDS})\b[,\s]*", re.I)

# Openers, stripped only when the sentence continues past them, so a bare
# "well?" or "so?" is left alone rather than turned into nothing.
#
# Deliberately excludes anything starting with a confirmation word -- "ok so",
# "right so", "yeah so". Those are the words the DM read-back keys on, and an
# opener rule is the wrong place to be deleting them: "ok so actually make it
# the GLS store" must stay recognisable as the correction it is.
_OPENER = re.compile(r"^\W*(?:so|well|alright|and|but)\b[,\s]*", re.I)

# Trailing tags. "Is this correct, like that" ends up as "is this correct".
_TRAILING = re.compile(
    r"[,\s]*\b(?:like that|or something|you know|i guess|right\?)\W*$", re.I
)


def _tidy(text: str) -> str:
    """Collapse the gaps left behind by removal."""
    out = re.sub(r"\s+", " ", text)
    out = re.sub(r"\s+([,.?!])", r"\1", out)      # " ," from a removed word
    out = re.sub(r"([,;])\s*(?=[,;.?!])", "", out)  # ", ," from two in a row
    out = re.sub(r"^[\s,;.]+", "", out)
    return out.strip()


def strip_fillers(text: str) -> str:
    """Clean a transcript for reading back, matching, and prompting.

    Never returns empty when given something: a filler-only utterance keeps its
    original text so the caller can see that speech happened and reject it on
    its own terms.
    """
    if not text or not text.strip():
        return text or ""

    out = _DISFLUENCY.sub(" ", text)
    out = _PADDING.sub(" ", out)
    out = _tidy(out)
    # Openers and trailing tags are applied after tidying, so "um, so what's
    # left" has already become "so what's left" and the opener can see it.
    trimmed = _OPENER.sub("", out)
    if trimmed.strip():          # never let an opener be the whole sentence
        out = trimmed
    trimmed = _TRAILING.sub("", out)
    if trimmed.strip():
        out = trimmed

    out = _tidy(out)
    return out or text.strip()

"""Intent registry. Each entry maps a spoken pattern to a worker method."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Intent:
    name: str
    method: str
    pattern: re.Pattern[str]
    extract: Callable[[re.Match[str]], dict]


# A spoken card reference. Either a full id ("ZI-667") or a bare number after
# the word card/ticket/issue ("card 667"), which is how it actually gets said.
_CARD_REF = (
    # \b then (?!MCSL\b): without the boundary the match can simply start at
    # "CSL-386" and sail past the lookahead. MCSL-386 names a release.
    r"(?:(?P<card>\b(?!MCSL\b)[A-Z]{2,6}-\d{1,5})"
    r"|(?:card|ticket|issue)\s+(?P<cardnum>\d{2,5})(?![-\d]))"
)


def _card_params(m: re.Match[str]) -> dict:
    """Bare numbers get the ZI prefix: the board's cards are all ZI-NNN.

    Returns an empty card_id when the utterance named no card at all -- "who is
    the dev for that" is valid, and the reference is resolved from conversation
    state before this point.
    """
    groups = m.groupdict()
    if groups.get("card"):
        return {"card_id": groups["card"]}
    if groups.get("cardnum"):
        return {"card_id": f"ZI-{groups['cardnum']}"}
    return {"card_id": ""}


def _no_params(m: re.Match[str]) -> dict:
    return {}


def _query_params(m: re.Match[str]) -> dict:
    """The whole utterance is the query — the trigger phrase alone is useless
    as a knowledge-base search term."""
    return {"query": m.string.strip()}


_CARD_ID = r"(?P<card>[A-Z]{2,6}-\d{1,5})"

# Releases are lists, not cards (e.g. "SL MCSL 386: Iteration backlog"), and
# only MCSL exposes a plain-integer release token — FedEx/AuPost use dotted
# versions ("v2.3.123") that don't fit this shape, so they fall through to
# tier 3 for now. The captured text is passed through verbatim; release_status
# on the worker side strips non-digits itself.
# The prefix is optional: "how many cards assigned to me in 385" is how the
# question actually gets asked out loud. Three digits minimum, so a count like
# "29 cards" cannot be read as a release. The worker validates the number
# against the real release lists and says so if it matches none.
# Two alternatives:
#   MCSL-prefixed  -> "MCSL 386", "MCSL-385"
#   bare number    -> "in 385", because that is how the question gets asked
#                     out loud. Guarded so it cannot eat the digits out of a
#                     card id: not preceded by a letter or hyphen (ZI-691),
#                     not followed by one, and 3 digits minimum so a count
#                     like "29 cards" is never read as a release.
_RELEASE_ID = (
    r"(?P<release>MCSL[\s-]?\d{2,4}"
    r"|(?<![A-Za-z])(?<!-)\b\d{3,4}\b(?![-\d]))"
)


def _dm_params(m: re.Match[str]) -> dict:
    return {"person": m.group("person").strip(), "text": m.group("body").strip()}


def _body_params(m: re.Match[str]) -> dict:
    return {"text": m.group("body").strip()}


def _person_params(m: re.Match[str]) -> dict:
    return {"person": m.group("person").strip()}


def _app_params(m: re.Match[str]) -> dict:
    return {"app": m.group("app").strip(" .?")}


def _channel_params(m: re.Match[str]) -> dict:
    return {"channel": m.group("channel").strip("# "), "text": m.group("body").strip()}


def _release_params(m: re.Match[str]) -> dict:
    return {"release": m.group("release")}


# Order is precedence, not priority: match() returns the first entry whose
# pattern matches, not the best or most specific one. Earlier entries win
# when an utterance could plausibly match more than one intent. release_status
# is listed first: "status of MCSL 386" also satisfies card_status's pattern
# (both share the "status" trigger and an ID-shaped token), and a release
# should win that tie since "MCSL 386" names a list, not a card.
INTENTS: list[Intent] = [
    Intent(
        name="send_dm",
        method="send_dm",
        # "DM Ashok saying ...", "message Madan Kumar that ...",
        # "tell Ashok that ...", "ping Ashok saying ...".
        # Listed first: a DM body can contain words like "status of ZI-691"
        # that would otherwise match a read intent and silently drop the send.
        pattern=re.compile(
            r"\b(?:dm|d\.?\s?m\.?|message|tell|ping)\s+"
            r"(?P<person>[A-Za-z][A-Za-z .']{1,30}?)\s+"
            r"(?:saying|to say|that|:)\s+(?P<body>.+)$",
            re.I,
        ),
        extract=_dm_params,
    ),
    Intent(
        name="send_dm_incomplete",
        method="__slots__",
        # "send a message", "I need to DM someone", "message somebody".
        # After the full send_dm pattern, so a complete request never lands
        # here. This one starts the ask-for-the-missing-pieces dialogue.
        pattern=re.compile(
            r"\b(?:send|write|shoot)\b.{0,12}\b(?:message|msg|dm)\b"
            r"|\bi\s+(?:need|want)\s+to\s+(?:send|dm|message)\b"
            r"|^\W*(?:dm|message)\W*$",
            re.I,
        ),
        extract=_no_params,
    ),
    Intent(
        name="remind_me",
        method="__local__",
        # "remind me to check ZI-653 orders at 4"
        pattern=re.compile(
            r"\bremind\s+me\s+(?:to\s+)?(?P<body>.+)$", re.I
        ),
        extract=_body_params,
    ),
    Intent(
        name="add_note",
        method="__local__",
        # "note that the GLS store needs re-toggling", "remember that ..."
        pattern=re.compile(
            r"\b(?:note|remember)\s+(?:that\s+)?(?P<body>.+)$", re.I
        ),
        extract=_body_params,
    ),
    Intent(
        name="list_notes",
        method="__local__",
        pattern=re.compile(r"\b(?:what|any)\b.{0,15}\bnotes?\b|\bmy notes\b", re.I),
        extract=_no_params,
    ),
    Intent(
        name="read_replies",
        method="read_replies",
        # "did Ashok reply", "any reply from Ashok", "what did Ashok say".
        # Before dev_status, whose "who built/wrote" trigger is narrower but
        # whose intent overlaps conceptually.
        pattern=re.compile(
            r"\b(?:did\s+(?P<person>[A-Za-z][A-Za-z .']{1,30}?)\s+(?:reply|respond|answer|say)"
            r"|(?:any\s+)?(?:reply|replies|response)\s+from\s+(?P<person2>[A-Za-z][A-Za-z .']{1,30}?)"
            r"|what\s+did\s+(?P<person3>[A-Za-z][A-Za-z .']{1,30}?)\s+say)\b",
            re.I,
        ),
        extract=lambda m: {"person": (m.group("person") or m.group("person2") or m.group("person3") or "").strip()},
    ),
    Intent(
        name="post_channel",
        method="post_channel",
        # "post in qa-team saying the toggle is off",
        # "comment in #mcsl-qa that ZI-667 is verified"
        # A channel post is not a DM. send_dm's pattern needs a person before
        # the "saying", so "post in qa-team saying ..." cannot match it -- but
        # the distinction matters enough to test rather than assume.
        pattern=re.compile(
            r"\b(?:post|comment|write)\b[^.]{0,20}?\bin\s+#?(?P<channel>[a-z0-9][a-z0-9._-]{1,40})\s+"
            r"(?:saying|to say|that|:)\s+(?P<body>.+)$",
            re.I,
        ),
        extract=_channel_params,
    ),
    Intent(
        name="open_app",
        method="__local__",
        # "open slack", "launch vs code", "open the terminal"
        pattern=re.compile(
            r"\b(?:open|launch|start|bring up)\s+(?P<app>[A-Za-z][A-Za-z0-9 .+-]{1,30})$",
            re.I,
        ),
        extract=_app_params,
    ),
    Intent(
        name="card_devs",
        method="card_devs",
        # "who is the dev for that", "who is working on ZI-667"
        # Before card_status, which would otherwise answer with the list and
        # assignee when the question is specifically about people.
        pattern=re.compile(
            rf"\b(?:who(?:'?s| is| are)?)\b.{{0,30}}?"
            rf"\b(?:dev|developer|devs|working on|assigned|owner)\b"
            rf"(?:.{{0,30}}?{_CARD_REF})?",
            re.I,
        ),
        extract=_card_params,
    ),
    Intent(
        name="test_plan",
        method="test_plan",
        # "what is the testing plan for that", "test cases for ZI-667"
        pattern=re.compile(
            rf"\b(?:test(?:ing)?\s+(?:plan|cases?|scenarios?)|tcs?|test\s+plan)\b"
            rf"(?:.{{0,30}}?{_CARD_REF})?",
            re.I,
        ),
        extract=_card_params,
    ),
    Intent(
        name="card_status",
        method="card_status",
        # Listed ahead of the release intents: "card 667 in MCSL-380" names
        # both, and the card is the specific thing being asked about --
        # answering with all 31 cards in the release drops the question.
        #
        # Ordinary verbs count. Real transcripts: "go through card ZI-667",
        # "can I go through with the card 667 in MCSL-380".
        pattern=re.compile(
            rf"\b(?:status|state|what'?s? happening|go\s+through|open|check|"
            rf"look\s+at|read|show|tell\s+me\s+about|about)\b.{{0,40}}?{_CARD_REF}",
            re.I,
        ),
        extract=_card_params,
    ),
    Intent(
        name="my_release_cards",
        method="my_release_cards",
        # "in MCSL 385 how many tickets are assigned to me", "my tickets in
        # MCSL 386", "which tickets are mine in MCSL 385".
        #
        # Must precede release_status, which matches the release alone and
        # would otherwise answer with every card in it — honouring the scope
        # but silently dropping the "assigned to me" filter.
        pattern=re.compile(
            rf"(?=.*{_RELEASE_ID})"
            rf"(?=.*\b(?:assigned\s+to\s+me|to\s+me|my|mine|i\s+have)\b)",
            re.I,
        ),
        extract=_release_params,
    ),
    Intent(
        name="release_status",
        method="release_status",
        # Spoken phrasing varies far more than written. Real transcripts seen
        # so far: "what's in MCSL 386", "What is an MCSL-386?", "in MCSL-385
        # see how many cards". Accept a bare release mention too — saying a
        # release number at all almost always means "what's in it".
        pattern=re.compile(
            rf"\b(?:status|state|what'?s?|what\s+is|which|show|tell|release|iteration|backlog|in)\b"
            rf".{{0,40}}?{_RELEASE_ID}",
            re.I,
        ),
        extract=_release_params,
    ),
    Intent(
        name="my_tasks",
        method="my_tasks",
        pattern=re.compile(r"\b(?:my|the)\s+tasks?\b|\bwhat am i (?:working on|doing)\b", re.I),
        extract=_no_params,
    ),
    Intent(
        name="dev_status",
        method="dev_status",
        pattern=re.compile(r"\bwho (?:built|wrote|developed)\b|\bdev(?:eloper)? status\b", re.I),
        extract=_query_params,
    ),
    Intent(
        name="customer_issues",
        method="customer_issues",
        pattern=re.compile(
            r"\b(?:any )?(?:customer|merchant|support)\s+(?:issues?|tickets?|problems?)\b"
            r"|\bopen (?:zendesk|ZI) issues?\b",
            re.I,
        ),
        extract=_no_params,
    ),
]


def match(text: str) -> tuple[Intent, dict] | None:
    """Return the first matching intent and its extracted params."""
    for intent in INTENTS:
        m = intent.pattern.search(text)
        if m:
            return intent, intent.extract(m)
    return None

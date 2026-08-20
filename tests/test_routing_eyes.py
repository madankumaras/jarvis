"""Which of "look at this" and "go through that card" wins, and when.

The two phrasings overlap almost completely -- "go through X and tell me the
issue" is a Trello lookup when X is a card and a file read when X is a doc -- so
these are the tests that keep them apart.
"""
import pytest

from jarvis.router.intents import match


def named(text):
    found = match(text)
    return found[0].name if found else None


def params(text):
    found = match(text)
    return found[1] if found else {}


# --- looking at the screen ------------------------------------------------

@pytest.mark.parametrize("said", [
    "look at this",
    "look at my screen",
    "take a look",
    "see this",
    "what's on my screen",
    "what am I looking at",
    "is this correct",
    "is that right",
    "what do you see",
])
def test_asking_about_the_screen_looks_at_the_screen(said):
    assert named(said) == "look_at_screen"


def test_the_motivating_sentence_looks_at_the_screen():
    """Asked for verbatim: "see this req is going for that card is this
    correct". It names a card, so every card intent would happily claim it --
    but the thing being asked about is the request on screen."""
    assert named("see this request is going for that card is this correct") == "look_at_screen"


def test_the_whole_sentence_becomes_the_question():
    """"Is this correct?" carries no information on its own."""
    assert "weight" in params("see this request, is the weight correct")["question"]


# --- reading a document --------------------------------------------------

@pytest.mark.parametrize("said", [
    "go through the doc and tell me what it says",
    "go through this document",
    "read the PDF",
    "summarise this page",
    "check the spec",
    "walk through the readme",
    "read this file",
    "go through the support guide",
    "summarize the report",
])
def test_asking_about_a_document_reads_the_document(said):
    assert named(said) == "read_document"


def test_an_explicit_doc_is_read_rather_than_photographed():
    """"Look at this doc" is ambiguous between the two. Reading the file wins:
    a screenshot only sees the visible page and cannot scroll."""
    assert named("look at this doc") == "read_document"


# --- the collision that matters -------------------------------------------

@pytest.mark.parametrize("said,expected", [
    ("go through ZI-667 and tell me the issue", "card_status"),
    ("go through the doc and tell me the issue", "read_document"),
    ("go through ZI-667", "card_status"),
    ("read the doc", "read_document"),
])
def test_a_card_and_a_document_are_told_apart(said, expected):
    assert named(said) == expected


def test_a_bare_card_reference_is_not_claimed_by_the_document_intent():
    """"go through that card" carries no id: it is resolved from conversation
    state before matching. What matters here is that the document intent does
    not grab it first and go looking for a file."""
    assert named("go through that card and tell me the issue") != "read_document"


def test_a_card_lookup_is_not_hijacked_by_the_screen_intent():
    assert named("status of ZI-691") == "card_status"
    assert named("what cards are assigned to me") != "look_at_screen"


def test_reading_replies_is_not_reading_a_document():
    """"read" appears in both; the document intent needs a document noun."""
    assert named("did Ashok reply") == "read_replies"


def test_a_dm_mentioning_the_screen_still_sends():
    """A DM body can say anything, including "look at this"."""
    assert named("dm Ashok saying look at this screen") == "send_dm"


# --- the phrasing asked for most often -----------------------------------

@pytest.mark.parametrize("said", [
    "what cards assigned to me",
    "what cards are assigned to me",
    "which cards are assigned to me",
    "what tickets are assigned to me",
    "what's assigned to me",
    "anything assigned to me",
    "my cards",
])
def test_every_way_of_asking_what_is_mine_routes_the_same(said):
    """Regression: "what cards ARE assigned to me" matched nothing at all,
    while "what cards assigned to me" worked -- the reverse of what anyone
    would guess, on the question asked most often."""
    assert named(said) == "my_work"


@pytest.mark.parametrize("said", [
    "in 385 how many tickets are assigned to me",
    "in MCSL 385 which tickets are assigned to me",
])
def test_naming_a_release_still_scopes_to_that_release(said):
    assert named(said) == "my_release_cards"

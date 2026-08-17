import pytest

from jarvis.router.confirm import Confirmation, PendingAction, interpret


@pytest.mark.parametrize(
    "text",
    ["ok", "okay", "yes", "yeah", "yep", "yup", "sure", "send", "send it",
     "go", "go ahead", "do it", "confirm", "OK.", "  yes  "],
)
def test_affirmatives(text):
    assert interpret(text) == "yes"


@pytest.mark.parametrize(
    "text",
    ["no", "nope", "nah", "cancel", "stop", "don't", "do not",
     "forget it", "nevermind", "never mind"],
)
def test_negatives(text):
    assert interpret(text) == "no"


@pytest.mark.parametrize(
    "text",
    ["", "   ", "uh", "hmm", "what", "the toggle is still off", "maybe",
     "ok so actually make it the GLS store", "yes but change the name"],
)
def test_anything_else_is_unclear(text):
    """Only a bare affirmative is consent. 'ok so actually...' is a correction."""
    assert interpret(text) == "unclear"


def test_unclear_cancels_because_ambiguity_is_never_consent():
    c = Confirmation(PendingAction("send_dm", {"user_id": "U1", "text": "hi"}, "Send?", ""))
    assert c.resolve("hmm") is False
    assert c.settled is True


def test_yes_confirms():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""))
    assert c.resolve("ok") is True
    assert c.settled is True


def test_no_cancels():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""))
    assert c.resolve("cancel") is False


def test_expired_confirmation_cannot_be_resolved():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""), timeout_seconds=0)
    assert c.expired() is True
    assert c.resolve("ok") is False


def test_a_settled_confirmation_cannot_be_resolved_twice():
    """Guards against a stray 'ok' later in the session re-firing an action."""
    c = Confirmation(PendingAction("send_dm", {}, "?", ""))
    assert c.resolve("ok") is True
    assert c.resolve("ok") is False


def test_a_cancelled_confirmation_cannot_be_revived():
    c = Confirmation(PendingAction("send_dm", {}, "?", ""))
    assert c.resolve("no") is False
    assert c.resolve("ok") is False

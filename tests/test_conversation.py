import pytest

from jarvis.router.conversation import Conversation, SlotFill, is_closing


# ---- closing the conversation ---------------------------------------

@pytest.mark.parametrize("text", [
    "that's all", "thats it", "thanks", "thank you", "nothing", "no thanks",
    "bye", "we're done", "done", "stop", "That's all.",
])
def test_closers(text):
    assert is_closing(text) is True


@pytest.mark.parametrize("text", [
    "", "status of ZI-691", "done with the toggle", "stop the rate shopping toggle",
])
def test_non_closers(text):
    """"done with the toggle" is a statement, not a goodbye."""
    assert is_closing(text) is False


# ---- remembering what was mentioned ---------------------------------

def test_remembers_the_last_card():
    c = Conversation()
    c.remember("card_status", {"card_id": "ZI-667"})
    assert c.last_card == "ZI-667"


def test_a_later_mention_replaces_the_earlier_one():
    c = Conversation()
    c.remember("card_status", {"card_id": "ZI-667"})
    c.remember("card_status", {"card_id": "ZI-691"})
    assert c.last_card == "ZI-691"


def test_remembering_ignores_absent_fields():
    c = Conversation()
    c.remember("card_status", {"card_id": "ZI-667"})
    c.remember("my_tasks", {})
    assert c.last_card == "ZI-667"


# ---- referring back --------------------------------------------------

@pytest.mark.parametrize("said", [
    "go through that card",
    "open the card",
    "check that one",
    "what about it",
])
def test_a_reference_resolves_to_the_last_card(said):
    c = Conversation(last_card="ZI-667")
    assert "ZI-667" in c.resolve(said)


def test_a_person_reference_resolves():
    c = Conversation(last_person="Ashok")
    assert "Ashok" in c.resolve("send him a message")


def test_text_without_references_is_untouched():
    c = Conversation(last_card="ZI-667")
    assert c.resolve("what are my tasks") == "what are my tasks"


def test_a_reference_with_nothing_remembered_is_flagged():
    """Better to say "which card?" than to guess."""
    c = Conversation()
    assert c.unresolved_reference("go through that card") is True


def test_a_reference_with_something_remembered_is_not_flagged():
    c = Conversation(last_card="ZI-667")
    assert c.unresolved_reference("go through that card") is False


# ---- slot filling ----------------------------------------------------

def test_a_slotfill_asks_for_what_is_missing():
    sf = SlotFill(action="send_dm", needs=["person", "text"])
    assert "who" in sf.next_question().lower()


def test_filling_advances_to_the_next_question():
    sf = SlotFill(action="send_dm", needs=["person", "text"])
    sf.fill("Ashok")
    assert sf.slots == {"person": "Ashok"}
    assert "say" in sf.next_question().lower()


def test_a_complete_slotfill_has_no_question():
    sf = SlotFill(action="send_dm", needs=["person"])
    sf.fill("Ashok")
    assert sf.complete is True
    assert sf.next_question() == ""


def test_filling_an_empty_answer_does_not_advance():
    """Silence must not be recorded as the answer."""
    sf = SlotFill(action="send_dm", needs=["person", "text"])
    sf.fill("   ")
    assert sf.needs == ["person", "text"]


def test_filling_a_complete_slotfill_is_harmless():
    sf = SlotFill(action="send_dm", needs=[])
    sf.fill("stray words")
    assert sf.slots == {}


def test_conversation_reports_when_it_is_waiting_on_an_answer():
    c = Conversation()
    assert c.expects_answer() is False
    c.slots = SlotFill(action="send_dm", needs=["person"])
    assert c.expects_answer() is True
    c.slots.fill("Ashok")
    assert c.expects_answer() is False

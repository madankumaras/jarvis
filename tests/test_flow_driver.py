"""Driving a flow from utterances, and how it interacts with the router."""
import pytest

from jarvis.flow import driver
from jarvis.flow.engine import Flow
from jarvis.flow.spec import Step, Workflow
from jarvis.router.conversation import Conversation
from jarvis.router.core import handle_transcript
from jarvis.types import Vocab


class FakeWorker:
    def __init__(self, methods=("card_status", "my_work")):
        self.methods = list(methods)
        self.calls = []

    def capabilities(self):
        return self.methods

    def call(self, method, **params):
        self.calls.append((method, params))
        return {"speech": "ok", "detail": ""}


def _wf(*steps):
    return Workflow("t", ("go now",), tuple(steps))


def test_a_trigger_starts_a_flow_and_speaks_its_first_line():
    c = Conversation(last_card="ZI-667")
    resp = handle_transcript("new build deployed", Vocab(), FakeWorker(), conversation=c)
    assert c.in_flow() is True
    assert "pulling" in resp.speech.lower()
    assert resp.tier == 3


def test_a_running_flow_owns_the_turn():
    """Mid-flow, the next utterance answers the flow -- it is not a new
    command."""
    c = Conversation()
    c.flow = Flow(workflow=_wf(Step("agent", "check {store}", needs=("store",))))
    w = FakeWorker()
    resp = handle_transcript("the GLS packaging store", Vocab(), w, conversation=c)

    assert c.flow.slots["store"] == "the GLS packaging store"
    assert w.calls == [], "a slot answer must not be routed as a command"
    assert "gls packaging" in resp.detail.lower()


def test_yes_advances_an_offer():
    c = Conversation()
    c.flow = Flow(workflow=_wf(Step("agent", "one"), Step("agent", "two", offer="Next?")))
    c.flow.record("one done")
    resp = handle_transcript("yes", Vocab(), FakeWorker(), conversation=c)
    assert resp.detail == "two"


@pytest.mark.parametrize("said", ["no", "not now", "hmm", "stop it there"])
def test_anything_but_yes_stops_the_flow(said):
    c = Conversation()
    c.flow = Flow(workflow=_wf(Step("agent", "one"), Step("agent", "two", offer="Next?")))
    c.flow.record("one done")
    resp = handle_transcript(said, Vocab(), FakeWorker(), conversation=c)
    assert c.flow.finished is True
    assert "stopping" in resp.speech.lower() or resp.ends


def test_closing_the_conversation_abandons_the_flow():
    c = Conversation()
    c.flow = Flow(workflow=_wf(Step("agent", "one"), Step("agent", "two", offer="Next?")))
    c.flow.record("one done")
    resp = handle_transcript("that's all", Vocab(), FakeWorker(), conversation=c)
    assert resp.ends is True
    assert c.flow.finished is True


def test_a_flow_seeds_the_card_from_the_conversation():
    """You already said which card; the flow should not ask again."""
    c = Conversation(last_card="ZI-686")
    handle_transcript("prep that card", Vocab(), FakeWorker(), conversation=c)
    assert c.flow.slots["card"] == "ZI-686"


def test_a_flow_asks_for_the_card_when_none_is_known():
    c = Conversation()
    resp = handle_transcript("prep that card", Vocab(), FakeWorker(), conversation=c)
    assert resp.awaiting is True
    assert "which card" in resp.speech.lower()


def test_a_workflow_trigger_beats_an_ordinary_intent():
    """"check the toggles" is a flow, not a question."""
    c = Conversation(last_card="ZI-667")
    handle_transcript("check the toggles", Vocab(), FakeWorker(), conversation=c)
    assert c.in_flow() is True
    assert c.flow.workflow.name == "toggle_request"


def test_an_ordinary_question_does_not_start_a_flow():
    c = Conversation()
    w = FakeWorker()
    handle_transcript("status of ZI-691", Vocab(), w, conversation=c)
    assert c.in_flow() is False
    assert w.calls[0][0] == "card_status"


def test_a_finished_flow_releases_the_turn():
    c = Conversation()
    c.flow = Flow(workflow=_wf(Step("agent", "one")))
    c.flow.record("done")
    w = FakeWorker()
    handle_transcript("status of ZI-691", Vocab(), w, conversation=c)
    assert w.calls[0][0] == "card_status"


def test_a_malformed_catalogue_disables_flows_rather_than_breaking_turns(monkeypatch):
    import jarvis.router.core as core

    monkeypatch.setattr(core, "_CATALOGUE", None)
    monkeypatch.setattr(core, "load_workflows", lambda *a: (_ for _ in ()).throw(ValueError("bad")))
    c = Conversation()
    w = FakeWorker()
    resp = handle_transcript("status of ZI-691", Vocab(), w, conversation=c)
    assert resp.ok is True
    assert w.calls[0][0] == "card_status"

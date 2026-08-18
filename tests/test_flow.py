"""Named multi-step flows.

The engine holds position, not behaviour, so all of this runs with no I/O.
"""
import pytest

from jarvis.flow.engine import ASK, DONE, OFFER, RUN, Flow
from jarvis.flow.spec import Catalogue, Step, Workflow, load


# ---- the catalogue ---------------------------------------------------

def test_the_shipped_catalogue_loads():
    cat = load()
    assert {w.name for w in cat.workflows} >= {"new_build", "prep_card", "toggle_request"}


def test_every_shipped_step_after_the_first_has_an_offer():
    """A misheard workflow name must not commit you to several actions."""
    for wf in load().workflows:
        for step in wf.steps[1:]:
            assert step.offer, f"{wf.name}: a later step has no offer"


def test_a_trigger_phrase_is_matched_inside_a_sentence():
    cat = load()
    assert cat.match("ok the new build is deployed now").name == "new_build"


def test_the_longest_trigger_wins():
    cat = Catalogue(workflows=[
        Workflow("short", ("build",), (Step("agent", "x"),)),
        Workflow("long", ("new build deployed",), (Step("agent", "x"),)),
    ])
    assert cat.match("the new build deployed today").name == "long"


def test_an_unrelated_sentence_matches_nothing():
    assert load().match("what is the status of ZI-667") is None
    assert load().match("") is None


def test_a_workflow_without_triggers_is_rejected(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("workflows:\n  - name: x\n    steps:\n      - request: do it\n")
    with pytest.raises(ValueError, match="triggers"):
        load(p)


def test_a_step_without_a_request_is_rejected(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("workflows:\n  - name: x\n    triggers: [go]\n    steps:\n      - say: hi\n")
    with pytest.raises(ValueError, match="request"):
        load(p)


def test_an_unknown_kind_is_rejected(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("workflows:\n  - name: x\n    triggers: [go]\n    steps:\n"
                 "      - kind: magic\n        request: do it\n")
    with pytest.raises(ValueError, match="kind"):
        load(p)


def test_a_missing_file_is_an_empty_catalogue_not_a_crash(tmp_path):
    assert load(tmp_path / "nope.yaml").workflows == []


# ---- the engine ------------------------------------------------------

def _wf(*steps):
    return Workflow("t", ("go",), tuple(steps))


def test_the_first_step_runs_without_an_offer():
    f = Flow(workflow=_wf(Step("agent", "do {card}", say="starting")))
    f.seed(card="ZI-667")
    ins = f.next_instruction()
    assert ins.action == RUN
    assert ins.request == "do ZI-667"
    assert ins.speech == "starting"


def test_a_later_step_is_offered_before_it_runs():
    f = Flow(workflow=_wf(Step("agent", "one"), Step("agent", "two", offer="Next?")))
    f.record("one done")
    assert f.next_instruction().action == OFFER
    f.accept()
    assert f.next_instruction().action == RUN


def test_a_step_is_not_offered_twice():
    f = Flow(workflow=_wf(Step("agent", "one"), Step("agent", "two", offer="Next?")))
    f.record("one done")
    f.accept()
    assert f.next_instruction().action == RUN


def test_declining_ends_the_whole_flow():
    """Declining "shall I write the AC?" means stop -- not skip to the test
    cases that depend on it."""
    f = Flow(workflow=_wf(Step("agent", "one"), Step("agent", "two", offer="Next?"),
                          Step("agent", "three", offer="And?")))
    f.record("one done")
    f.decline()
    assert f.finished is True


def test_a_missing_slot_is_asked_for():
    f = Flow(workflow=_wf(Step("agent", "check {store}", needs=("store",))))
    ins = f.next_instruction()
    assert ins.action == ASK
    assert ins.slot == "store"
    assert "store" in ins.speech.lower()


def test_answering_a_slot_lets_the_step_run():
    f = Flow(workflow=_wf(Step("agent", "check {store}", needs=("store",))))
    f.answer_slot("store", "gls-packaging")
    ins = f.next_instruction()
    assert ins.action == RUN
    assert ins.request == "check gls-packaging"


def test_an_empty_answer_does_not_fill_the_slot():
    f = Flow(workflow=_wf(Step("agent", "check {store}", needs=("store",))))
    f.answer_slot("store", "   ")
    assert f.next_instruction().action == ASK


def test_a_placeholder_with_no_value_is_asked_for_even_without_needs():
    """Otherwise "{card}" is spoken literally."""
    f = Flow(workflow=_wf(Step("agent", "read {card}")))
    assert f.next_instruction().action == ASK


def test_seeding_fills_from_the_conversation():
    f = Flow(workflow=_wf(Step("agent", "read {card}", needs=("card",))))
    f.seed(card="ZI-686", person="Ashok")
    assert f.next_instruction().action == RUN
    assert f.slots["person"] == "Ashok"


def test_seeding_does_not_overwrite_an_answered_slot():
    f = Flow(workflow=_wf(Step("agent", "read {card}")))
    f.answer_slot("card", "ZI-1")
    f.seed(card="ZI-999")
    assert f.slots["card"] == "ZI-1"


def test_seeding_ignores_empty_values():
    f = Flow(workflow=_wf(Step("agent", "read {card}", needs=("card",))))
    f.seed(card="", person=None)
    assert f.next_instruction().action == ASK


def test_the_flow_finishes_after_the_last_step():
    f = Flow(workflow=_wf(Step("agent", "only")))
    f.record("done")
    assert f.finished is True
    assert f.next_instruction().action == DONE


def test_results_are_kept_in_order():
    f = Flow(workflow=_wf(Step("agent", "one"), Step("agent", "two")))
    f.record("first"); f.record("second")
    assert f.results == ["first", "second"]

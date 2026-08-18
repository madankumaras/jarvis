"""What the board's labels mean for QA.

Every expectation here was read off the live board, not invented: MCSL 385 had
17 cards at QA with only 2 verified, 7 closed by support, 5 spilled over and
5 marked duplicate.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))

from qa_labels import ACTIONABLE, TERMINAL, classify, progress  # noqa: E402


def test_qa_verified_beats_qa():
    """A verified card keeps its QA label, so order decides. Real example:
    ZI-632 carries DEV, Dev Done, QA and QA_VERIFIED."""
    st = classify(["DEV", "Dev Done", "QA", "QA_VERIFIED"])
    assert st.state == "verified"
    assert st.done is True
    assert st.actionable is False


def test_qa_without_verified_is_work():
    st = classify(["DEV", "Dev Done", "QA"])
    assert st.state == "in_qa"
    assert st.actionable is True
    assert st.meaning == "needs testing"


def test_closed_by_support_beats_everything():
    """ZI-648 carries both Closed By Support and QA. It must not be offered as
    testing work."""
    st = classify(["SL: Closed By Support", "QA"])
    assert st.state == "closed_by_support"
    assert st.actionable is False
    assert "no testing needed" in st.note


def test_a_duplicate_in_qa_is_flagged_as_a_sanity_check():
    """The case that matters: a duplicate may already have been tested in an
    earlier release, so it needs a sanity pass rather than the full plan."""
    st = classify(["DEV", "Dev Done", "SL: 🔄 Duplicate", "QA"])
    assert st.state == "in_qa"
    assert st.duplicate is True
    assert "sanity check" in st.note


def test_a_verified_duplicate_does_not_ask_for_a_sanity_check():
    st = classify(["SL: 🔄 Duplicate", "QA", "QA_VERIFIED"])
    assert st.state == "verified"
    assert st.note == ""


def test_spill_over_is_reported():
    st = classify(["Spill Over"])
    assert st.state == "spill_over"
    assert "earlier release" in st.note


def test_dev_done_without_qa_is_ready_for_qa():
    st = classify(["DEV", "Dev Done"])
    assert st.state == "dev_done"
    assert st.actionable is True


def test_qa_reported_is_back_with_dev_but_still_ours():
    st = classify(["QA Reported"])
    assert st.state == "qa_reported"
    assert st.actionable is True


def test_no_labels_is_unlabelled_not_a_crash():
    assert classify([]).state == "unlabelled"
    assert classify(None).state == "unlabelled"


def test_unrelated_labels_do_not_invent_a_state():
    assert classify(["MCSL", "SL: 🚚 UPS", "CONFIDENCE:HIGH"]).state == "unlabelled"


def test_terminal_and_actionable_do_not_overlap():
    assert not (TERMINAL & ACTIONABLE)


# ---- release progress ------------------------------------------------

def _states(*label_sets):
    return [classify(list(s)) for s in label_sets]


def test_support_closed_cards_are_excluded_from_the_denominator():
    """Counting them as outstanding would make every release look unfinished
    forever -- 385 has 7 of them."""
    p = progress(_states(["QA", "QA_VERIFIED"], ["SL: Closed By Support"]))
    assert p["testable"] == 1
    assert p["verified"] == 1
    assert p["skipped"] == 1
    assert p["complete"] is True


def test_spill_over_is_also_outside_the_denominator():
    p = progress(_states(["QA", "QA_VERIFIED"], ["Spill Over"]))
    assert p["testable"] == 1
    assert p["spilled"] == 1


def test_a_release_with_one_verified_and_two_pending_is_in_progress():
    """Exactly the shape Madan described: 3 cards, 1 verified -> in progress."""
    p = progress(_states(["QA", "QA_VERIFIED"], ["QA"], ["QA"]))
    assert p["verified"] == 1
    assert p["testable"] == 3
    assert p["outstanding"] == 2
    assert p["complete"] is False


def test_all_verified_is_complete():
    p = progress(_states(["QA", "QA_VERIFIED"], ["QA", "QA_VERIFIED"]))
    assert p["complete"] is True
    assert p["outstanding"] == 0


def test_an_empty_release_is_not_complete():
    """Nothing to verify is not the same as finished."""
    assert progress([])["complete"] is False


def test_a_release_of_only_skips_is_not_complete():
    assert progress(_states(["SL: Closed By Support"]))["complete"] is False


def test_duplicates_are_counted_separately_from_state():
    p = progress(_states(["QA", "SL: 🔄 Duplicate"], ["QA"]))
    assert p["duplicates"] == 1
    assert p["outstanding"] == 2

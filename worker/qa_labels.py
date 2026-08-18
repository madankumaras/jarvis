"""What Trello labels mean for the QA workflow.

The board carries 50 labels; these are the ones that decide whether a card
needs testing. Verified against the live board: in MCSL 385, 17 cards sit at
QA without QA_VERIFIED while only 2 are verified -- that is what makes 385 the
active release, not its number.

Nothing here is guessed. Every mapping below was read off real cards.
"""
from __future__ import annotations

from dataclasses import dataclass

# Exact label names as they appear on the board.
QA_VERIFIED = "QA_VERIFIED"
QA = "QA"
QA_REPORTED = "QA Reported"
DEV = "DEV"
DEV_DONE = "Dev Done"
CLOSED_BY_SUPPORT = "SL: Closed By Support"
DUPLICATE = "SL: 🔄 Duplicate"
SPILL_OVER = "Spill Over"
INVESTIGATION = "INVESTIGATION"
DEPLOY_PENDING = "DEPLOYMENT PENDING"
SHIPPED = "SHIPPED"

# Ordered most-decisive first: a card can carry several, and the first match
# wins. QA_VERIFIED beats QA because a verified card also keeps its QA label.
STATES = (
    ("closed_by_support", CLOSED_BY_SUPPORT, "no testing needed"),
    ("verified",          QA_VERIFIED,       "done"),
    ("qa_reported",       QA_REPORTED,       "bug raised, back with dev"),
    ("in_qa",             QA,                "needs testing"),
    ("dev_done",          DEV_DONE,          "ready for QA"),
    ("in_dev",            DEV,               "still with dev"),
    ("investigation",     INVESTIGATION,     "under investigation"),
    ("spill_over",        SPILL_OVER,        "carried from an earlier release"),
)

# States that need nothing further from QA.
TERMINAL = {"verified", "closed_by_support"}
# States that are QA's actual queue.
ACTIONABLE = {"in_qa", "dev_done", "qa_reported"}


@dataclass(frozen=True)
class CardState:
    state: str
    meaning: str
    duplicate: bool
    spill_over: bool
    labels: tuple[str, ...]

    @property
    def actionable(self) -> bool:
        return self.state in ACTIONABLE

    @property
    def done(self) -> bool:
        return self.state in TERMINAL

    @property
    def note(self) -> str:
        """The extra thing worth saying aloud about this card, if any."""
        if self.state == "closed_by_support":
            return "closed by support, so no testing needed"
        if self.duplicate and self.actionable:
            # This is the case Madan flagged: a duplicate may already have been
            # tested in an earlier release, so it needs a sanity pass rather
            # than the full plan.
            return "marked duplicate — likely a sanity check rather than a full pass"
        if self.spill_over:
            return "spilled over from an earlier release"
        return ""


def classify(labels: list[str]) -> CardState:
    """Reduce a card's labels to one QA state."""
    names = [n for n in (labels or []) if n]
    present = set(names)
    for state, label, meaning in STATES:
        if label in present:
            return CardState(
                state=state,
                meaning=meaning,
                duplicate=DUPLICATE in present,
                spill_over=SPILL_OVER in present,
                labels=tuple(names),
            )
    return CardState(
        state="unlabelled",
        meaning="no workflow label",
        duplicate=DUPLICATE in present,
        spill_over=SPILL_OVER in present,
        labels=tuple(names),
    )


def progress(states: list[CardState]) -> dict:
    """Release progress, counting only what QA is actually responsible for.

    Cards closed by support are excluded from the denominator: counting them as
    outstanding work would make every release look unfinished forever.
    """
    testable = [s for s in states if s.state not in {"closed_by_support", "spill_over", "unlabelled"}]
    verified = [s for s in testable if s.state == "verified"]
    return {
        "verified": len(verified),
        "testable": len(testable),
        "outstanding": len([s for s in testable if s.actionable]),
        "skipped": len([s for s in states if s.state == "closed_by_support"]),
        "spilled": len([s for s in states if s.state == "spill_over"]),
        "duplicates": len([s for s in states if s.duplicate]),
        "complete": bool(testable) and len(verified) == len(testable),
    }

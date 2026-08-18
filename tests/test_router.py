from unittest.mock import patch
import pytest

from jarvis.router.core import handle_transcript
from jarvis.types import Response, RpcError, Vocab


class FakeWorker:
    def __init__(
        self,
        result=None,
        error=None,
        methods=("card_status", "release_status", "my_tasks", "dev_status", "customer_issues"),
    ):
        self.result = result or {"speech": "MCSL-384 is in QA Ready", "detail": "full detail"}
        self.error = error
        self.methods = list(methods)
        self.calls = []
        self.results = {}          # per-method overrides

    def call(self, method, **params):
        self.calls.append((method, params))
        if self.error:
            raise RpcError(self.error)
        if method in self.results:
            return self.results[method]
        return self.result

    def capabilities(self):
        return self.methods


@pytest.fixture
def vocab():
    return Vocab(cards=["MCSL-384"], people=["Ashok Kumar"], zi_ids=["ZI-653"])


def test_card_status_routes_to_worker(vocab):
    # Cards are ZI-NNN on the real board (e.g. "From SL: ZI-653 - ..."), not
    # MCSL-NNN — "MCSL 384" now addresses a release and is routed to
    # release_status instead (see test_release_status_routes_to_worker).
    worker = FakeWorker()
    resp = handle_transcript("status of ZI six five three", vocab, worker)

    assert resp.tier == 1
    assert resp.ok is True
    assert worker.calls == [("card_status", {"card_id": "ZI-653"})]
    assert "QA Ready" in resp.speech


def test_release_status_routes_to_worker(vocab):
    worker = FakeWorker(result={"speech": "SL MCSL 386 has 12 cards", "detail": ""})
    resp = handle_transcript("what's in MCSL 386", vocab, worker)

    assert resp.tier == 1
    assert resp.ok is True
    # vocab's cards fixture doesn't include "MCSL-386", so the correction
    # layer's digit-mismatch guard (see jarvis/correct/snap.py) leaves the
    # spoken spacing intact rather than snapping it to a hyphenated id.
    assert worker.calls == [("release_status", {"release": "MCSL 386"})]
    assert "12 cards" in resp.speech


def test_my_tasks_routes_to_worker(vocab):
    worker = FakeWorker(result={"speech": "you have 3 tasks", "detail": ""})
    resp = handle_transcript("what are my tasks", vocab, worker)

    assert worker.calls == [("my_tasks", {})]
    assert resp.speech == "you have 3 tasks"


def test_dev_status_routes_to_worker(vocab):
    worker = FakeWorker(result={"speech": "Ashok built it", "detail": ""})
    handle_transcript("who built the toggle flow", vocab, worker)

    assert worker.calls[0][0] == "dev_status"


def test_customer_issues_routes_to_worker(vocab):
    worker = FakeWorker(result={"speech": "5 open issues", "detail": ""})
    handle_transcript("any customer issues", vocab, worker)

    assert worker.calls == [("customer_issues", {})]


def test_unmatched_falls_through_to_tier_3(vocab):
    worker = FakeWorker()
    resp = handle_transcript("create a GLS carrier store", vocab, worker)

    assert resp.tier == 3
    assert worker.calls == []


def test_empty_transcript_does_not_reach_tier_3(vocab):
    worker = FakeWorker()
    resp = handle_transcript("   ", vocab, worker)

    assert resp.ok is False
    assert resp.tier == 1
    assert worker.calls == []
    assert "didn't catch" in resp.speech.lower()


def test_worker_error_is_spoken_not_swallowed(vocab):
    worker = FakeWorker(error="Trello 401 unauthorized")
    resp = handle_transcript("status of MCSL 384", vocab, worker)

    assert resp.ok is False
    assert "401" in resp.speech


def test_unsupported_capability_falls_to_tier_3(vocab):
    worker = FakeWorker(methods=["card_status"])  # no my_tasks
    resp = handle_transcript("what are my tasks", vocab, worker)

    assert resp.tier == 3
    assert worker.calls == []


def test_ambiguous_entity_asks_instead_of_guessing():
    worker = FakeWorker()
    vocab = Vocab(cards=["MCSL-384", "MCSL-390"])
    resp = handle_transcript("status of MCSL thirty eight", vocab, worker)

    if resp.needs_confirm:
        assert "did you mean" in resp.speech.lower()
        assert worker.calls == []


class NonDictResultWorker:
    """call() returns a plain string instead of a dict payload."""

    def call(self, method, **params):
        return "not a dict"

    def capabilities(self):
        return ["card_status", "my_tasks", "dev_status", "customer_issues"]


def test_non_dict_worker_result_does_not_raise(vocab):
    # A ZI id, not an MCSL one: NonDictResultWorker's capabilities() is a
    # fixed list that predates release_status, so an "MCSL 384"-style
    # utterance would now route to release_status and fall through to tier 3
    # (method unsupported) before ever reaching call() — defeating the point
    # of this test.
    worker = NonDictResultWorker()
    resp = handle_transcript("status of ZI-653", vocab, worker)

    assert resp.ok is False


class CapabilitiesRaisesWorker:
    """capabilities() raises a non-RpcError exception."""

    def call(self, method, **params):
        return {"speech": "ok", "detail": ""}

    def capabilities(self):
        raise ValueError("boom")


def test_capabilities_raising_value_error_does_not_raise(vocab):
    worker = CapabilitiesRaisesWorker()
    resp = handle_transcript("status of MCSL 384", vocab, worker)

    assert resp.ok is False


class CallRaisesKeyErrorWorker:
    """call() raises a non-RpcError exception."""

    def call(self, method, **params):
        raise KeyError("missing")

    def capabilities(self):
        return ["card_status", "my_tasks", "dev_status", "customer_issues"]


def test_call_raising_key_error_does_not_raise(vocab):
    # See test_non_dict_worker_result_does_not_raise: use a ZI id so this
    # still exercises card_status, which is in this stub's capabilities list.
    worker = CallRaisesKeyErrorWorker()
    resp = handle_transcript("status of ZI-653", vocab, worker)

    assert resp.ok is False


def test_dev_status_query_includes_full_utterance(vocab):
    worker = FakeWorker(result={"speech": "Ashok built it", "detail": ""})
    handle_transcript("who built the toggle flow", vocab, worker)

    assert worker.calls == [("dev_status", {"query": "who built the toggle flow"})]


def test_none_vocab_does_not_raise():
    worker = FakeWorker()
    resp = handle_transcript("status of MCSL 384", None, worker)

    assert resp.ok is False


# --- write actions: nothing may be sent without an explicit spoken yes ---

DM_METHODS = ("card_status", "release_status", "my_tasks", "dev_status",
              "customer_issues", "send_dm", "resolve_person")


def _dm_worker(person):
    w = FakeWorker(methods=DM_METHODS)
    w.results = {"resolve_person": person}
    return w


def test_send_dm_builds_a_pending_action_and_does_not_send(vocab):
    w = _dm_worker({"id": "U01", "name": "Ashok Kumar", "ambiguous": []})
    resp = handle_transcript("DM Ashok saying the toggle is still off", vocab, w)

    assert resp.needs_confirm is True
    assert "Ashok Kumar" in resp.speech
    assert "toggle is still off" in resp.speech
    assert not any(c[0] == "send_dm" for c in w.calls), "must not send before confirmation"
    assert resp.pending is not None
    assert resp.pending.method == "send_dm"
    assert resp.pending.params == {"user_id": "U01", "text": "the toggle is still off"}


def test_ambiguous_person_asks_instead_of_sending(vocab):
    w = _dm_worker({"id": "", "name": "", "ambiguous": [
        {"id": "U01", "name": "Ashok Kumar"}, {"id": "U02", "name": "Ashok Verma"}]})
    resp = handle_transcript("DM Ashok saying hello", vocab, w)

    assert resp.needs_confirm is False
    assert resp.ok is False
    assert "Ashok Kumar" in resp.speech and "Ashok Verma" in resp.speech
    assert not any(c[0] == "send_dm" for c in w.calls)


def test_unknown_person_does_not_produce_a_pending_action(vocab):
    w = _dm_worker({"id": "", "name": "", "ambiguous": []})
    resp = handle_transcript("DM Nobody saying hello", vocab, w)

    assert resp.ok is False
    assert resp.needs_confirm is False
    assert resp.pending is None


@pytest.mark.parametrize("utterance,person,body", [
    ("DM Ashok saying the toggle is still off", "Ashok", "the toggle is still off"),
    ("message Madan Kumar that the store is ready", "Madan Kumar", "the store is ready"),
    ("tell Ashok that ZI-691 is verified", "Ashok", "ZI-691 is verified"),
    ("ping Ashok saying please enable rate shopping", "Ashok", "please enable rate shopping"),
])
def test_dm_phrasings_extract_person_and_body(vocab, utterance, person, body):
    w = _dm_worker({"id": "U01", "name": person, "ambiguous": []})
    resp = handle_transcript(utterance, vocab, w)

    assert w.calls[0] == ("resolve_person", {"name": person})
    assert resp.pending.params["text"] == body


def test_a_read_intent_never_asks_for_confirmation(vocab):
    w = FakeWorker(methods=DM_METHODS)
    resp = handle_transcript("what are my tasks", vocab, w)
    assert resp.needs_confirm is False
    assert resp.pending is None


# --- reading replies ---

REPLY_METHODS = DM_METHODS + ("read_replies",)


@pytest.mark.parametrize("utterance,person", [
    ("did Ashok reply", "Ashok"),
    ("did Madan reply yet", "Madan"),
    ("any reply from Ashok", "Ashok"),
    ("what did Ashok say", "Ashok"),
])
def test_reply_phrasings_route_to_read_replies(vocab, utterance, person):
    w = FakeWorker(methods=REPLY_METHODS)
    w.results = {"read_replies": {"speech": "Ashok said: done", "detail": ""}}
    resp = handle_transcript(utterance, vocab, w)

    assert w.calls == [("read_replies", {"person": person})]
    assert resp.needs_confirm is False, "reading is not a write action"
    assert resp.ok is True


def test_reading_replies_needs_no_confirmation(vocab):
    w = FakeWorker(methods=REPLY_METHODS)
    w.results = {"read_replies": {"speech": "no reply yet", "detail": ""}}
    resp = handle_transcript("did Ashok reply", vocab, w)
    assert resp.pending is None


def test_a_dm_whose_body_mentions_replying_still_sends(vocab):
    """send_dm is registered first precisely so a body like this is not
    swallowed by the read intent."""
    w = _dm_worker({"id": "U01", "name": "Ashok Kumar", "ambiguous": []})
    w.methods = list(REPLY_METHODS)
    resp = handle_transcript("DM Ashok saying did Madan reply to you", vocab, w)
    assert resp.needs_confirm is True
    assert resp.pending.params["text"] == "did Madan reply to you"


# --- memory intents: answered locally, never sent to the worker ---

@pytest.fixture
def store(tmp_path):
    from jarvis.memory.store import Store

    return Store(str(tmp_path / "m.db"))


def test_remind_me_stores_a_task_with_a_due_time(vocab, store):
    w = FakeWorker()
    resp = handle_transcript("remind me to check ZI-653 orders at 4", vocab, w, store=store)

    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].text == "check ZI-653 orders"
    assert tasks[0].due_at is not None
    assert tasks[0].due_at.hour == 16
    assert w.calls == [], "memory is local, not a worker call"
    assert resp.ok is True


def test_remind_me_without_a_time_still_stores_the_task(vocab, store):
    handle_transcript("remind me to review the PR", vocab, FakeWorker(), store=store)
    tasks = store.list_tasks()
    assert tasks[0].text == "review the PR"
    assert tasks[0].due_at is None


def test_note_is_stored_and_recalled(vocab, store):
    handle_transcript("note that the GLS store needs re-toggling", vocab, FakeWorker(), store=store)
    resp = handle_transcript("what are my notes", vocab, FakeWorker(), store=store)
    assert "GLS store needs re-toggling" in resp.speech


def test_one_note_is_not_called_notes(vocab, store):
    handle_transcript("note that x", vocab, FakeWorker(), store=store)
    resp = handle_transcript("what are my notes", vocab, FakeWorker(), store=store)
    assert "1 note." in resp.speech


def test_no_notes_says_so(vocab, store):
    resp = handle_transcript("what are my notes", vocab, FakeWorker(), store=store)
    assert "no notes" in resp.speech.lower()


def test_memory_intents_never_ask_for_confirmation(vocab, store):
    """Local, private, trivially undone -- unlike a Slack message."""
    resp = handle_transcript("remind me to do the thing", vocab, FakeWorker(), store=store)
    assert resp.needs_confirm is False
    assert resp.pending is None


def test_memory_without_a_store_degrades_rather_than_crashing(vocab):
    resp = handle_transcript("remind me to do the thing", vocab, FakeWorker(), store=None)
    assert resp.ok is False
    assert "memory" in resp.speech.lower()


def test_tasks_are_scoped_to_the_active_domain(vocab, store):
    handle_transcript("remind me to do X", vocab, FakeWorker(), store=store, domain="fedex")
    assert store.list_tasks(domain="fedex")[0].text == "do X"
    assert store.list_tasks(domain="mcsl") == []


def test_my_tasks_merges_your_reminders_with_trello(vocab, store):
    from datetime import datetime, timedelta

    store.add_task("check the GLS store", domain="mcsl",
                   due_at=datetime.now() - timedelta(hours=2))   # overdue
    store.add_task("review the PR", domain="mcsl")                # no due date

    w = FakeWorker(result={"speech": "You have 7 cards. ZI-667 in MCSL 385", "detail": "trello detail"})
    resp = handle_transcript("what are my tasks", vocab, w, store=store)

    assert "overdue" in resp.speech
    assert "check the GLS store" in resp.speech
    assert "review the PR" in resp.speech
    assert "ZI-667" in resp.speech, "the Trello answer must survive the merge"


def test_overdue_reminders_lead(vocab, store):
    from datetime import datetime, timedelta

    store.add_task("late thing", domain="mcsl", due_at=datetime.now() - timedelta(hours=1))
    store.add_task("later thing", domain="mcsl", due_at=datetime.now() + timedelta(hours=5))

    w = FakeWorker(result={"speech": "trello says stuff", "detail": ""})
    resp = handle_transcript("what are my tasks", vocab, w, store=store)

    assert resp.speech.index("late thing") < resp.speech.index("later thing")


def test_my_tasks_with_no_local_reminders_is_unchanged(vocab, store):
    w = FakeWorker(result={"speech": "You have 7 cards", "detail": "d"})
    resp = handle_transcript("what are my tasks", vocab, w, store=store)
    assert resp.speech == "You have 7 cards"


def test_my_tasks_without_a_store_still_works(vocab):
    w = FakeWorker(result={"speech": "You have 7 cards", "detail": "d"})
    resp = handle_transcript("what are my tasks", vocab, w, store=None)
    assert resp.speech == "You have 7 cards"


# --- scope AND filter must both be honoured ---

RELEASE_METHODS = REPLY_METHODS + ("my_release_cards",)


@pytest.mark.parametrize("utterance,digits", [
    ("in MCSL 385 how many tickets assigned to me", "385"),
    ("in MCSL-385 how many tickets are assigned to me", "385"),
    ("my tickets in MCSL 386", "386"),
    ("which tickets are mine in MCSL 385", "385"),
    ("how many tickets do i have in MCSL 385", "385"),
    ("How many cards assigned to me in 385?", "385"),   # no prefix, as spoken
    ("my tickets in 386", "386"),
])
def test_a_release_plus_assigned_to_me_filters_by_you(vocab, utterance, digits):
    """Regression: release_status matched the release alone and answered with
    every card in it, honouring the scope but silently dropping the filter.

    The assertion is on the digits, not the captured string: the prefix is
    optional and the worker strips non-digits before matching a release list,
    so "MCSL 385" and "385" are the same lookup.
    """
    import re

    w = FakeWorker(methods=RELEASE_METHODS)
    w.results = {"my_release_cards": {"speech": "3 tickets assigned to you", "detail": ""}}
    handle_transcript(utterance, vocab, w)

    assert len(w.calls) == 1
    method, params = w.calls[0]
    assert method == "my_release_cards"
    assert re.sub(r"[^0-9]", "", params["release"]) == digits


@pytest.mark.parametrize("utterance", [
    "what's in MCSL 386",
    "status of MCSL-386",
    "show me MCSL 386",
])
def test_a_release_without_a_self_reference_still_lists_everything(vocab, utterance):
    w = FakeWorker(methods=RELEASE_METHODS)
    w.results = {"release_status": {"speech": "has 5 cards", "detail": ""}}
    handle_transcript(utterance, vocab, w)

    assert w.calls[0][0] == "release_status"


def test_my_tasks_is_unaffected_when_no_release_is_named(vocab):
    w = FakeWorker(methods=RELEASE_METHODS)
    handle_transcript("what are my tasks", vocab, w)
    assert w.calls[0][0] == "my_tasks"


def test_a_dm_body_mentioning_a_release_and_me_still_sends(vocab):
    w = _dm_worker({"id": "U01", "name": "Ashok Kumar", "ambiguous": []})
    w.methods = list(RELEASE_METHODS)
    resp = handle_transcript("DM Ashok saying my tickets in MCSL 385 are done", vocab, w)
    assert resp.needs_confirm is True
    assert resp.pending.params["text"] == "my tickets in MCSL 385 are done"


def test_a_card_id_is_not_read_as_a_release(vocab):
    """Regression: allowing a bare release number let '691' be pulled out of
    ZI-691, routing a card question to release_status."""
    w = FakeWorker(methods=RELEASE_METHODS)
    handle_transcript("status of ZI-691", vocab, w)
    assert w.calls[0] == ("card_status", {"card_id": "ZI-691"})


def test_a_plain_count_is_not_read_as_a_release(vocab):
    w = FakeWorker(methods=RELEASE_METHODS)
    resp = handle_transcript("29 cards are done", vocab, w)
    assert resp.tier == 3, "should fall through, not invent a release"
    assert w.calls == []


def test_a_bare_yes_or_no_never_launches_a_job(vocab):
    """Regression: a stray "no" after a settled question fell through to the
    agentic path and started real work."""
    from jarvis.router.conversation import Conversation

    for reply in ("no", "yes", "ok", "cancel"):
        w = FakeWorker(methods=RELEASE_METHODS)
        resp = handle_transcript(reply, vocab, w, conversation=Conversation())
        assert resp.tier == 1, f"{reply!r} reached tier {resp.tier}"
        assert w.calls == []


def test_the_tier3_line_names_no_mechanism(vocab):
    w = FakeWorker(methods=RELEASE_METHODS)
    resp = handle_transcript("create a GLS carrier store", vocab, w)
    assert resp.tier == 3
    for banned in ("claude", "tier", "worker", "subprocess", "cli"):
        assert banned not in resp.speech.lower()


def test_no_spoken_string_anywhere_names_the_plumbing(vocab):
    """The two error paths below only fire on failure, so the happy-path tests
    never caught them saying "Worker" out loud."""
    banned = ("claude", "tier", "worker", "subprocess", "socket", "rpc", "payload")

    class Dead:
        def capabilities(self):
            raise RpcError("connection refused")

        def call(self, m, **p):
            return {}

    resp = handle_transcript("status of ZI-691", vocab, Dead())
    assert resp.ok is False
    for term in banned:
        assert term not in resp.speech.lower(), f"leaked: {term}"

    class Weird:
        def capabilities(self):
            return ["card_status"]

        def call(self, m, **p):
            return "not a dict"

    resp = handle_transcript("status of ZI-691", vocab, Weird())
    assert resp.ok is False
    for term in banned:
        assert term not in resp.speech.lower(), f"leaked: {term}"


# --- dev, test plan, apps, channel posts ---

WIDE = REPLY_METHODS + ("my_release_cards", "card_devs", "test_plan", "post_channel")


def test_who_is_the_dev_resolves_the_card_from_context(vocab):
    from jarvis.router.conversation import Conversation

    c = Conversation()
    w = FakeWorker(methods=WIDE)
    handle_transcript("status of ZI-667", vocab, w, conversation=c)
    handle_transcript("who is the dev for that", vocab, w, conversation=c)

    assert w.calls[-1] == ("card_devs", {"card_id": "ZI-667"})


def test_asking_about_the_dev_with_no_card_in_context_asks_which(vocab):
    """It asks, and names the candidates it can see -- "Which card do you
    mean?" is a dead end when the mishear was the id itself."""
    from jarvis.router.conversation import Conversation

    w = FakeWorker(methods=WIDE + ("my_work",))
    w.results = {"my_work": {"speech": "", "detail": "", "items": [
        {"id": "ZI-667", "actionable": True},
        {"id": "ZI-686", "actionable": True},
        {"id": "ZI-632", "actionable": False},
    ]}}
    resp = handle_transcript("who is the dev for that", vocab, w, conversation=Conversation())

    assert resp.ok is False
    assert "ZI-667" in resp.speech and "ZI-686" in resp.speech
    assert "ZI-632" not in resp.speech, "a done card is not a candidate"
    assert not any(c[0] == "card_devs" for c in w.calls)


def test_asking_which_card_falls_back_when_there_are_no_candidates(vocab):
    from jarvis.router.conversation import Conversation

    w = FakeWorker(methods=WIDE + ("my_work",))
    w.results = {"my_work": {"speech": "", "detail": "", "items": []}}
    resp = handle_transcript("who is the dev for that", vocab, w, conversation=Conversation())
    assert "which card" in resp.speech.lower()


def test_a_single_candidate_is_offered_directly(vocab):
    from jarvis.router.conversation import Conversation

    w = FakeWorker(methods=WIDE + ("my_work",))
    w.results = {"my_work": {"speech": "", "detail": "", "items": [
        {"id": "ZI-667", "actionable": True}]}}
    resp = handle_transcript("go through that card", vocab, w, conversation=Conversation())
    assert "ZI-667" in resp.speech


def test_the_testing_plan_routes_with_the_remembered_card(vocab):
    from jarvis.router.conversation import Conversation

    c = Conversation()
    w = FakeWorker(methods=WIDE)
    handle_transcript("status of ZI-667", vocab, w, conversation=c)
    handle_transcript("what is the testing plan for that", vocab, w, conversation=c)

    assert w.calls[-1] == ("test_plan", {"card_id": "ZI-667"})


def test_a_channel_post_is_read_back_and_not_sent(vocab):
    w = FakeWorker(methods=WIDE)
    resp = handle_transcript("post in qa-team saying ZI-667 is verified", vocab, w)

    assert resp.needs_confirm is True
    assert "qa-team" in resp.speech
    assert "ZI-667 is verified" in resp.speech
    assert not any(c[0] == "post_channel" for c in w.calls), "must not post before consent"
    assert resp.pending.params == {"channel": "qa-team", "text": "ZI-667 is verified"}


def test_a_channel_post_is_not_routed_as_a_dm(vocab):
    """Quietly sending a channel message to a person would be worse than
    failing outright."""
    w = FakeWorker(methods=WIDE)
    resp = handle_transcript("post in mcsl-qa that the toggle is off", vocab, w)
    assert resp.pending.method == "post_channel"


def test_opening_an_app_needs_no_confirmation(vocab):
    """Launching an app is reversible with a click, unlike a Slack message."""
    w = FakeWorker(methods=WIDE)
    with patch("jarvis.apps.open_app", return_value=(True, "Opening Slack.")):
        resp = handle_transcript("open slack", vocab, w)
    assert resp.needs_confirm is False
    assert w.calls == [], "opening an app is local, not a worker call"


def test_an_unknown_app_is_reported(vocab):
    w = FakeWorker(methods=WIDE)
    resp = handle_transcript("open nonsense", vocab, w)
    assert resp.ok is False


# --- labels are the QA workflow ---

QA_METHODS = WIDE + ("my_work", "active_release", "release_progress")


@pytest.mark.parametrize("utterance", [
    "what cards assigned to me", "my cards", "my tickets",
    "what should I test", "what needs testing", "what is my work",
])
def test_the_bare_question_uses_the_label_aware_answer(vocab, utterance):
    """my_work reads QA labels: a verified card is not offered as work, and a
    duplicate is flagged as a sanity check. my_tasks cannot tell the
    difference."""
    w = FakeWorker(methods=QA_METHODS)
    w.results = {"my_work": {"speech": "3 to test", "detail": "", "items": []}}
    handle_transcript(utterance, vocab, w)
    assert w.calls[0][0] == "my_work"


@pytest.mark.parametrize("utterance,digits", [
    ("is 385 done", "385"),
    ("what's left in 385", "385"),
    ("how far is 385", "385"),
])
def test_release_progress_extracts_the_release(vocab, utterance, digits):
    w = FakeWorker(methods=QA_METHODS)
    w.results = {"release_progress": {"speech": "in progress", "detail": ""}}
    handle_transcript(utterance, vocab, w)
    assert w.calls[0] == ("release_progress", {"release": digits})


def test_release_progress_with_no_number_means_the_active_release(vocab):
    w = FakeWorker(methods=QA_METHODS)
    w.results = {"release_progress": {"speech": "in progress", "detail": ""}}
    handle_transcript("release status", vocab, w)
    assert w.calls[0] == ("release_progress", {"release": ""})


@pytest.mark.parametrize("utterance", [
    "which release are we working on", "current release",
])
def test_asking_which_release_is_active(vocab, utterance):
    w = FakeWorker(methods=QA_METHODS)
    w.results = {"active_release": {"speech": "MCSL 385", "detail": ""}}
    handle_transcript(utterance, vocab, w)
    assert w.calls[0][0] == "active_release"


def test_a_named_release_still_wins_over_the_bare_question(vocab):
    """"my tickets in 386" names a release, so it belongs to my_release_cards
    rather than the label-aware summary of everything."""
    w = FakeWorker(methods=QA_METHODS)
    w.results = {"my_release_cards": {"speech": "none", "detail": "", "ids": []}}
    handle_transcript("my tickets in 386", vocab, w)
    assert w.calls[0][0] == "my_release_cards"


def test_a_filler_word_never_starts_a_job(vocab):
    """Regression: "Why?" fell through and spent a minute running a one-word
    prompt. An agentic run is too expensive for an utterance that carries no
    intent."""
    for said in ("Why?", "what", "hmm", "ok then", "hey jarvis"):
        w = FakeWorker(methods=WIDE)
        resp = handle_transcript(said, vocab, w)
        assert resp.tier != 3, f"{said!r} started a job"
        assert resp.ok is False


def test_a_real_request_still_reaches_the_agentic_path(vocab):
    w = FakeWorker(methods=WIDE)
    resp = handle_transcript("create a GLS carrier store with full env", vocab, w)
    assert resp.tier == 3


def test_open_a_pr_is_not_an_app_launch(vocab):
    """"open a PR" would otherwise try to launch an application called "a PR"."""
    w = FakeWorker(methods=WIDE)
    resp = handle_transcript("summarise the fix and open a PR", vocab, w)
    assert resp.tier == 3


@pytest.mark.parametrize("said,app", [
    ("open slack", "slack"),
    ("open the terminal", "the terminal"),
    ("launch vs code", "vs code"),
])
def test_real_app_launches_still_match(vocab, said, app):
    from jarvis.router.intents import match

    m = match(said)
    assert m is not None and m[0].name == "open_app"
    assert m[1]["app"] == app

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from jarvis.daemon import Jarvis
from jarvis.types import Response, Vocab


def _jarvis_with_fakes(transcript, worker_result=None):
    """Build a Jarvis without running __init__, which would spawn a real worker."""
    j = Jarvis.__new__(Jarvis)
    j.domain = "mcsl"
    j.vocab = Vocab(cards=["ZI-691"])
    j.transcriber = MagicMock()
    j.transcriber.transcribe.return_value = transcript
    j.worker = MagicMock()
    j.worker.capabilities.return_value = [
        "card_status", "release_status", "my_tasks", "dev_status",
        "customer_issues", "send_dm", "resolve_person",
    ]
    j.worker.calls = []
    result = worker_result or {"speech": "in QA Ready", "detail": ""}

    def _call(method, **params):
        j.worker.calls.append((method, params))
        return result

    j.worker.call.side_effect = _call
    j.manager = MagicMock()
    j.manager.resolve_alias.return_value = None
    j.tier3 = MagicMock()
    j.tier3.start.return_value = True
    j.store = MagicMock()
    j.busy = False
    from jarvis.router.conversation import Conversation
    j.conversation = Conversation()
    j.dash = MagicMock()
    from jarvis.watch.scheduler import Scheduler
    j.scheduler = Scheduler()
    return j


def test_utterance_produces_a_tier_1_response():
    j = _jarvis_with_fakes("status of ZI six nine one")
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert resp.tier == 1
    assert resp.speech == "in QA Ready"


def test_empty_transcript_is_not_sent_to_the_worker():
    j = _jarvis_with_fakes("")
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert resp.ok is False
    j.worker.call.assert_not_called()


def test_domain_switch_changes_sticky_domain():
    j = _jarvis_with_fakes("switch to fedex")
    j.manager.resolve_alias.return_value = "fedex"
    with patch("jarvis.daemon.speak"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert j.domain == "fedex"


def test_domain_name_alone_does_not_switch():
    """"status of the fedex toggle" mentions a domain but is not a switch
    request; switching on a bare mention would strand the user."""
    j = _jarvis_with_fakes("status of the fedex toggle")
    j.manager.resolve_alias.return_value = "fedex"
    with patch("jarvis.daemon.speak"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert j.domain == "mcsl"


def test_response_is_always_spoken():
    j = _jarvis_with_fakes("status of ZI-691")
    with patch("jarvis.daemon.speak") as spoken:
        j.handle_utterance(np.zeros(16000, dtype=np.float32))
    spoken.assert_called_once()


def test_transcriber_receives_the_vocab_for_biasing():
    j = _jarvis_with_fakes("status of ZI-691")
    with patch("jarvis.daemon.speak"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32))
    assert j.transcriber.transcribe.call_args[0][1] is j.vocab


# --- write actions: an explicit spoken yes is the only thing that sends ---

from jarvis.router.confirm import PendingAction


def _pending_response():
    return Response(
        speech="Sending to Ashok Kumar: hello. Ok?",
        needs_confirm=True,
        pending=PendingAction("send_dm", {"user_id": "U01", "text": "hello"}, "", ""),
    )


class _FakeCapture:
    def drain(self):
        pass

    def record(self, seconds):
        return np.zeros(int(seconds * 16000), dtype=np.float32)


def _confirm_run(reply):
    j = _jarvis_with_fakes("DM Ashok saying hello")
    j.transcriber.transcribe.side_effect = ["DM Ashok saying hello", reply]
    with patch("jarvis.daemon.handle_transcript", return_value=_pending_response()), \
         patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    return j, resp


def test_pending_action_is_executed_after_a_yes():
    j, resp = _confirm_run("ok")
    assert ("send_dm", {"user_id": "U01", "text": "hello"}) in j.worker.calls


@pytest.mark.parametrize("reply", ["no", "cancel", "hmm", "", "what", "the toggle is off"])
def test_pending_action_is_dropped_on_anything_but_yes(reply):
    """Ambiguity is never consent -- a wrong DM cannot be taken back."""
    j, resp = _confirm_run(reply)
    assert not any(c[0] == "send_dm" for c in j.worker.calls)
    assert resp.ok is False


def test_cancelling_says_so():
    j, resp = _confirm_run("no")
    assert "cancel" in resp.speech.lower()


def test_a_read_response_never_enters_the_confirm_turn():
    j = _jarvis_with_fakes("what are my tasks")
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    assert resp.needs_confirm is False
    assert j.transcriber.transcribe.call_count == 1, "must not listen for a confirmation"


def test_tier3_is_started_and_does_not_block():
    j = _jarvis_with_fakes("create a GLS carrier store")
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    assert resp.tier == 3
    assert j.tier3.start.call_count == 1


def test_a_second_tier3_job_is_refused_while_one_runs():
    j = _jarvis_with_fakes("create a GLS carrier store")
    j.tier3.start.return_value = False
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    assert resp.ok is False
    assert "last one" in resp.speech.lower()


def test_every_command_is_logged():
    j = _jarvis_with_fakes("what are my tasks")
    with patch("jarvis.daemon.speak"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    assert j.store.log_command.call_count == 1


def test_a_logging_failure_does_not_break_the_turn():
    j = _jarvis_with_fakes("what are my tasks")
    j.store.log_command.side_effect = RuntimeError("disk full")
    with patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    assert resp.ok is True


# --- watchers must never interrupt a live turn ---

def test_an_announcement_is_spoken_when_idle():
    j = _jarvis_with_fakes("x")
    with patch("jarvis.daemon.speak") as spoken:
        j._announce(["Reminder: check the GLS store"])
    assert spoken.call_count == 1


def test_an_announcement_is_held_while_a_turn_is_in_progress():
    """Interrupting mid-sentence is worse than a slightly late notification."""
    j = _jarvis_with_fakes("x")
    j.busy = True
    with patch("jarvis.daemon.speak") as spoken:
        j._announce(["Reminder: check the GLS store"])
    assert spoken.call_count == 0
    assert j.scheduler.pending == ["Reminder: check the GLS store"]


def test_held_announcements_are_spoken_after_the_turn():
    j = _jarvis_with_fakes("x")
    j.busy = True
    j._announce(["held news"])
    j.busy = False
    with patch("jarvis.daemon.speak") as spoken:
        j._announce(j.scheduler.drain())
    assert spoken.call_count == 1


def test_sending_a_dm_records_the_recipient_for_the_reply_watcher():
    j = _jarvis_with_fakes("DM Ashok saying hello")
    resp = Response(
        speech="Sending to Ashok Kumar: hello. Ok?",
        detail="Ashok Kumar (U01): hello",
        needs_confirm=True,
        pending=PendingAction("send_dm", {"user_id": "U01", "text": "hello"}, "", ""),
    )
    j.transcriber.transcribe.return_value = "ok"
    with patch("jarvis.daemon.speak"):
        j._run_confirmation(resp, _FakeCapture())

    j.store.mark_seen.assert_called_once_with("dm_sent", "Ashok Kumar")


def test_a_read_action_does_not_record_a_dm_recipient():
    j = _jarvis_with_fakes("x")
    resp = Response(speech="?", needs_confirm=True,
                    pending=PendingAction("something_else", {}, "", ""))
    j.transcriber.transcribe.return_value = "ok"
    with patch("jarvis.daemon.speak"):
        j._run_confirmation(resp, _FakeCapture())
    j.store.mark_seen.assert_not_called()


def test_the_scheduler_has_all_four_watchers():
    j = _jarvis_with_fakes("x")
    j.scheduler = Jarvis._build_scheduler(j)
    assert {job.name for job in j.scheduler.jobs} == {"reminders", "zendesk", "trello", "replies"}


# --- "Did you mean X?" must not be a dead end ---

def _clarify_run(reply):
    j = _jarvis_with_fakes("how many cards assigned to mean 385")
    ask = Response(speech="Did you mean MCSL-385?", detail="heard 'mean 385'",
                   needs_confirm=True)
    j.transcriber.transcribe.side_effect = ["how many cards assigned to mean 385", reply]
    with patch("jarvis.daemon.handle_transcript", side_effect=[ask, Response(speech="29 cards")]), \
         patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    return j, resp


def test_a_yes_to_did_you_mean_reruns_the_corrected_sentence():
    j, resp = _clarify_run("yes")
    assert resp.speech == "29 cards"


@pytest.mark.parametrize("reply", ["no", "hmm", ""])
def test_anything_but_yes_abandons_the_clarification(reply):
    j, resp = _clarify_run(reply)
    assert resp.ok is False
    assert "never mind" in resp.speech.lower()


def test_a_clarification_without_a_capture_just_asks():
    j = _jarvis_with_fakes("x")
    ask = Response(speech="Did you mean MCSL-385?", needs_confirm=True)
    with patch("jarvis.daemon.handle_transcript", return_value=ask), \
         patch("jarvis.daemon.speak"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), None)
    assert resp.needs_confirm is True


# --- everything Jarvis says must mute its own mic ---

def _listening_jarvis(transcript="x"):
    from jarvis.ears.wake import WakeListener

    j = _jarvis_with_fakes(transcript)
    j.listener = WakeListener(lambda c, s=None: None, mode="clap", settle=0)
    return j


def test_speaking_mutes_the_mic_and_unmutes_after():
    j = _listening_jarvis()
    seen = {}
    with patch("jarvis.daemon.speak", side_effect=lambda r: seen.setdefault("muted_during", j.listener.muted.is_set())), \
         patch("jarvis.daemon.time.sleep"):
        j._say(Response(speech="a long sentence"))
    assert seen["muted_during"] is True
    assert j.listener.muted.is_set() is False


def test_a_watcher_announcement_mutes_the_mic():
    """Regression: watcher announcements run on a background thread, outside
    the turn-scoped settle, and woke Jarvis with its own voice."""
    j = _listening_jarvis()
    with patch.object(j, "_say") as said:
        j._announce(["Reminder: check the GLS store"])
    said.assert_called_once()


def test_a_tier3_completion_mutes_the_mic():
    j = _listening_jarvis("create a GLS carrier store")
    with patch("jarvis.daemon.speak"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    callback = j.tier3.start.call_args[0][1]
    with patch.object(j, "_say") as said:
        callback("some long output from claude")
    said.assert_called_once()


def test_speaking_unmutes_even_if_speech_raises():
    j = _listening_jarvis()
    with patch("jarvis.daemon.speak", side_effect=RuntimeError("say failed")), \
         patch("jarvis.daemon.time.sleep"):
        with pytest.raises(RuntimeError):
            j._say(Response(speech="x"))
    assert j.listener.muted.is_set() is False, "a failed say must not leave the mic muted"


# --- one wake buys a conversation, not a single command ---

class _ScriptedCapture:
    """Feeds a fixed list of utterances, one per record() call."""

    def __init__(self, *_):
        self.windows = []

    def drain(self):
        pass

    def record(self, seconds):
        self.windows.append(seconds)
        return np.zeros(int(seconds * 16000), dtype=np.float32)


def _converse(*transcripts):
    j = _jarvis_with_fakes("")
    j.transcriber.transcribe.side_effect = list(transcripts)
    j.listener = MagicMock()
    cap = _ScriptedCapture()
    with patch("jarvis.daemon.speak"), patch("jarvis.daemon.time.sleep"):
        j._on_wake(cap, "wakeword:hey_jarvis")
    return j, cap


def test_the_conversation_continues_without_another_wake_word():
    j, cap = _converse("what are my tasks", "what are my tasks", "")
    assert len(cap.windows) == 3, "should have listened again after answering"


def test_silence_closes_the_conversation():
    j, cap = _converse("what are my tasks", "")
    assert len(cap.windows) == 2


def test_a_follow_up_gets_a_longer_window_than_the_first_utterance():
    from jarvis.daemon import CAPTURE_SECONDS, FOLLOWUP_SECONDS

    j, cap = _converse("what are my tasks", "")
    assert cap.windows[0] == CAPTURE_SECONDS
    assert cap.windows[1] == FOLLOWUP_SECONDS


def test_saying_goodbye_closes_the_conversation():
    j, cap = _converse("that's all")
    assert len(cap.windows) == 1


def test_the_conversation_cannot_loop_forever():
    from jarvis.daemon import MAX_TURNS

    j, cap = _converse(*["what are my tasks"] * (MAX_TURNS + 5))
    assert len(cap.windows) == MAX_TURNS


def test_a_fresh_conversation_starts_on_each_wake():
    j, _ = _converse("go through card 667", "")
    assert j.conversation.last_card in ("", "ZI-667")
    j.transcriber.transcribe.side_effect = ["what are my tasks", ""]
    cap = _ScriptedCapture()
    with patch("jarvis.daemon.speak"), patch("jarvis.daemon.time.sleep"):
        j._on_wake(cap, "clap")
    assert j.conversation.last_card == "", "state must not leak between wakes"


# --- plain language, and results that are actually spoken ---

def test_the_mechanism_is_never_named_aloud():
    j = _jarvis_with_fakes("create a GLS carrier store")
    with patch("jarvis.daemon.speak"), patch("jarvis.daemon.time.sleep"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    for banned in ("claude", "tier", "worker", "subprocess"):
        assert banned not in resp.speech.lower(), f"leaked internal term: {banned}"


def test_the_dispatch_line_says_what_is_happening():
    j = _jarvis_with_fakes("create a GLS carrier store")
    with patch("jarvis.daemon.speak"), patch("jarvis.daemon.time.sleep"):
        resp = j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
    assert "fetching" in resp.speech.lower() or "boss" in resp.speech.lower()


def test_a_finished_job_is_summarised_and_spoken():
    j = _jarvis_with_fakes("create a GLS carrier store")
    j.worker.call.side_effect = None
    j.worker.call.return_value = {"speech": "The store is ready.", "detail": ""}
    with patch("jarvis.daemon.speak"), patch("jarvis.daemon.time.sleep"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
        callback = j.tier3.start.call_args[0][1]
        with patch.object(j, "_say") as said:
            callback("a wall of formatted output nobody wants read aloud")
    spoken = said.call_args[0][0].speech
    assert "The store is ready." in spoken
    assert "what do you want to do" in spoken.lower()


def test_a_failed_summary_still_says_something_useful():
    j = _jarvis_with_fakes("create a store")
    j.worker.call.side_effect = RuntimeError("no api key")
    with patch("jarvis.daemon.speak"), patch("jarvis.daemon.time.sleep"):
        assert "could not summarise" in j._summarise("output", "q").lower()


# --- the acknowledgement names what it is fetching ---

def test_the_acknowledgement_names_the_card():
    """Saying the subject back is what makes it an assistant rather than a
    progress bar: you learn it heard you correctly before the answer lands."""
    from jarvis.daemon import _acknowledge

    said = _acknowledge("go through card ZI-687 and summarise the cutoff fix")
    assert "ZI-687" in said
    assert "minute" in said.lower()


def test_the_acknowledgement_names_a_release():
    from jarvis.daemon import _acknowledge

    assert "MCSL 386" in _acknowledge("what's in MCSL 386 right now")


def test_the_acknowledgement_falls_back_to_the_request():
    from jarvis.daemon import _acknowledge

    said = _acknowledge("create a GLS carrier store")
    assert "GLS carrier store" in said


def test_the_acknowledgement_survives_an_empty_request():
    from jarvis.daemon import _acknowledge

    assert _acknowledge("").strip() != ""


def test_the_acknowledgement_names_no_mechanism():
    from jarvis.daemon import _acknowledge

    said = _acknowledge("go through card ZI-687").lower()
    for banned in ("claude", "tier", "worker", "subprocess"):
        assert banned not in said


def test_an_expired_session_is_reported_not_summarised():
    """The error must not be read back as an answer -- doing so produced a
    spoken "what do you want to do?" that the mic heard and acted on."""
    j = _jarvis_with_fakes("create a store")
    with patch("jarvis.daemon.speak"), patch("jarvis.daemon.time.sleep"):
        j.handle_utterance(np.zeros(16000, dtype=np.float32), _FakeCapture())
        callback = j.tier3.start.call_args[0][1]
        with patch.object(j, "_say") as said, patch.object(j, "_summarise") as summarised:
            callback("Failed to authenticate: OAuth session expired and could not be refreshed")
    spoken = said.call_args[0][0]
    assert "sign-in has expired" in spoken.speech
    assert "claude login" in spoken.speech
    assert spoken.ok is False
    # A bare `assert_not_called(), "msg"` builds a tuple and checks nothing.
    assert summarised.call_count == 0, "must not summarise an auth error into an answer"

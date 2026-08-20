"""End-to-end routing for "look at this" and "go through this doc".

Screen capture and AppleScript are stubbed; everything between the transcript
and the worker call is real.
"""
import pytest

from jarvis.eyes.document import Doc
from jarvis.eyes.window import Window
from jarvis.router.core import handle_transcript
from jarvis.types import RpcError, Vocab

from tests.test_router import FakeWorker

EYES = ("card_status", "look", "read_doc")


@pytest.fixture
def vocab():
    return Vocab(cards=["MCSL-385"], people=["Ashok Kumar"], zi_ids=["ZI-667"])


@pytest.fixture
def seeing(monkeypatch, tmp_path):
    """A frontmost Chrome window that photographs successfully."""
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG fake")
    win = Window(id=7, app="Google Chrome", title="Trello", width=1200, height=800)
    monkeypatch.setattr("jarvis.eyes.window.frontmost", lambda: win)
    monkeypatch.setattr("jarvis.eyes.window.look", lambda: (str(shot), win))
    return win


def test_looking_at_the_screen_sends_the_shot_and_the_question(seeing, vocab):
    worker = FakeWorker(methods=EYES)
    worker.results["look"] = {"speech": "The weight shows 1.5 kg but the order says 2 kg.", "ok": True}

    reply = handle_transcript("see this request, is the weight correct", vocab, worker)

    method, params = worker.calls[-1]
    assert method == "look"
    assert params["path"].endswith(".png")
    assert "weight" in params["question"]
    assert "Chrome" in params["window"]
    assert "1.5 kg" in reply.speech
    assert reply.ok is True


def test_a_capture_failure_is_spoken_not_raised(monkeypatch, vocab):
    def boom():
        raise RuntimeError("I could not take the screenshot. Check Screen Recording permission.")

    monkeypatch.setattr("jarvis.eyes.window.look", boom)
    reply = handle_transcript("look at this", vocab, FakeWorker(methods=EYES))
    assert reply.ok is False
    assert "Screen Recording" in reply.speech


def test_a_worker_failure_while_looking_is_spoken(seeing, vocab):
    worker = FakeWorker(methods=EYES, error="socket gone")
    reply = handle_transcript("look at this", vocab, worker)
    assert reply.ok is False
    assert "screenshot" in reply.speech.lower()


def test_a_project_without_eyes_says_so(seeing, vocab):
    reply = handle_transcript("look at this", vocab, FakeWorker(methods=("card_status",)))
    assert reply.ok is False
    assert "look at the screen" in reply.speech


# --- documents ------------------------------------------------------------

def test_reading_a_document_sends_the_path_not_a_picture(monkeypatch, seeing, vocab):
    monkeypatch.setattr(
        "jarvis.eyes.document.frontmost",
        lambda app, title="": Doc(kind="file", ref="/tmp/guide.pdf", app=app),
    )
    worker = FakeWorker(methods=EYES)
    worker.results["read_doc"] = {"speech": "It is the MCSL 383 support guide.", "ok": True}

    reply = handle_transcript("go through this doc and tell me what it says", vocab, worker)

    method, params = worker.calls[-1]
    assert method == "read_doc"
    assert params["ref"] == "/tmp/guide.pdf"
    assert params["kind"] == "file"
    assert "383" in reply.speech


def test_a_browser_url_is_read_as_a_url(monkeypatch, seeing, vocab):
    monkeypatch.setattr(
        "jarvis.eyes.document.frontmost",
        lambda app, title="": Doc(kind="url", ref="https://trello.com/c/abc", app=app),
    )
    worker = FakeWorker(methods=EYES)
    worker.results["read_doc"] = {"speech": "It is a Trello card.", "ok": True}

    handle_transcript("read this page", vocab, worker)
    assert worker.calls[-1][1]["kind"] == "url"


def test_an_app_that_hides_its_file_falls_back_to_looking(monkeypatch, seeing, vocab):
    """VS Code gives only a filename, which cannot be opened. A picture of the
    visible page is worse than the file and far better than "I can't"."""
    monkeypatch.setattr(
        "jarvis.eyes.document.frontmost",
        lambda app, title="": Doc(kind="name", ref="vision.py", app=app),
    )
    worker = FakeWorker(methods=EYES)
    worker.results["look"] = {"speech": "A Python file defining a look function.", "ok": True}

    reply = handle_transcript("go through this file", vocab, worker)

    assert worker.calls[-1][0] == "look"
    assert "read the screen instead" in reply.speech
    assert "look function" in reply.speech
    assert reply.ok is True


def test_the_fallback_does_not_claim_success_when_looking_also_fails(
    monkeypatch, seeing, vocab
):
    monkeypatch.setattr(
        "jarvis.eyes.document.frontmost",
        lambda app, title="": Doc(kind="name", ref="vision.py", app=app),
    )
    worker = FakeWorker(methods=EYES)
    worker.results["look"] = {"speech": "no idea", "ok": False}

    reply = handle_transcript("go through this file", vocab, worker)
    assert reply.ok is False
    assert "Chrome" in reply.speech


def test_no_window_at_all_is_reported(monkeypatch, vocab):
    monkeypatch.setattr("jarvis.eyes.window.frontmost", lambda: None)
    reply = handle_transcript("go through this doc", vocab, FakeWorker(methods=EYES))
    assert reply.ok is False
    assert "window" in reply.speech.lower()


def test_looking_never_raises_whatever_the_worker_returns(seeing, vocab):
    """handle_transcript is the seam the daemon calls from the audio thread."""
    for bad in [None, "a string", 42, []]:
        worker = FakeWorker(methods=EYES)
        worker.results["look"] = bad
        reply = handle_transcript("look at this", vocab, worker)
        assert isinstance(reply.speech, str) and reply.speech

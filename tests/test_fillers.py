import pytest

from jarvis.correct.fillers import strip_fillers


@pytest.mark.parametrize("said,expected", [
    ("um can you check ZI-667", "can you check ZI-667"),
    ("uh what is the status", "what is the status"),
    ("so um what's in 385", "what's in 385"),
    ("erm go through this doc", "go through this doc"),
    ("what is the, uh, testing plan", "what is the testing plan"),
    ("hmm is this correct", "is this correct"),
    ("check ZI-667 you know", "check ZI-667"),
    ("i mean what is the dev status", "what is the dev status"),
    ("see this request, like that", "see this request"),
    ("well what should I test", "what should I test"),
    ("so what's in MCSL 386", "what's in MCSL 386"),
])
def test_fillers_are_removed(said, expected):
    assert strip_fillers(said) == expected


# --- the words that only look like filler --------------------------------

def test_a_bare_ok_survives():
    """A bare "ok" is what confirms a Slack DM. Stripping it would send the
    message on the next thing said instead."""
    assert strip_fillers("ok") == "ok"
    assert strip_fillers("okay") == "okay"


@pytest.mark.parametrize("said", ["yes", "yeah", "no", "nope", "send it", "go ahead"])
def test_confirmations_survive_untouched(said):
    assert strip_fillers(said) == said


@pytest.mark.parametrize("said", [
    "is this correct",
    "is that right",
    "does this look fine",
    "just show me mine",
    "ok so actually make it the GLS store",
])
def test_load_bearing_words_survive(said):
    """"right" switches the screen to judging; "just" means only; and "ok so
    actually ..." is a correction that must not decay into a bare "ok"."""
    assert strip_fillers(said) == said


def test_a_bare_opener_is_left_alone():
    """"So?" is a question, not a preamble to something."""
    assert strip_fillers("so") == "so"
    assert strip_fillers("well") == "well"


def test_a_filler_only_utterance_keeps_its_text():
    """Something was said. Whether it was a request is the thin-utterance
    guard's decision, not this module's."""
    assert strip_fillers("um") == "um"
    assert strip_fillers("uh um hmm") != ""


@pytest.mark.parametrize("said", ["", "   ", None])
def test_nothing_in_nothing_out(said):
    assert strip_fillers(said) in ("", None, "   ")


# --- it must not mangle what is left -------------------------------------

def test_no_double_spaces_or_stranded_commas():
    out = strip_fillers("check, um, ZI-667, uh, please")
    assert "  " not in out
    assert ", ," not in out
    assert not out.startswith(",")


def test_ids_and_numbers_are_untouched():
    assert "ZI-667" in strip_fillers("um go through ZI-667")
    assert "385" in strip_fillers("so what's in 385")


def test_a_sentence_with_no_fillers_is_returned_unchanged():
    for said in ["what cards are assigned to me",
                 "dm Ashok saying the toggle is off",
                 "go through this doc and tell me the issue"]:
        assert strip_fillers(said) == said


def test_the_word_um_inside_another_word_is_safe():
    """Substring matching would eat "number", "summary", "umbrella"."""
    for said in ["what is the number", "give me the summary", "check the volume"]:
        assert strip_fillers(said) == said


def test_repeated_sounds_are_handled():
    assert strip_fillers("ummm uhhh what is this") == "what is this"


# --- the read-back that started this ------------------------------------

def test_the_acknowledgement_does_not_cut_a_word_in_half():
    """A raw 60-character slice read back as "looking into the rate for the
    interna"."""
    from jarvis.daemon import _acknowledge

    said = ("what is the rate for the international shipment going out of "
            "Bangalore to Chennai today")
    out = _acknowledge(said)
    assert "—" in out
    tail = out.split("looking into", 1)[1].strip().rstrip(".")
    assert said.startswith(tail), f"{tail!r} is not a clean prefix"
    assert tail.endswith("out"), f"expected a whole last word, got {tail!r}"
    assert not tail.endswith(",")


def test_a_short_request_is_read_back_whole():
    from jarvis.daemon import _acknowledge

    assert "what should I test" in _acknowledge("what should I test")


def test_a_named_card_is_preferred_over_echoing_the_sentence():
    from jarvis.daemon import _acknowledge

    assert "ZI-667" in _acknowledge("um so go through that thing for ZI-667")


def test_the_transcriber_strips_fillers_before_returning(monkeypatch):
    """One seam: the read-back, the intent patterns, the workflow triggers and
    the tier-3 prompt all read what this returns."""
    import numpy as np

    from jarvis.ears.stt import Transcriber

    class Seg:
        text = " Um, so go through, uh, this doc "
        no_speech_prob = 0.001
        avg_logprob = -0.25

    t = Transcriber()
    monkeypatch.setattr(t, "_ensure_model",
                        lambda: type("M", (), {"transcribe": lambda *a, **k: ([Seg()], None)})())
    monkeypatch.setattr(Transcriber, "is_silent", staticmethod(lambda audio: False))
    assert t.transcribe(np.zeros(16000, dtype=np.float32)) == "go through this doc"

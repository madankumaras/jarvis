import numpy as np
import pytest

from jarvis.ears.stt import Transcriber, build_initial_prompt
from jarvis.types import Vocab


def test_initial_prompt_includes_card_ids():
    prompt = build_initial_prompt(Vocab(cards=["ZI-691"], people=["Ashok Kumar"]))
    assert "ZI-691" in prompt
    assert "Ashok Kumar" in prompt


def test_initial_prompt_includes_baseline_jargon():
    prompt = build_initial_prompt(Vocab())
    for term in ("Trello", "toggle", "rate shopping"):
        assert term in prompt


def test_initial_prompt_is_bounded():
    huge = Vocab(cards=[f"ZI-{i}" for i in range(500)])
    assert len(build_initial_prompt(huge)) <= 900


def test_initial_prompt_prefers_people_and_carriers_over_bulk_ids():
    """Card ids are recoverable by the correction layer; names are not, so they
    must survive truncation."""
    v = Vocab(
        cards=[f"ZI-{i}" for i in range(500)],
        people=["Ashok Kumar"],
        carriers=["gls"],
    )
    prompt = build_initial_prompt(v)
    assert "Ashok Kumar" in prompt
    assert "gls" in prompt


def test_transcriber_defers_model_load():
    t = Transcriber()
    assert t._model is None


@pytest.mark.audio
def test_transcribe_silence_returns_empty():
    t = Transcriber()
    silence = np.zeros(16000, dtype=np.float32)
    assert t.transcribe(silence).strip() == ""


# --- silence gate: whisper hallucinates fluent sentences out of a quiet room ---

def test_silence_is_detected_below_the_rms_gate():
    from jarvis.ears.stt import SILENCE_RMS

    quiet = np.full(16000, SILENCE_RMS * 0.5, dtype=np.float32)
    assert Transcriber.is_silent(quiet) is True


def test_audible_speech_passes_the_gate():
    from jarvis.ears.stt import SILENCE_RMS

    loud = np.full(16000, SILENCE_RMS * 3, dtype=np.float32)
    assert Transcriber.is_silent(loud) is False


def test_empty_audio_is_silent():
    assert Transcriber.is_silent(np.array([], dtype=np.float32)) is True


def test_transcribe_returns_empty_on_silence_without_loading_the_model():
    """The gate must run before the model does -- a silent room should cost
    nothing and, crucially, must not produce invented text."""
    t = Transcriber()
    quiet = np.zeros(16000, dtype=np.float32)
    assert t.transcribe(quiet) == ""
    assert t._model is None, "model was loaded despite silence"


def test_high_no_speech_prob_segments_are_dropped():
    class _Seg:
        def __init__(self, text, p, lp=-0.3):
            self.text, self.no_speech_prob, self.avg_logprob = text, p, lp

    class _FakeModel:
        def transcribe(self, *a, **kw):
            return [_Seg("real words", 0.001, -0.25), _Seg("hallucinated", 0.95, -2.1)], None

    t = Transcriber()
    t._model = _FakeModel()
    loud = np.full(16000, 0.1, dtype=np.float32)
    assert t.transcribe(loud) == "real words"


def test_low_confidence_segments_are_dropped_even_when_no_speech_prob_passes():
    """Measured hallucinations score no_speech_prob ~0.50 -- under a naive 0.6
    gate -- but avg_logprob -1.3 to -2.1, far below real speech at -0.25."""

    class _Seg:
        def __init__(self, text, p, lp):
            self.text, self.no_speech_prob, self.avg_logprob = text, p, lp

    class _FakeModel:
        def transcribe(self, *a, **kw):
            return [_Seg("See any cards assigned in Trello", 0.503, -2.12)], None

    t = Transcriber()
    t._model = _FakeModel()
    assert t.transcribe(np.full(16000, 0.1, dtype=np.float32)) == ""


def test_real_speech_confidence_is_accepted():
    class _Seg:
        def __init__(self, text, p, lp):
            self.text, self.no_speech_prob, self.avg_logprob = text, p, lp

    class _FakeModel:
        def transcribe(self, *a, **kw):
            return [_Seg(" What is the status of ZI-691?", 0.001, -0.248)], None

    t = Transcriber()
    t._model = _FakeModel()
    assert t.transcribe(np.full(16000, 0.1, dtype=np.float32)) == "What is the status of ZI-691?"

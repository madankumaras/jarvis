"""faster-whisper wrapper with domain-biased decoding."""
from __future__ import annotations

import os

import numpy as np

from jarvis.correct.fillers import strip_fillers
from jarvis.types import Vocab

SAMPLE_RATE = 16000
MAX_PROMPT_CHARS = 900

# Measured on this mic: 3s of a quiet room is RMS ~0.008; audible sound is
# ~0.022. Below the gate we do not transcribe at all, because whisper
# hallucinates fluent sentences out of silence — and the domain initial_prompt
# steers those hallucinations towards plausible-sounding commands like
# "Is there any card assigned to me in Trello?", which then execute.
SILENCE_RMS = 0.012

# Whisper invents fluent sentences from room noise, and the domain prompt
# steers them towards plausible commands ("See any cards assigned in Trello in
# my name") which then execute. Measured on this mic, the two populations are
# far apart:
#
#                     no_speech_prob   avg_logprob
#   ambient noise       0.50 - 0.53    -2.12 / -1.28
#   real speech         0.001          -0.248
#
# A segment must clear both to be believed.
NO_SPEECH_PROB = 0.30
MIN_AVG_LOGPROB = -1.0

# JARVIS_STT_DEBUG=1 prints the score of every rejected segment, so the
# thresholds above can be retuned against a real room rather than guessed.
DEBUG_SCORES = bool(os.environ.get("JARVIS_STT_DEBUG"))

BASELINE_JARGON = (
    "MCSL, FedEx, AU Post, Trello, Zendesk, Slack, Shopify, "
    "toggle, rate shopping, carrier, packaging, QA Ready, ZI"
)


def build_initial_prompt(vocab: Vocab) -> str:
    """Bias whisper's decoder toward real entity names.

    Order matters because the prompt is truncated: people and carriers come
    before bulk ids. A misheard name is unrecoverable, whereas a misheard id
    is snapped back by the correction layer.
    """
    parts = [BASELINE_JARGON]
    for group in (vocab.people, vocab.carriers, vocab.cards, vocab.zi_ids):
        if group:
            parts.append(", ".join(group))
    prompt = ", ".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS].rsplit(",", 1)[0]
    return prompt


class Transcriber:
    """Loads the model lazily so importing this module stays cheap."""

    def __init__(self, model_size: str = "small.en") -> None:
        self.model_size = model_size
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    @staticmethod
    def is_silent(audio: np.ndarray) -> bool:
        if audio.size == 0:
            return True
        return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) < SILENCE_RMS

    def transcribe(self, audio: np.ndarray, vocab: Vocab | None = None) -> str:
        # Cheapest possible check first: no model call at all on a silent room.
        if self.is_silent(audio):
            return ""

        model = self._ensure_model()
        segments, _ = model.transcribe(
            audio,
            language="en",
            initial_prompt=build_initial_prompt(vocab) if vocab else BASELINE_JARGON,
            vad_filter=True,
            beam_size=1,
            # Without this, whisper feeds each segment its own previous output
            # and can spiral into repeated invented text.
            condition_on_previous_text=False,
        )
        kept: list[str] = []
        for s in segments:
            nsp = getattr(s, "no_speech_prob", 0.0)
            lp = getattr(s, "avg_logprob", 0.0)
            if nsp < NO_SPEECH_PROB and lp > MIN_AVG_LOGPROB:
                kept.append(s.text)
            elif DEBUG_SCORES:
                print(
                    f"  [stt] dropped no_speech={nsp:.3f} logprob={lp:.3f} {s.text!r}",
                    flush=True,
                )
        # Strip fillers here, the one point all three callers go through, so the
        # read-back, the tier-3 prompt and the dashboard transcript all get the
        # cleaned text. Typed input needs none of this, which is why it is here
        # and not in handle_transcript.
        return strip_fillers(" ".join(kept).strip())

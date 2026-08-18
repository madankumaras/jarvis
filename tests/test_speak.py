import os

import pytest
from unittest.mock import patch

from jarvis.types import Response
from jarvis.voice.speak import notify, say, speak


def test_say_shells_out_to_macos_say():
    with patch("subprocess.run") as run:
        say("hello there")
    assert run.call_args[0][0][0] == "say"
    assert "hello there" in run.call_args[0][0]


def test_say_passes_dash_leading_text_after_a_separator():
    """Two defences against the same bug, deliberately.

    Without `--`, say parses "-x ..." as a flag and refuses the utterance --
    and Trello titles routinely start with a dash. Sanitising now strips a
    leading dash as well, so the separator is belt-and-braces rather than the
    only guard. Both are asserted: relying on sanitisation alone would break
    the moment a dash survived it.
    """
    with patch("subprocess.run") as run:
        say("-x fixed the toggle")
    argv = run.call_args[0][0]
    assert "--" in argv
    spoken = argv[-1]
    assert argv.index("--") < len(argv) - 1
    assert not spoken.startswith("-"), "a leading dash reached say()"
    assert "fixed the toggle" in spoken


def test_say_ignores_empty_text():
    with patch("subprocess.run") as run:
        say("")
    run.assert_not_called()


def test_notify_uses_osascript():
    with patch("subprocess.run") as run:
        notify("Jarvis", "MCSL-384 is in QA Ready")
    assert run.call_args[0][0][0] == "osascript"


def test_notify_escapes_double_quotes():
    with patch("subprocess.run") as run:
        notify("Jarvis", 'he said "hello"')
    script = run.call_args[0][0][2]
    assert '\\"hello\\"' in script


def test_speak_says_speech_and_notifies_with_detail():
    resp = Response(speech="short line", detail="the long body")
    with patch("jarvis.voice.speak.say") as s, patch("jarvis.voice.speak.notify") as n:
        speak(resp)
    s.assert_called_once_with("short line")
    assert "the long body" in n.call_args[0][1]


def test_speak_falls_back_to_speech_when_detail_is_empty():
    resp = Response(speech="only this")
    with patch("jarvis.voice.speak.say"), patch("jarvis.voice.speak.notify") as n:
        speak(resp)
    assert "only this" in n.call_args[0][1]


# --- greeting ---

from datetime import datetime

from jarvis.voice.greeting import greeting, time_of_day


def test_time_of_day_boundaries():
    assert time_of_day(0) == "Good morning"
    assert time_of_day(11) == "Good morning"
    assert time_of_day(12) == "Good afternoon"
    assert time_of_day(16) == "Good afternoon"
    assert time_of_day(17) == "Good evening"
    assert time_of_day(23) == "Good evening"


def test_greeting_addresses_the_user_and_offers_help():
    text = greeting(datetime(2026, 8, 15, 9, 0))
    assert text == "Good morning boss, how can I help you?"


def test_greeting_uses_the_current_time_when_none_given():
    assert "how can I help you?" in greeting()


# --- voice selection ---

def test_the_voice_and_rate_come_from_the_environment():
    """Set JARVIS_VOICE / JARVIS_VOICE_RATE rather than editing code."""
    import importlib

    import jarvis.voice.speak as sp

    original = (sp.VOICE, sp.RATE)
    try:
        os.environ["JARVIS_VOICE"] = "Rishi"
        os.environ["JARVIS_VOICE_RATE"] = "165"
        importlib.reload(sp)
        assert sp.VOICE == "Rishi"
        assert sp.RATE == 165
    finally:
        os.environ.pop("JARVIS_VOICE", None)
        os.environ.pop("JARVIS_VOICE_RATE", None)
        importlib.reload(sp)
        assert (sp.VOICE, sp.RATE) == original


def test_the_configured_voice_is_passed_to_say():
    import jarvis.voice.speak as sp

    with patch("subprocess.run") as run:
        sp.say("hello")
    argv = run.call_args[0][0]
    assert argv[argv.index("-v") + 1] == sp.VOICE
    assert argv[argv.index("-r") + 1] == str(sp.RATE)


def test_novelty_voices_are_excluded():
    """`say -v ?` mixes real synthesisers with voices that sing or buzz."""
    from jarvis.voice.voices import english_voices

    names = {n for n, _ in english_voices()}
    for joke in ("Zarvox", "Bubbles", "Bells", "Trinoids", "Whisper"):
        assert joke not in names


def test_indian_english_voices_are_listed_first():
    """The accent of the voice should match the accent speaking to it."""
    from jarvis.voice.voices import english_voices

    voices = english_voices()
    if not any(loc == "en_IN" for _, loc in voices):
        pytest.skip("no en_IN voices installed on this machine")
    assert voices[0][1] == "en_IN"


def test_no_duplicate_voices():
    from jarvis.voice.voices import english_voices

    names = [n for n, _ in english_voices()]
    assert len(names) == len(set(names))


# --- emoji in Trello comments were being read aloud by name ---

def test_emoji_are_removed_not_read_aloud():
    """macOS `say` reads emoji by their Unicode names, so a comment containing
    🧪 ✅ → • became "test tube", "white heavy check mark", "rightwards arrow",
    "bullet". That is what "it talked another language" actually was."""
    from jarvis.voice.speak import speakable

    out = speakable("Reassessment 🧪 done ✅ • see → next")
    for ch in ("🧪", "✅", "•", "→"):
        assert ch not in out
    assert "Reassessment" in out and "done" in out


def test_a_ticket_reference_is_spoken_as_words():
    from jarvis.voice.speak import speakable

    assert "ticket 399431" in speakable("cutoff time not applied [#399431]")


def test_the_ticket_rule_runs_before_the_markdown_rule():
    """The markdown rule strips '#', which the ticket rule needs. Ordering bug
    turned "[#39993]" into "[ 39993]"."""
    from jarvis.voice.speak import speakable

    assert "[" not in speakable("surcharge [#39993] in MCSL 385")


def test_markdown_scaffolding_is_stripped():
    from jarvis.voice.speak import speakable

    assert speakable("**Reassessment completed**") == "Reassessment completed"


def test_a_url_is_not_spelled_out():
    from jarvis.voice.speak import speakable

    out = speakable("see https://trello.com/c/UhLpxjzk for detail")
    assert "https" not in out
    assert "a link" in out


def test_an_em_dash_becomes_a_pause():
    from jarvis.voice.speak import speakable

    assert "—" not in speakable("needs testing — marked duplicate")


def test_sanitising_never_returns_none_or_crashes():
    from jarvis.voice.speak import speakable

    assert speakable("") == ""
    assert speakable(None) == ""
    assert speakable("🧪🚚✅") == ""


def test_say_speaks_the_sanitised_text():
    import jarvis.voice.speak as sp

    with patch("subprocess.run") as run:
        sp.say("done ✅ — see → next")
    spoken = run.call_args[0][0][-1]
    assert "✅" not in spoken and "→" not in spoken


def test_say_skips_text_that_sanitises_to_nothing():
    """A comment that is only emoji must not fire an empty utterance."""
    import jarvis.voice.speak as sp

    with patch("subprocess.run") as run:
        sp.say("🧪 ✅ •")
    run.assert_not_called()


# --- code identifiers were read as gibberish ---

@pytest.mark.parametrize("raw,expected_in", [
    ("bkg_ref_id missing", "booking reference I D"),
    ("pickup_dropoff_office_id", "pickup dropoff office I D"),
    ("isInsuranceRequired = false", "is Insurance Required"),
    ("payment_mode hardcoded to QR", "Q R"),
    ("the api returned", "A P I"),
    ("check the sku field", "S K U"),
])
def test_identifiers_are_made_pronounceable(raw, expected_in):
    """Every TTS reads "bkg_ref_id" as noise, and in an Indian English voice it
    lands somewhere between gibberish and another language."""
    from jarvis.voice.speak import speakable

    assert expected_in in speakable(raw)


def test_camel_case_is_split_into_words():
    from jarvis.voice.speak import speakable

    assert speakable("isInsuranceRequired") == "is Insurance Required"


def test_a_long_all_caps_token_is_left_alone():
    """Spelling out eight letters is worse than attempting the word."""
    from jarvis.voice.speak import speakable

    assert "PARSPL" in speakable("24_SPP_PARSPL service code")


def test_ordinary_words_are_untouched():
    from jarvis.voice.speak import speakable

    said = speakable("India Post label prints undiscounted product price")
    assert said == "India Post label prints undiscounted product price"


def test_expansion_does_not_double_space_an_acronym():
    from jarvis.voice.speak import speakable

    assert "A  P  I" not in speakable("the API is slow")


def test_whitespace_is_collapsed_after_expansion():
    from jarvis.voice.speak import speakable

    assert "  " not in speakable("bkg_ref_id  __  payment_mode")

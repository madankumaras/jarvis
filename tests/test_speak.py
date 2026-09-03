import os

import pytest
from unittest.mock import patch

from jarvis.types import Response
from jarvis.voice.speak import speakable
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


# --- capitals that are real words -----------------------------------------

@pytest.mark.parametrize("said,expected", [
    ("USE_SCHEDULED_PICKUP", "USE"),
    ("rateRequestType LIST", "LIST"),
    ("status is TRUE", "TRUE"),
    ("value is NULL", "NULL"),
    ("state is OPEN", "OPEN"),
    ("marked DONE", "DONE"),
    ("the ACCOUNT rate", "ACCOUNT"),
    ("24_SPP_PARSPL service code", "PARSPL"),
])
def test_a_capitalised_real_word_is_not_spelled_out(said, expected):
    """Spacing every capitalised run turned USE_SCHEDULED_PICKUP into
    "U S E SCHEDULED PICKUP" and LIST into "L I S T". A vowel means it can be
    attempted as a word, and the token is left verbatim -- `say` reads ACCOUNT
    and Account identically, so rewriting it only loses a service code."""
    out = speakable(said)
    assert expected in out
    assert " ".join(expected) not in out


@pytest.mark.parametrize("said,expected", [
    ("MCSL 385", "M C S L"),
    ("the DHL rate", "D H L"),
    ("GLS packaging", "G L S"),
    ("TNT is pending", "T N T"),
    ("2 KG", "K G"),
])
def test_a_vowelless_acronym_is_still_spelled_out(said, expected):
    assert expected in speakable(said)


@pytest.mark.parametrize("said,expected", [
    ("the UPS rate", "U P S"),
    ("USPS label", "U S P S"),
    ("250 INR", "rupees"),
    ("45 AUD", "Australian dollars"),
])
def test_domain_terms_with_vowels_are_named_explicitly(said, expected):
    """"UPS" and "USPS" contain vowels, so the rule would attempt them as
    words. Currency codes likewise -- "Inr", "Aud"."""
    assert expected in speakable(said)


# --- identifiers that are noise ------------------------------------------

def test_a_random_store_suffix_is_not_read_out():
    """Shopify puts a random tail on every store slug. The readable half is
    what identifies the store to a person."""
    out = speakable("Opening the mypostautomation-gs01o4wy store")
    assert "mypostautomation" in out
    assert "gs01o4wy" not in out


def test_a_meaningful_hyphenated_name_survives():
    """A slug made of words keeps them. "qa" still becomes "Q A", which is
    right -- the store really is named QA-moody-store -- so what matters is
    that no part is dropped the way a random suffix is."""
    out = speakable("Opening qa-moody-store")
    assert "moody" in out and "store" in out
    assert "667" in speakable("ZI-667 is verified")


def test_a_file_extension_is_not_read_as_letters():
    out = speakable("I read MCSL_383_Support_Guide.md")
    assert ".md" not in out
    assert "Support Guide" in out


@pytest.mark.parametrize("name", ["core.py", "vision.ts", "config.yaml", "run.sh"])
def test_code_extensions_are_dropped(name):
    assert "." not in speakable(f"look at {name}")


def test_a_json_field_becomes_words():
    """Verbatim from the screen-judging answer."""
    out = speakable("totalPackageCount is 2 but requestedPackageLineItems has one entry")
    assert "total Package Count" in out
    assert "requested Package Line Items" in out


# --- numbers ---------------------------------------------------------------

@pytest.mark.parametrize("said,expected", [
    ("window 548419010", "5 4 8 4 1 9 0 1 0"),
    ("app id 384068550657", "3 8 4 0 6 8 5 5 0 6 5 7"),
])
def test_a_long_identifier_is_read_as_digits(said, expected):
    """`say` reads 548419010 as "five hundred forty-eight million four hundred
    nineteen thousand and ten" -- a wall of number words where an id was
    meant."""
    assert expected in speakable(said)


@pytest.mark.parametrize("said,expected", [
    ("MCSL 385 has 16 cards", "385"),
    ("2 packages", "2"),
    ("120000 characters", "120000"),
    ("at 16:00", "16:00"),
    ("0.43 score", "0.43"),
    ("1.5 KG", "1.5"),
])
def test_real_quantities_are_left_as_numbers(said, expected):
    """Six digits and under are quantities, not identifiers, and are better
    read as numbers."""
    assert expected in speakable(said)


@pytest.mark.parametrize("said,expected", [
    ("1470x801 pixels", "1470 by 801"),
    ("30x20x10 CM", "30 by 20 by 10"),
])
def test_dimensions_are_read_as_by(said, expected):
    assert expected in speakable(said)

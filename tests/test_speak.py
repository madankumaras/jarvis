from unittest.mock import patch

from jarvis.types import Response
from jarvis.voice.speak import notify, say, speak


def test_say_shells_out_to_macos_say():
    with patch("subprocess.run") as run:
        say("hello there")
    assert run.call_args[0][0][0] == "say"
    assert "hello there" in run.call_args[0][0]


def test_say_passes_dash_leading_text_after_a_separator():
    # Without `--`, say parses "-x ..." as a flag and refuses the utterance.
    # Trello titles and comments routinely start with a dash.
    with patch("subprocess.run") as run:
        say("-x fixed the toggle")
    argv = run.call_args[0][0]
    assert "--" in argv
    assert argv.index("--") < argv.index("-x fixed the toggle")


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

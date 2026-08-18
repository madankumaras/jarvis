from unittest.mock import patch

from jarvis.apps import MATCH_CUTOFF, installed, open_app, resolve


def test_some_apps_are_found():
    assert len(installed()) > 5


def test_no_app_names_carry_the_suffix():
    assert not any(a.endswith(".app") for a in installed())


def test_an_exact_name_resolves():
    apps = installed()
    assert resolve(apps[0]) == apps[0]


def test_resolution_is_case_insensitive():
    apps = installed()
    assert resolve(apps[0].lower()) == apps[0]


def test_a_leading_the_is_ignored():
    """People say "open the terminal", not "open terminal"."""
    if "Terminal" not in installed():
        return
    assert resolve("the terminal") == "Terminal"


def test_a_partial_name_finds_the_full_one():
    if "Google Chrome" not in installed():
        return
    assert resolve("chrome") == "Google Chrome"


def test_spoken_aliases_resolve_where_substrings_cannot():
    """"vs code" is not a substring of "Visual Studio Code" and never fuzzy
    matches it, so it needs an explicit alias."""
    if "Visual Studio Code" not in installed():
        return
    assert resolve("vs code") == "Visual Studio Code"


def test_a_mishearing_still_resolves():
    if "Slack" not in installed():
        return
    assert resolve("slak") == "Slack"


def test_an_unrelated_word_resolves_to_nothing():
    """Measured: unrelated words reach 0.67 similarity against real app names.
    Opening the wrong app is more confusing than admitting no match."""
    assert resolve("nonsense") == ""
    assert resolve("xyzzy") == ""


def test_the_cutoff_is_above_the_measured_false_positive_range():
    assert MATCH_CUTOFF >= 0.8


def test_empty_input_resolves_to_nothing():
    assert resolve("") == ""
    assert resolve("   ") == ""


def test_opening_an_unknown_app_reports_rather_than_failing_silently():
    ok, said = open_app("definitely not an app")
    assert ok is False
    assert "couldn't find" in said.lower()


def test_opening_a_known_app_shells_out_to_open():
    apps = installed()
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        ok, said = open_app(apps[0])
    assert ok is True
    assert run.call_args[0][0][:2] == ["open", "-a"]
    assert "opening" in said.lower()


def test_a_failed_launch_is_reported():
    apps = installed()
    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        ok, said = open_app(apps[0])
    assert ok is False

"""Opening apps, raising windows, and sending one to the Dock."""
import pytest

from jarvis import apps
from jarvis.eyes.window import Window
from jarvis.router.intents import match


def named(text):
    found = match(text)
    return found[0].name if found else None


def app_of(text):
    found = match(text)
    return found[1].get("app", "") if found else ""


# --- opening versus raising ----------------------------------------------

@pytest.mark.parametrize("said,app", [
    ("open slack", "slack"),
    ("launch vs code", "vs code"),
    ("open the terminal", "the terminal"),
    ("bring up slack", "slack"),
])
def test_opening_an_app(said, app):
    """"Bring up X" is launching, not raising -- the only spoken form where
    "bring" does not mean move-to-front."""
    assert named(said) == "open_app"
    assert app_of(said) == app


@pytest.mark.parametrize("said,app", [
    ("bring slack front", "slack"),
    ("bring slack to the front", "slack"),
    ("bring the 383 window front", "the 383 window"),
    ("bring chrome forward", "chrome"),
    ("switch to chrome", "chrome"),
    ("go to the terminal", "the terminal"),
    ("focus the trello window", "the trello window"),
    ("focus on slack", "slack"),
    ("raise chrome", "chrome"),
])
def test_raising_a_window_or_app(said, app):
    assert named(said) == "focus_window"
    assert app_of(said) == app


@pytest.mark.parametrize("said", [
    "minimize this", "minimise this window", "hide this window",
    "hide this", "minimise", "send that window to the dock",
])
def test_minimising(said):
    """(?:window)? not window?, which made only the "w" optional and left
    "minimize this" matching nothing at all."""
    assert named(said) == "minimise_window"


# --- what must NOT be a window command -----------------------------------

@pytest.mark.parametrize("said,expected", [
    ("show me my cards", "my_work"),
    ("what cards are assigned to me", "my_work"),
    ("what should I test", "my_work"),
    ("go through this doc", "read_document"),
    ("bring me the status of ZI-667", "card_status"),
])
def test_real_questions_are_not_window_commands(said, expected):
    """A single loose pattern matched "show me my cards" and "bring me the
    status of ZI-667", and would have tried to raise a window called "me the
    status of ZI-667"."""
    assert named(said) == expected


def test_a_card_id_is_never_a_window():
    assert named("go to ZI-667") != "focus_window"
    assert named("switch to ZI-686") != "focus_window"


def test_opening_a_pull_request_is_not_an_app():
    assert named("summarise the fix and open a PR") is None


# --- finding the window --------------------------------------------------

def _stub(monkeypatch, windows):
    monkeypatch.setattr("jarvis.eyes.window.all_windows", lambda: windows)


SCREEN = [
    Window(id=1, app="Google Chrome", title="MCSL 383 Support Guide", width=1470, height=801),
    Window(id=2, app="Slack", title="dev-interns-qa-regression (Channel)", width=1200, height=800),
    Window(id=3, app="Code", title=".gitignore — MCSLDomainExpert", width=1400, height=900),
]


def test_a_title_beats_an_app_name(monkeypatch):
    """"Bring the 383 window front" should find that Chrome window, not merely
    raise Chrome and leave whichever tab was frontmost."""
    _stub(monkeypatch, SCREEN)
    assert apps.find_window("the 383 window").id == 1


def test_an_app_name_matches_when_no_title_does(monkeypatch):
    _stub(monkeypatch, SCREEN)
    assert apps.find_window("slack").id == 2


def test_an_electron_window_is_still_findable(monkeypatch):
    """Slack and VS Code have no AppleScript window dictionary, but their
    titles are in the CoreGraphics list all the same."""
    _stub(monkeypatch, SCREEN)
    assert apps.find_window("gitignore").id == 3


def test_nothing_close_finds_nothing(monkeypatch):
    _stub(monkeypatch, SCREEN)
    assert apps.find_window("nonsense banana xyz") is None
    assert apps.find_window("") is None


# --- "this window" names nothing -----------------------------------------

@pytest.mark.parametrize("said", ["this window", "that window", "this", "that", "that one"])
def test_a_demonstrative_names_the_front_window_instead_of_failing(monkeypatch, said):
    """Searching for a window called "this" and reporting failure is the worst
    of the available answers."""
    monkeypatch.setattr(
        "jarvis.eyes.window.frontmost",
        lambda: Window(id=1, app="Google Chrome", title="Trello", width=900, height=600),
    )
    ok, spoken = apps.focus_window(said)
    assert ok is True
    assert "already at the front" in spoken
    assert "Chrome" in spoken


def test_a_demonstrative_with_no_window_asks(monkeypatch):
    monkeypatch.setattr("jarvis.eyes.window.frontmost", lambda: None)
    ok, spoken = apps.focus_window("this window")
    assert ok is False
    assert "which one" in spoken.lower()


# --- an unscriptable app gets an honest answer ---------------------------

def test_an_electron_app_says_it_can_only_raise_the_app(monkeypatch):
    """Claiming to have raised a particular Slack window would be a lie."""
    _stub(monkeypatch, SCREEN)
    monkeypatch.setattr(apps, "_raise_in_app", lambda app, needle: False)
    monkeypatch.setattr(apps, "focus_app", lambda name: (True, f"{name} is at the front."))
    ok, spoken = apps.focus_window("dev-interns")
    assert ok is True
    assert "only raise the app" in spoken
    # Not the whole title: "dev-interns-qa-regression (Channel) - PluginHive -
    # 7 new items - Slack" read aloud is noise.
    assert "qa-regression" not in spoken


def test_naming_the_app_does_not_get_the_window_caveat(monkeypatch):
    """"Switch to Slack" asked for the app and got it. Explaining a window
    limitation nobody hit is noise."""
    _stub(monkeypatch, SCREEN)
    monkeypatch.setattr(apps, "_raise_in_app", lambda app, needle: False)
    monkeypatch.setattr(apps, "focus_app", lambda name: (True, f"{name} is at the front."))
    ok, spoken = apps.focus_window("slack")
    assert ok is True
    assert "only raise the app" not in spoken
    assert spoken == "Slack is at the front."


def test_a_window_that_cannot_be_found_reports_the_phrase(monkeypatch):
    _stub(monkeypatch, SCREEN)
    monkeypatch.setattr(apps, "focus_app", lambda name: (False, "no such app"))
    ok, spoken = apps.focus_window("banana split")
    assert ok is False
    assert "banana split" in spoken


# --- AppleScript string safety ------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ('Payment "Gateway"', 'Payment \\"Gateway\\"'),
    ("back\\slash", "back\\\\slash"),
    ("plain title", "plain title"),
])
def test_titles_are_escaped_for_applescript(raw, expected):
    """Window titles are page-authored. An unescaped quote ends the string
    literal early and breaks the script."""
    assert apps._as_string(raw) == expected


def test_an_unscriptable_app_is_not_asked_to_minimise(monkeypatch):
    monkeypatch.setattr(
        "jarvis.eyes.window.frontmost",
        lambda: Window(id=1, app="Slack", title="Slack", width=900, height=600),
    )
    ok, spoken = apps.minimise_front()
    assert ok is False
    assert "Accessibility" in spoken


def test_minimising_with_no_window_says_so(monkeypatch):
    monkeypatch.setattr("jarvis.eyes.window.frontmost", lambda: None)
    ok, spoken = apps.minimise_front()
    assert ok is False
    assert "can't see a window" in spoken

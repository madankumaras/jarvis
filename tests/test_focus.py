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
    ("go to the terminal", "the terminal"),
    ("focus the trello window", "the trello window"),
    ("focus on slack", "slack"),
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


@pytest.mark.parametrize("said", [
    "bring chrome forward", "switch to chrome", "raise chrome", "open chrome",
])
def test_anything_naming_chrome_goes_to_the_profile_handler(said):
    """Chrome is the one app where the app is not the whole answer -- three
    profiles, and only one is signed in to the stores. The profile handler
    delegates to plain app focus when no profile is named."""
    assert named(said) == "chrome_profile"


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


# --- how much of the machine can it actually reach -----------------------

def test_finder_resolves():
    """Finder lives in /System/Library/CoreServices, which is not scanned --
    117 internal agents live there too. It is named explicitly instead."""
    assert apps.resolve("finder") == "Finder"
    assert apps.resolve("files") == "Finder"


def test_keychain_access_resolves():
    """It is no longer in /System/Applications/Utilities on this macOS; it
    moved to CoreServices/Applications, and resolved to nothing until that
    directory was added."""
    assert apps.resolve("keychain access") == "Keychain Access"
    assert apps.resolve("keychain") == "Keychain Access"


@pytest.mark.parametrize("said", ["system preferences", "preferences", "settings"])
def test_the_old_name_for_system_settings_still_works(said):
    """Renamed in Ventura, still said the old way."""
    assert apps.resolve(said) == "System Settings"


@pytest.mark.parametrize("said", [
    "slack", "chrome", "firefox", "vs code", "terminal", "safari",
    "activity monitor", "disk utility", "screenshot", "script editor",
    "calculator", "preview", "textedit", "app store",
])
def test_ordinary_app_names_resolve(said):
    assert apps.resolve(said) != ""


@pytest.mark.parametrize("said", ["banana split", "xyzzy", "nonsense app name"])
def test_an_app_that_is_not_there_is_refused_rather_than_guessed(said):
    """Opening the wrong app is more confusing than admitting no match."""
    assert apps.resolve(said) == ""


def test_the_user_applications_folder_is_searched(monkeypatch, tmp_path):
    (tmp_path / "Homemade.app").mkdir()
    monkeypatch.setattr(apps, "SEARCH_DIRS", (str(tmp_path),))
    monkeypatch.setattr(apps, "EXTRA_APPS", {})
    assert "Homemade" in apps.installed()


def test_a_vendor_subfolder_is_searched(monkeypatch, tmp_path):
    """Setapp, Microsoft Office and Adobe all bury their apps one level down."""
    nested = tmp_path / "Microsoft Office"
    nested.mkdir()
    (nested / "Microsoft Word.app").mkdir()
    monkeypatch.setattr(apps, "SEARCH_DIRS", (str(tmp_path),))
    monkeypatch.setattr(apps, "EXTRA_APPS", {})
    assert "Microsoft Word" in apps.installed()


def test_a_helper_bundle_inside_a_bundle_is_ignored(monkeypatch, tmp_path):
    """Every app contains helper .app bundles; none of them are launchable."""
    outer = tmp_path / "Big.app"
    (outer / "Contents" / "Helpers").mkdir(parents=True)
    (outer / "Contents" / "Helpers" / "Updater.app").mkdir()
    monkeypatch.setattr(apps, "SEARCH_DIRS", (str(tmp_path),))
    monkeypatch.setattr(apps, "EXTRA_APPS", {})
    found = apps.installed()
    assert "Big" in found
    assert "Updater" not in found


def test_spotlight_is_only_a_fallback(monkeypatch):
    """Spotlight knows 321 bundles against 76 in the scanned directories, and
    most of the difference is internal helpers. A pool that size makes a fuzzy
    mishear likelier to land somewhere surprising, so it runs last."""
    called = []
    monkeypatch.setattr(apps, "spotlight", lambda n: called.append(n) or "")
    apps.resolve("slack")
    assert called == [], "spotlight ran for an app already in the list"
    apps.resolve("definitely not installed anywhere")
    assert called, "spotlight should be the last resort"


def test_spotlight_failure_is_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise OSError("mdfind missing")

    monkeypatch.setattr(apps.subprocess, "run", boom)
    assert apps.spotlight("anything") == ""


def test_spotlight_skips_bundles_nested_in_other_bundles(monkeypatch):
    class Done:
        returncode = 0
        stdout = ("/Applications/Big.app/Contents/Helpers/Thing.app\n"
                  "/Applications/Thing.app\n")

    monkeypatch.setattr(apps.subprocess, "run", lambda *a, **k: Done())
    assert apps.spotlight("thing") == "Thing"

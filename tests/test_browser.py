"""Chrome profiles by name.

No real Chrome and no real config file: Local State, osascript and the caches
are all stubbed, so this says nothing about whose machine it runs on.
"""
import json

import pytest

from jarvis import browser
from jarvis.router.intents import match

# Shaped like the real thing, including the detail that made display names
# useless: all three profiles carry the same display name.
INFO = {
    "profile": {"info_cache": {
        "Default": {"name": "A. Person", "user_name": "someone@company.com"},
        "Profile 1": {"name": "A. Person", "user_name": "second@gmail.com"},
        "Profile 5": {"name": "A. Person", "user_name": "first@gmail.com"},
    }}
}


@pytest.fixture
def chrome(monkeypatch, tmp_path):
    """Stub Local State, the config file and the window cache."""
    monkeypatch.setattr(browser, "_read_json", lambda path: (
        INFO if "Local State" in path else json.loads(
            (tmp_path / "windows.json").read_text() if (tmp_path / "windows.json").exists() else "{}"
        )
    ))
    monkeypatch.setattr(browser, "CONFIG", str(tmp_path / "profiles.yaml"))
    monkeypatch.setattr(browser, "WINDOW_CACHE", str(tmp_path / "windows.json"))
    return tmp_path


def test_profiles_are_keyed_by_email_not_display_name(chrome):
    """All three profiles carry one display name, so info_cache[dir]["name"] cannot
    tell them apart. user_name is the only key."""
    found = browser.profiles()
    assert found["Default"] == "someone@company.com"
    assert len({v for v in found.values()}) == 3


def test_the_work_account_is_recognised_by_its_domain(chrome):
    """A domain nobody can sign up for is the employer's. That is the label
    that matters: the office profile is the one signed in to the stores."""
    derived = browser.derive_labels(browser.profiles())
    assert derived["office"] == "someone@company.com"


def test_personal_accounts_are_numbered(chrome):
    derived = browser.derive_labels(browser.profiles())
    assert set(derived) == {"office", "personal", "personal 2"}


def test_no_work_account_means_no_office_label(chrome, monkeypatch):
    monkeypatch.setattr(browser, "profiles", lambda: {"Default": "a@gmail.com"})
    assert browser.derive_labels(browser.profiles()) == {"personal": "a@gmail.com"}


# --- the config file is what actually decides ----------------------------

def test_a_config_file_overrides_the_guess(chrome):
    """Chrome's own profile order is arbitrary -- it labelled the two personal
    accounts the opposite way round from how they are actually referred to."""
    (chrome / "profiles.yaml").write_text(
        "office: someone@company.com\n"
        "personal: first@gmail.com\n"
        "personal 2: second@gmail.com\n"
    )
    assert browser.resolve_profile("personal chrome")[0] == "Profile 5"
    assert browser.resolve_profile("second personal chrome")[0] == "Profile 1"


def test_the_config_file_is_seeded_on_first_use(chrome):
    assert not (chrome / "profiles.yaml").exists()
    browser.labels()
    body = (chrome / "profiles.yaml").read_text()
    assert "office: someone@company.com" in body


def test_comments_and_quotes_in_the_config_are_tolerated(chrome):
    (chrome / "profiles.yaml").write_text(
        '# a comment\n\noffice: "someone@company.com"   # trailing\n'
    )
    assert browser.labels() == {"office": "someone@company.com"}


# --- saying it ------------------------------------------------------------

@pytest.mark.parametrize("said,expected", [
    ("office chrome", "Default"),
    ("office browser", "Default"),
    ("work browser", "Default"),
    ("the official chrome", "Default"),
])
def test_the_office_profile_is_reachable_by_several_names(chrome, said, expected):
    assert browser.resolve_profile(said)[0] == expected


def test_personal_two_is_not_swallowed_by_personal(chrome):
    """Longest label first, or "second personal" matches "personal"."""
    (chrome / "profiles.yaml").write_text(
        "personal: first@gmail.com\npersonal 2: second@gmail.com\n"
    )
    assert browser.resolve_profile("second personal chrome")[0] == "Profile 1"
    assert browser.resolve_profile("personal chrome")[0] == "Profile 5"


def test_an_email_said_outright_resolves(chrome):
    assert browser.resolve_profile("open first@gmail.com chrome")[0] == "Profile 5"


def test_an_unknown_profile_lists_what_is_known(chrome):
    ok, spoken = browser.open_profile("banana")
    assert ok is False
    assert "office" in spoken


def test_a_bare_chrome_names_no_profile(chrome):
    assert browser.resolve_profile("")[0] == ""


# --- raise, do not relaunch ---------------------------------------------

def test_a_known_window_is_raised_rather_than_reopened(chrome, monkeypatch):
    """Measured: `open -na --profile-directory` called for a profile that
    already had a window opened a fifth window rather than focusing the fourth.
    Repeating "open office chrome" would pile up windows."""
    (chrome / "windows.json").write_text(json.dumps({"Default": "99"}))
    monkeypatch.setattr(browser, "window_ids", lambda: {"99", "100"})
    monkeypatch.setattr(browser, "raise_window", lambda wid: wid == "99")
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a))

    ok, spoken = browser.open_profile("office chrome")
    assert ok is True
    assert "at the front" in spoken
    assert launched == [], "should not have launched anything"


def test_a_stale_window_id_falls_back_to_launching(chrome, monkeypatch):
    """The window may have been closed since. Raising a dead id must not be
    reported as success."""
    (chrome / "windows.json").write_text(json.dumps({"Default": "99"}))
    monkeypatch.setattr(browser, "window_ids", lambda: {"100"})
    monkeypatch.setattr(browser, "_new_window", lambda before, **k: "101")
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a[0]))

    ok, spoken = browser.open_profile("office chrome")
    assert ok is True
    assert "Opened" in spoken
    assert any("--profile-directory=Default" in " ".join(cmd) for cmd in launched)


def test_the_new_window_id_is_remembered(chrome, monkeypatch):
    monkeypatch.setattr(browser, "window_ids", lambda: set())
    monkeypatch.setattr(browser, "_new_window", lambda before, **k: "555")
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: None)

    browser.open_profile("office chrome")
    assert json.loads((chrome / "windows.json").read_text())["Default"] == "555"


def test_a_launch_failure_is_reported_not_raised(chrome, monkeypatch):
    monkeypatch.setattr(browser, "window_ids", lambda: set())

    def boom(*a, **k):
        raise OSError("no open binary")

    monkeypatch.setattr(browser.subprocess, "run", boom)
    ok, spoken = browser.open_profile("office chrome")
    assert ok is False
    assert "Couldn't open" in spoken


# --- a window id goes into AppleScript ----------------------------------

@pytest.mark.parametrize("bad", [
    "1; do shell script \"rm -rf /\"", "abc", "", "1 or true", "99)",
])
def test_only_a_plain_number_is_accepted_as_a_window_id(bad):
    """The id is interpolated into a script, so anything else is refused
    outright rather than escaped."""
    assert browser.raise_window(bad) is False


# --- routing -------------------------------------------------------------

def named(text):
    found = match(text)
    return found[0].name if found else None


def profile_of(text):
    found = match(text)
    return found[1].get("profile", "") if found else ""


@pytest.mark.parametrize("said,profile", [
    ("open office chrome", "office"),
    ("bring the office browser front", "office"),
    ("open work browser", "work"),
    ("switch to my personal chrome", "personal"),
    ("open second personal chrome", "second personal"),
    ("bring office chrome to the front", "office"),
])
def test_a_named_profile_routes_to_the_profile_handler(said, profile):
    """Before focus_window and open_app, which would both resolve "office
    chrome" to the Chrome application and launch whichever profile is
    default."""
    assert named(said) == "chrome_profile"
    assert profile_of(said) == profile


@pytest.mark.parametrize("said", ["open chrome", "bring chrome to the front"])
def test_a_bare_chrome_carries_no_profile(said):
    """Picking one would be a guess, so the router treats it as a plain app."""
    assert named(said) == "chrome_profile"
    assert profile_of(said) == ""


@pytest.mark.parametrize("said,expected", [
    ("open slack", "open_app"),
    ("go through this doc", "read_document"),
    ("what cards are assigned to me", "my_work"),
    ("bring the 383 window front", "focus_window"),
])
def test_everything_else_is_untouched(said, expected):
    assert named(said) == expected

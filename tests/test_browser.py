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
    monkeypatch.setattr(browser, "identify", lambda d: "")
    monkeypatch.setattr(browser, "_new_window", lambda before, **k: "101")
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a[0]))

    ok, spoken = browser.open_profile("office chrome")
    assert ok is True
    assert "Opened" in spoken
    assert any("--profile-directory=Default" in " ".join(cmd) for cmd in launched)


def test_the_new_window_id_is_remembered(chrome, monkeypatch):
    monkeypatch.setattr(browser, "window_ids", lambda: set())
    monkeypatch.setattr(browser, "identify", lambda d: "")
    monkeypatch.setattr(browser, "_new_window", lambda before, **k: "555")
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: None)

    browser.open_profile("office chrome")
    assert json.loads((chrome / "windows.json").read_text())["Default"] == "555"


def test_a_launch_failure_is_reported_not_raised(chrome, monkeypatch):
    monkeypatch.setattr(browser, "window_ids", lambda: set())
    monkeypatch.setattr(browser, "identify", lambda d: "")

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


# --- windows Jarvis did not open ----------------------------------------

def test_an_existing_window_is_identified_by_its_pages(chrome, monkeypatch):
    """The question that prompted this: three profiles were already open by
    hand, and the cache only knows what Jarvis launched itself. Without this,
    "bring office chrome front" opened a fourth window while the office one sat
    there already.

    Measured on the three real windows: each scored 1.00 against its own
    profile's session hosts and at most 0.43 against the others.
    """
    monkeypatch.setattr(browser, "_session_hosts", lambda d, recent=3: {
        "Default": {"admin.shopify.com", "trello.com", "app.slack.com"},
        "Profile 1": {"phonepe.com", "razorpay.com"},
        "Profile 5": {"youtube.com", "netflix.com"},
    }[d])
    monkeypatch.setattr(browser, "_live_windows", lambda: {
        "10": {"youtube.com", "netflix.com"},
        "20": {"admin.shopify.com", "trello.com"},
        "30": {"phonepe.com"},
    })
    assert browser.identify("Default") == "20"
    assert browser.identify("Profile 1") == "30"
    assert browser.identify("Profile 5") == "10"


def test_a_window_that_fits_two_profiles_is_not_claimed(chrome, monkeypatch):
    """Google and Gmail are open in every profile. A window of nothing but
    shared hosts identifies nothing, and guessing would raise the wrong one."""
    shared = {"google.com", "mail.google.com"}
    monkeypatch.setattr(browser, "_session_hosts", lambda d, recent=3: shared)
    monkeypatch.setattr(browser, "_live_windows", lambda: {"10": shared})
    assert browser.identify("Default") == ""


def test_identification_needs_a_clear_winner(chrome, monkeypatch):
    monkeypatch.setattr(browser, "_session_hosts", lambda d, recent=3: {
        "Default": {"a.com", "b.com"},
        "Profile 1": {"a.com", "b.com", "c.com"},
        "Profile 5": set(),
    }[d])
    # Fits Profile 1 at least as well as Default, so Default must not claim it.
    monkeypatch.setattr(browser, "_live_windows", lambda: {"10": {"a.com", "b.com"}})
    assert browser.identify("Default") == ""


def test_no_session_data_identifies_nothing(chrome, monkeypatch):
    monkeypatch.setattr(browser, "_session_hosts", lambda d, recent=3: set())
    assert browser.identify("Default") == ""


def test_an_identified_window_is_raised_and_then_cached(chrome, monkeypatch):
    monkeypatch.setattr(browser, "window_ids", lambda: set())
    monkeypatch.setattr(browser, "identify", lambda d: "77")
    monkeypatch.setattr(browser, "raise_window", lambda wid: wid == "77")
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a))

    ok, spoken = browser.open_profile("office chrome")
    assert ok is True and "at the front" in spoken
    assert launched == [], "should have raised, not launched"
    assert json.loads((chrome / "windows.json").read_text())["Default"] == "77"


def test_nothing_identified_still_launches(chrome, monkeypatch):
    monkeypatch.setattr(browser, "window_ids", lambda: set())
    monkeypatch.setattr(browser, "identify", lambda d: "")
    monkeypatch.setattr(browser, "_new_window", lambda before, **k: "88")
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a[0]))

    ok, spoken = browser.open_profile("office chrome")
    assert ok is True and "Opened" in spoken
    assert launched, "expected a launch when no window exists"


# --- opening a store ----------------------------------------------------

@pytest.fixture
def slugs(monkeypatch):
    monkeypatch.setattr(browser, "store_slugs", lambda directory="Default", limit=400: [
        "mypostautomation-gs01o4wy", "ajexautomation", "ajexautomation2",
        "qa-moody-store", "indiapoststore2",
    ])


@pytest.mark.parametrize("said,expected", [
    ("ajex store", "ajexautomation"),
    ("the ajex store", "ajexautomation"),
    ("ajexautomation2", "ajexautomation2"),
    ("moody store", "qa-moody-store"),
    ("indiapost store", "indiapoststore2"),
    ("mypostautomation", "mypostautomation-gs01o4wy"),
])
def test_a_store_is_found_from_history(slugs, said, expected):
    """Slugs carry a random suffix -- "mypostautomation-gs01o4wy" is not
    something anyone says aloud and cannot be constructed from the name, so
    history is the only source."""
    assert browser.resolve_store(said)[0] == expected


def test_an_exact_name_beats_a_prefix(slugs):
    """"ajexautomation2" must not open ajexautomation."""
    assert browser.resolve_store("ajexautomation2") == ["ajexautomation2"]


def test_an_ambiguous_name_returns_every_match(slugs):
    assert browser.resolve_store("ajex") == ["ajexautomation", "ajexautomation2"]


def test_an_unknown_store_is_reported_with_what_to_do(chrome, slugs, monkeypatch):
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": False)
    monkeypatch.setattr(browser, "open_url", lambda url, d, window="": True)
    ok, spoken = browser.open_page("banana store")
    assert ok is False
    assert "history" in spoken


def test_a_store_opens_in_the_office_profile_by_default(chrome, slugs, monkeypatch):
    """The office account is the one signed in to the stores, so anything
    work-shaped belongs there without having to say so."""
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": False)
    (chrome / "profiles.yaml").write_text("office: someone@company.com\n")
    opened = {}
    monkeypatch.setattr(browser, "open_url",
                        lambda url, d, window="": opened.update(url=url, directory=d) or True)
    ok, spoken = browser.open_page("the moody store")
    assert ok is True
    assert opened["directory"] == "Default"
    assert opened["url"] == "https://admin.shopify.com/store/qa-moody-store"
    assert "office" in spoken


def test_an_explicit_profile_wins(chrome, slugs, monkeypatch):
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": False)
    (chrome / "profiles.yaml").write_text(
        "office: someone@company.com\npersonal: first@gmail.com\n"
    )
    opened = {}
    monkeypatch.setattr(browser, "open_url",
                        lambda url, d, window="": opened.update(directory=d) or True)
    browser.open_page("the moody store", "personal")
    assert opened["directory"] == "Profile 5"


def test_ambiguity_is_said_out_loud(chrome, slugs, monkeypatch):
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": False)
    monkeypatch.setattr(browser, "open_url", lambda url, d, window="": True)
    ok, spoken = browser.open_page("ajex store")
    assert ok is True
    assert "ajexautomation" in spoken
    assert "2 that match" in spoken


def test_a_named_url_beats_a_store_search(chrome, monkeypatch):
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": False)
    (chrome / "urls.yaml").write_text("partner dashboard: https://partners.shopify.com\n")
    monkeypatch.setattr(browser, "URLS_CONFIG", str(chrome / "urls.yaml"))
    opened = {}
    monkeypatch.setattr(browser, "open_url",
                        lambda url, d, window="": opened.update(url=url) or True)
    ok, spoken = browser.open_page("the partner dashboard")
    assert ok is True
    assert opened["url"] == "https://partners.shopify.com"


def test_a_bare_url_is_opened_as_given(chrome, monkeypatch):
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": False)
    opened = {}
    monkeypatch.setattr(browser, "open_url",
                        lambda url, d, window="": opened.update(url=url) or True)
    ok, _ = browser.open_page("https://admin.shopify.com/store/x/orders")
    assert ok is True
    assert opened["url"] == "https://admin.shopify.com/store/x/orders"


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "file:///etc/passwd", "not a url", "",
    "data:text/html,<script>x</script>",
])
def test_only_http_urls_are_opened(bad):
    """A URL reaches the command line, and file:// or javascript: has no
    business being launched by a misheard sentence."""
    assert browser.open_url(bad, "Default") is False


# --- routing for pages --------------------------------------------------

@pytest.mark.parametrize("said,page,profile", [
    ("open the ajex store", "ajex store", ""),
    ("open the moody store in office chrome", "moody store", "office"),
    ("open the partner dashboard", "partner dashboard", ""),
    ("go to the indiapost store", "indiapost store", ""),
    ("open https://admin.shopify.com/store/x", "https://admin.shopify.com/store/x", ""),
])
def test_a_page_routes_to_the_page_handler(said, page, profile):
    found = match(said)
    assert found[0].name == "open_page"
    assert found[1]["page"] == page
    assert found[1]["profile"] == profile


@pytest.mark.parametrize("said,expected", [
    ("open slack", "open_app"),
    ("open the terminal", "open_app"),
    ("open finder", "open_app"),
    ("open office chrome", "chrome_profile"),
    ("open chrome", "chrome_profile"),
    ("bring office chrome to the front", "chrome_profile"),
])
def test_apps_and_profiles_are_not_read_as_pages(said, expected):
    """The phrase has to end in a page noun, or "open slack" becomes a page."""
    assert named(said) == expected


# --- tabs accumulate the same way windows did ---------------------------

def test_an_already_open_page_focuses_its_tab(chrome, slugs, monkeypatch):
    """Measured: "open the moody store" twice took the tab count from 80 to
    81. The window cache stops that a level up; this stops it a level down."""
    (chrome / "profiles.yaml").write_text("office: someone@company.com\n")
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": True)
    opened = []
    monkeypatch.setattr(browser, "open_url",
                        lambda url, d, window="": opened.append(url) or True)

    ok, spoken = browser.open_page("the moody store")
    assert ok is True
    assert "already open" in spoken
    assert opened == [], "should not have opened anything"


def test_a_page_that_is_not_open_is_opened(chrome, slugs, monkeypatch):
    (chrome / "profiles.yaml").write_text("office: someone@company.com\n")
    monkeypatch.setattr(browser, "focus_tab", lambda needle, prefer="": False)
    opened = []
    monkeypatch.setattr(browser, "open_url",
                        lambda url, d, window="": opened.append(url) or True)

    ok, spoken = browser.open_page("the moody store")
    assert ok is True and "Opening" in spoken
    assert opened == ["https://admin.shopify.com/store/qa-moody-store"]


def test_the_tab_needle_drops_the_query_string():
    """Shopify appends an appLoadId to store URLs, so matching the whole thing
    would never hit an open tab."""
    assert browser._needle(
        "https://admin.shopify.com/store/x/apps/mcsl-qa?appLoadId=d708db3f"
    ) == "admin.shopify.com/store/x/apps/mcsl-qa"


@pytest.mark.parametrize("url,expected", [
    ("https://a.com/b/", "a.com/b"),
    ("http://a.com/b#frag", "a.com/b"),
    ("https://a.com", "a.com"),
])
def test_needle_normalisation(url, expected):
    assert browser._needle(url) == expected


@pytest.mark.parametrize("bad", ['has"quote', "has\\backslash", ""])
def test_a_needle_that_could_break_the_script_is_refused(bad):
    assert browser.focus_tab(bad) is False


# --- adding a tab beats asking for a new instance -----------------------

def test_a_known_window_gets_a_new_tab_not_a_new_instance(monkeypatch):
    """`open -na` asks macOS for a new instance; used repeatedly against a
    running Chrome it restarted the browser and took three windows of tabs
    with it. Adding a tab touches only the running instance."""
    monkeypatch.setattr(browser, "window_ids", lambda: {"42"})
    monkeypatch.setattr(browser, "new_tab", lambda wid, url: wid == "42")
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a))

    assert browser.open_url("https://x.com/y", "Default", "42") is True
    assert launched == [], "should not have asked for a new instance"


def test_no_window_falls_back_to_a_new_instance(monkeypatch):
    """The one case that genuinely needs it: a profile with no window at all."""
    monkeypatch.setattr(browser, "window_ids", lambda: set())
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a[0]))

    assert browser.open_url("https://x.com/y", "Default", "") is True
    assert any("-na" in cmd for cmd in launched)


def test_a_dead_window_id_falls_back_to_a_new_instance(monkeypatch):
    monkeypatch.setattr(browser, "window_ids", lambda: {"99"})
    monkeypatch.setattr(browser, "new_tab", lambda wid, url: pytest.fail("stale id used"))
    launched = []
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: launched.append(a[0]))

    assert browser.open_url("https://x.com/y", "Default", "42") is True
    assert launched


@pytest.mark.parametrize("wid,url", [
    ("abc", "https://x.com"), ("42", "javascript:alert(1)"),
    ("42", 'https://x.com/"quote'), ("", "https://x.com"),
])
def test_new_tab_refuses_anything_unsafe(wid, url):
    assert browser.new_tab(wid, url) is False


# --- how a store is named out loud --------------------------------------

@pytest.mark.parametrize("slug,expected", [
    ("qa-moody-store", "the qa-moody-store"),
    ("ajexautomation", "the ajexautomation store"),
    ("indiapoststore2", "the indiapoststore2 store"),
])
def test_a_slug_that_already_says_store_does_not_get_another(slug, expected):
    """"the qa-moody-store store" is what appending it unconditionally gives,
    and it is read aloud exactly that way."""
    assert browser.store_name(slug) == expected

import pytest

from jarvis.eyes import document as docs
from jarvis.eyes import window as eyes


# --- picking the window a person means ------------------------------------

def _win(app="Chrome", layer=0, w=1200, h=800, wid=42, title=""):
    return {
        "kCGWindowLayer": layer, "kCGWindowNumber": wid,
        "kCGWindowOwnerName": app, "kCGWindowName": title,
        "kCGWindowBounds": {"Width": w, "Height": h},
    }


def test_the_first_normal_window_is_the_frontmost():
    """CGWindowListCopyWindowInfo returns front-to-back order."""
    picked = eyes.pick([_win(app="Chrome", wid=1), _win(app="Slack", wid=2)])
    assert picked.app == "Chrome"
    assert picked.id == 1


def test_menu_bar_and_overlay_layers_are_skipped():
    """Observed live: the top three on-screen windows were a 1470x33 menu-bar
    extra, the Window Server menubar, and a 1470x32 strip -- none of them the
    window being asked about."""
    picked = eyes.pick([
        _win(app="Claude", layer=26, w=1470, h=33, wid=1),
        _win(app="Window Server", layer=24, w=1470, h=33, wid=2),
        _win(app="Claude", layer=0, w=1470, h=923, wid=3),
    ])
    assert picked.id == 3


def test_tiny_windows_are_skipped():
    picked = eyes.pick([_win(w=180, h=40, wid=1), _win(w=900, h=600, wid=2)])
    assert picked.id == 2


def test_jarvis_own_hud_is_never_the_answer():
    """The dashboard is on screen whenever Jarvis is awake."""
    picked = eyes.pick([_win(app="Jarvis", wid=1), _win(app="Chrome", wid=2)])
    assert picked.app == "Chrome"


def test_no_candidates_is_none_not_an_exception():
    assert eyes.pick([]) is None
    assert eyes.pick(None) is None
    assert eyes.pick([_win(layer=26)]) is None


def test_a_window_with_no_id_is_skipped():
    bad = _win(wid=1)
    del bad["kCGWindowNumber"]
    assert eyes.pick([bad, _win(wid=7)]).id == 7


def test_describe_names_the_app_and_title():
    assert "Chrome" in eyes.Window(1, "Chrome", "Trello", 900, 600).describe()
    assert "Trello" in eyes.Window(1, "Chrome", "Trello", 900, 600).describe()


def test_describe_does_not_repeat_the_app_name():
    assert eyes.Window(1, "Preview", "Preview", 900, 600).describe() == "Preview"
    assert eyes.Window(1, "Preview", "", 900, 600).describe() == "Preview"


def test_look_says_something_speakable_when_there_is_no_window(monkeypatch):
    """The caller has to say the failure out loud, so it must be a sentence."""
    monkeypatch.setattr(eyes, "frontmost", lambda: None)
    with pytest.raises(RuntimeError) as err:
        eyes.look()
    assert "window" in str(err.value).lower()


def test_a_capture_failure_mentions_the_permission(monkeypatch):
    """A screenshot that silently returns the wallpaper is the usual symptom of
    missing Screen Recording permission, so name it."""
    monkeypatch.setattr(eyes, "frontmost", lambda: eyes.Window(1, "Chrome", "", 900, 600))

    def boom(win, path=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(eyes, "capture", boom)
    with pytest.raises(RuntimeError) as err:
        eyes.look()
    assert "Screen Recording" in str(err.value)


# --- finding the document in front ----------------------------------------

def test_a_browser_gives_a_url(monkeypatch):
    monkeypatch.setattr(docs, "_osascript", lambda s, timeout=8.0: "https://trello.com/c/abc")
    doc = docs.frontmost("Google Chrome")
    assert doc.kind == "url"
    assert doc.readable is True


def test_a_real_file_path_is_a_file(monkeypatch, tmp_path):
    target = tmp_path / "guide.pdf"
    target.write_text("x")
    monkeypatch.setattr(docs, "_osascript", lambda s, timeout=8.0: str(target))
    doc = docs.frontmost("Preview")
    assert doc.kind == "file"
    assert doc.ref == str(target)


def test_a_path_that_does_not_exist_is_only_a_name(monkeypatch):
    monkeypatch.setattr(docs, "_osascript", lambda s, timeout=8.0: "/gone/missing.pdf")
    doc = docs.frontmost("Preview")
    assert doc.kind == "name"
    assert doc.readable is False


def test_an_app_that_will_not_answer_gives_nothing(monkeypatch):
    """AppleScript returns non-zero when the app is not running or the user
    declined the control prompt. Observed: Preview replied "Application isn't
    running. (-600)"."""
    monkeypatch.setattr(docs, "_osascript", lambda s, timeout=8.0: "")
    assert docs.frontmost("Preview") is None


def test_an_unknown_app_gives_nothing():
    assert docs.frontmost("Some Random App") is None
    assert docs.frontmost("") is None


@pytest.mark.parametrize("title,expected", [
    ("handlers.py - jarvis - Visual Studio Code", "handlers.py"),
    ("● core.py - jarvis", "core.py"),
    ("MCSL_383_Support_Guide.md — Documents", "MCSL_383_Support_Guide.md"),
])
def test_an_editor_title_yields_the_filename(title, expected):
    assert docs.from_title(title).ref == expected


@pytest.mark.parametrize("title", ["Visual Studio Code", "", "Untitled"])
def test_a_title_with_no_filename_yields_nothing(title):
    assert docs.from_title(title) is None


def test_an_editor_is_read_from_its_title_not_applescript(monkeypatch):
    monkeypatch.setattr(docs, "_osascript", lambda s, timeout=8.0: pytest.fail("should not run"))
    doc = docs.frontmost("Code", "vision.py - jarvis - Visual Studio Code")
    assert doc.ref == "vision.py"
    assert doc.readable is False       # a bare name is not enough to open


@pytest.mark.parametrize("raw,expected", [
    ("alias Macintosh HD:Users:madan:x.pdf", "/Users/madan/x.pdf"),
    ("file Macintosh HD:Users:madan:x.pdf", "/Users/madan/x.pdf"),
    ("/Users/madan/x.pdf", "/Users/madan/x.pdf"),
])
def test_applescript_path_spellings_all_normalise(raw, expected):
    """Older scripting dictionaries return HFS colon paths."""
    assert docs._as_path(raw) == expected


# --- the helper-window trap ------------------------------------------------

# Verbatim from a live CGWindowList dump with one page open in Chrome. Front to
# back: a transparent menu-bar extra, the Window Server menubar, three untitled
# Chrome helper windows, and last of all the actual content window.
REAL_CHROME = [
    {"kCGWindowLayer": 26, "kCGWindowNumber": 16777, "kCGWindowOwnerName": "Google Chrome",
     "kCGWindowName": "", "kCGWindowAlpha": 0.0, "kCGWindowBounds": {"Width": 1470, "Height": 33}},
    {"kCGWindowLayer": 24, "kCGWindowNumber": 189, "kCGWindowOwnerName": "Window Server",
     "kCGWindowName": "Menubar", "kCGWindowAlpha": 1.0, "kCGWindowBounds": {"Width": 1470, "Height": 33}},
    {"kCGWindowLayer": 0, "kCGWindowNumber": 130, "kCGWindowOwnerName": "Google Chrome",
     "kCGWindowName": "", "kCGWindowAlpha": 1.0, "kCGWindowBounds": {"Width": 1470, "Height": 41}},
    {"kCGWindowLayer": 0, "kCGWindowNumber": 129, "kCGWindowOwnerName": "Google Chrome",
     "kCGWindowName": "", "kCGWindowAlpha": 1.0, "kCGWindowBounds": {"Width": 1470, "Height": 81}},
    {"kCGWindowLayer": 0, "kCGWindowNumber": 176, "kCGWindowOwnerName": "Google Chrome",
     "kCGWindowName": "", "kCGWindowAlpha": 1.0, "kCGWindowBounds": {"Width": 1470, "Height": 158}},
    {"kCGWindowLayer": 0, "kCGWindowNumber": 126, "kCGWindowOwnerName": "Google Chrome",
     "kCGWindowName": "req.html", "kCGWindowAlpha": 1.0, "kCGWindowBounds": {"Width": 1470, "Height": 801}},
]


def test_the_content_window_wins_over_its_apps_helper_windows():
    """Regression, found only end to end: front-to-back order chose the 158px
    helper and Jarvis answered that the screen was blank."""
    picked = eyes.pick(REAL_CHROME)
    assert picked.id == 126
    assert picked.title == "req.html"


def test_a_fully_transparent_overlay_is_skipped():
    assert eyes.pick([_win(wid=1, w=900, h=600) | {"kCGWindowAlpha": 0.0},
                      _win(wid=2, w=900, h=600)]).id == 2


def test_the_frontmost_app_wins_even_if_a_background_window_is_bigger():
    """A maximised Slack window behind the browser is not what "this" means."""
    picked = eyes.pick([
        _win(app="Google Chrome", wid=1, w=900, h=600, title="Trello"),
        _win(app="Slack", wid=2, w=2000, h=1400, title="Slack"),
    ])
    assert picked.app == "Google Chrome"


def test_size_decides_when_no_window_has_a_title():
    """Titles need Screen Recording permission, so the size rule stands alone."""
    picked = eyes.pick([
        _win(wid=1, w=1470, h=158), _win(wid=2, w=1470, h=801), _win(wid=3, w=1470, h=200),
    ])
    assert picked.id == 2

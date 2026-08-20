"""Open macOS applications by spoken name.

Local, not a worker call: launching an app has nothing to do with any Domain
Expert repo. Harmless and reversible, so no spoken confirmation -- unlike a
Slack message, closing an app you did not want costs a click.
"""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

SEARCH_DIRS = (
    "/Applications",
    "~/Applications",                                # per-user installs
    "/System/Applications",
    "/System/Applications/Utilities",
    # 12 user-facing apps, and the only home of Keychain Access on this macOS --
    # it is no longer in Utilities, which is why "keychain access" resolved to
    # nothing. Deliberately NOT its parent /System/Library/CoreServices, which
    # holds 117 internal agents (AirPlayUIAgent, AddressBookUrlForwarder,
    # AccessibilityUIServer). Putting those in the pool would let one mishear
    # launch a system daemon.
    "/System/Library/CoreServices/Applications",
)

# Apps that live outside every scanned directory, or are said by a name the
# bundle does not have.
EXTRA_APPS = {
    "Finder": "/System/Library/CoreServices/Finder.app",
}
# Measured: real mishears score 0.89-0.91 ("slak"->slack, "sfari"->safari),
# while unrelated words reach 0.67 ("chrom"->home). 0.8 separates them, and
# opening the wrong app is more confusing than admitting no match.
MATCH_CUTOFF = 0.8

# How the names are actually said aloud, where that is not a substring of the
# bundle name. "vs code" will never fuzzy-match "Visual Studio Code".
ALIASES = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "browser": "Google Chrome",
    "terminal": "Terminal",
    "mail": "Mail",
    "calendar": "Calendar",
    "activity monitor": "Activity Monitor",
    # Renamed in macOS Ventura, still said the old way.
    "system preferences": "System Settings",
    "preferences": "System Settings",
    "settings": "System Settings",
    "keychain": "Keychain Access",
    "finder": "Finder",
    "files": "Finder",
    "vs": "Visual Studio Code",
    "editor": "Visual Studio Code",
    "notes": "Notes",
    "music": "Music",
    "spotify": "Spotify",
    "whatsapp": "WhatsApp",
    "teams": "Microsoft Teams",
    "excel": "Microsoft Excel",
    "word": "Microsoft Word",
    "outlook": "Microsoft Outlook",
}


def installed() -> list[str]:
    """Application names, without the .app suffix.

    One level of nesting is included so vendor folders work -- Setapp,
    "Microsoft Office", "Adobe Creative Cloud" all bury their apps a directory
    down. There are none on this machine today, which is exactly why it is worth
    handling before there are.
    """
    names: set[str] = set()
    for directory in SEARCH_DIRS:
        d = Path(directory).expanduser()
        if not d.is_dir():
            continue
        for app in d.glob("*.app"):
            names.add(app.stem)
        for app in d.glob("*/*.app"):
            # A bundle inside a bundle is a helper, not something to launch.
            if ".app/" not in str(app):
                names.add(app.stem)
    for name, path in EXTRA_APPS.items():
        if Path(path).exists():
            names.add(name)
    return sorted(names)


def spotlight(spoken: str) -> str:
    """Ask macOS's own index for an app, for anything the directories missed.

    A fallback rather than the primary source. Spotlight knows about 321 app
    bundles here against 76 in the scanned directories, and most of that
    difference is internal helpers -- a pool that size makes a fuzzy mishear
    much more likely to land somewhere surprising. Used only once the curated
    list has failed, so precision comes first and reach second. Measured at
    under 100ms.
    """
    wanted = (spoken or "").strip().removeprefix("the ").strip()
    if not wanted:
        return ""
    try:
        done = subprocess.run(
            ["mdfind", "-onlyin", "/",
             "kMDItemContentType == 'com.apple.application-bundle' && "
             f'kMDItemFSName == "{wanted}.app"c'],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return ""
    if done.returncode != 0:
        return ""
    for line in done.stdout.splitlines():
        path = Path(line.strip())
        # Skip helpers nested inside another bundle.
        if path.name.endswith(".app") and ".app/" not in str(path):
            return path.stem
    return ""


def resolve(spoken: str) -> str:
    """Map a spoken name to an installed app, or "" if nothing is close.

    Speech gives "slack", "the terminal", "google chrome" -- so match
    case-insensitively on substrings first, then fall back to fuzzy matching.
    """
    wanted = (spoken or "").strip().lower()
    wanted = wanted.removeprefix("the ").strip()
    if not wanted:
        return ""

    apps = installed()
    lowered = {a.lower(): a for a in apps}

    aliased = ALIASES.get(wanted)
    if aliased and aliased.lower() in lowered:
        return lowered[aliased.lower()]

    if wanted in lowered:
        return lowered[wanted]
    # "chrome" should find "Google Chrome"; prefer the shortest such match so
    # "code" does not land on something incidental.
    contains = sorted((a for a in apps if wanted in a.lower()), key=len)
    if contains:
        return contains[0]

    close = difflib.get_close_matches(wanted, list(lowered), n=1, cutoff=MATCH_CUTOFF)
    if close:
        return lowered[close[0]]

    # Last resort: ask Spotlight, which finds apps in places nobody scans.
    return spotlight(wanted)


def open_app(name: str) -> tuple[bool, str]:
    """Launch an app. Returns (ok, what to say)."""
    app = resolve(name)
    if not app:
        return False, f"I couldn't find an app called {name}."
    try:
        result = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return False, f"Couldn't open {app}: {exc}"
    if result.returncode != 0:
        return False, f"Couldn't open {app}."
    return True, f"Opening {app}."


# --- bringing things to the front ----------------------------------------

# Apps whose own AppleScript dictionary understands windows, so a *specific*
# window can be raised rather than just the app. Electron apps are absent on
# purpose: Slack and Visual Studio Code both answer "every window doesn't
# understand the count message", so for those the app is the finest granularity
# available without Accessibility permission.
SCRIPTABLE = {
    "Google Chrome": 'title of active tab of w',
    "Chromium": 'title of active tab of w',
    "Brave Browser": 'title of active tab of w',
    "Safari": 'name of w',
    "Terminal": 'name of w',
    "Finder": 'name of w',
    "Preview": 'name of w',
    "TextEdit": 'name of w',
}


def focus_app(name: str) -> tuple[bool, str]:
    """Bring an app to the front. Launches it if it is not running.

    `open -a` activates a running app rather than starting a second copy, so
    this is the same call as opening -- only the wording differs, because
    "bring Slack front" and "open Slack" want different confirmations.
    """
    app = resolve(name)
    if not app:
        return False, f"I couldn't find an app called {name}."
    try:
        done = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return False, f"Couldn't bring {app} forward: {exc}"
    if done.returncode != 0:
        return False, f"Couldn't bring {app} forward."
    return True, f"{app} is at the front."


def _as_string(text: str) -> str:
    """Escape a value for interpolation into an AppleScript string literal.

    Window titles are page-authored -- "Payment Gateway | PhonePe" was on screen
    while this was written -- so a title containing a quote or a backslash would
    otherwise end the literal early and break the script.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _raise_in_app(app: str, needle: str) -> bool:
    """Raise the matching window inside a scriptable app. True if it did."""
    prop = SCRIPTABLE.get(app)
    if not prop:
        return False
    needle = _as_string(needle)
    # `set index to 1` is what actually reorders; activate alone raises whatever
    # window was already frontmost for that app.
    script = f'''
    tell application "{_as_string(app)}"
      repeat with w in windows
        if ({prop}) contains "{needle}" then
          set index of w to 1
          activate
          return "yes"
        end if
      end repeat
      return "no"
    end tell'''
    try:
        done = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return False
    return done.returncode == 0 and done.stdout.strip() == "yes"


def find_window(phrase: str) -> object | None:
    """The on-screen window whose title or app best matches what was said."""
    from jarvis.eyes.window import all_windows

    wanted = (phrase or "").strip().lower()
    wanted = wanted.removeprefix("the ").removesuffix(" window").strip()
    if not wanted:
        return None

    windows = all_windows()
    # A title substring is the strongest signal: "bring the 383 window front"
    # should find "MCSL 383 Support Guide" and not merely raise Chrome.
    for w in windows:
        if wanted in (w.title or "").lower():
            return w
    for w in windows:
        if wanted in w.app.lower():
            return w
    titles = {(w.title or w.app).lower(): w for w in windows}
    close = difflib.get_close_matches(wanted, list(titles), n=1, cutoff=MATCH_CUTOFF)
    return titles[close[0]] if close else None


# "Bring this window front" names nothing. Searching for a window called "this"
# and reporting failure is the worst of the available answers.
_DEMONSTRATIVE = re.compile(r"^(?:the\s+)?(?:this|that|it)(?:\s+one)?(?:\s+window)?$", re.I)


def focus_window(phrase: str) -> tuple[bool, str]:
    """Raise a particular window by what it is called.

    Falls back to raising the owning app, which is as far as macOS will go for
    an Electron app without Accessibility permission -- and says so, rather than
    claiming to have done something it did not.
    """
    if _DEMONSTRATIVE.match((phrase or "").strip()):
        from jarvis.eyes.window import frontmost

        win = frontmost()
        if win is None:
            return False, "I can't see a window in front. Which one do you mean?"
        # It is already the front window, so the useful reply is to name it and
        # let the next sentence correct the assumption.
        return True, (f"{win.describe()} is already at the front. "
                      "Name the app or the window if you meant a different one.")

    found = find_window(phrase)
    if found is None:
        ok, said = focus_app(phrase)
        return (ok, said) if ok else (False, f"I can't see a window called {phrase}.")

    if found.title and _raise_in_app(found.app, found.title):
        return True, f"{found.title} is at the front."

    ok, said = focus_app(found.app)
    if not ok:
        return False, said

    # The caveat is only worth saying when a *window* was asked for. "Switch to
    # Slack" names the app, and answering it by reading out
    # "dev-interns-qa-regression (Channel) - PluginHive - 7 new items - Slack"
    # is noise: the request was already satisfied.
    wanted = (phrase or "").strip().lower().removeprefix("the ").removesuffix(" window").strip()
    if wanted and wanted not in found.app.lower():
        return True, f"{found.app} is at the front, but I can only raise the app, not that window."
    return True, said


def minimise_front() -> tuple[bool, str]:
    """Send the frontmost window to the Dock."""
    from jarvis.eyes.window import frontmost

    win = frontmost()
    if win is None:
        return False, "I can't see a window in front to minimise."
    if win.app not in SCRIPTABLE:
        return False, (f"{win.app} won't let me minimise its window without "
                       "Accessibility permission.")
    try:
        done = subprocess.run(
            ["osascript", "-e",
             f'tell application "{_as_string(win.app)}" to set minimized of front window to true'],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        return False, f"Couldn't minimise {win.app}: {exc}"
    if done.returncode != 0:
        return False, f"Couldn't minimise {win.app}."
    return True, f"Minimised {win.describe()}."

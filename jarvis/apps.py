"""Open macOS applications by spoken name.

Local, not a worker call: launching an app has nothing to do with any Domain
Expert repo. Harmless and reversible, so no spoken confirmation -- unlike a
Slack message, closing an app you did not want costs a click.
"""
from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

SEARCH_DIRS = ("/Applications", "/System/Applications", "/System/Applications/Utilities")
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
}


def installed() -> list[str]:
    """Application names, without the .app suffix."""
    names: set[str] = set()
    for directory in SEARCH_DIRS:
        d = Path(directory)
        if not d.is_dir():
            continue
        for app in d.glob("*.app"):
            names.add(app.stem)
    return sorted(names)


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
    return lowered[close[0]] if close else ""


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

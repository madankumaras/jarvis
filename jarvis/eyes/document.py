"""What document is open in front, as a path or a URL.

Reading the real file beats reading a photograph of it. A screenshot shows one
visible page, misreads dense tables, and cannot scroll; the file itself is whole
and exact. So "go through this doc" asks the frontmost app what it has open, and
only falls back to a screenshot when the app will not say.

Each app needs its own AppleScript because there is no general "what document is
open" API. None of these need Accessibility permission -- they talk to the app's
own scripting dictionary, not to the window server. The first time one runs,
macOS asks to allow control of that app; declining leaves the fallback.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

# Browsers give a URL, document apps give a file path. Order is irrelevant --
# only the frontmost app is consulted.
_SCRIPTS: dict[str, str] = {
    "Google Chrome": 'tell application "Google Chrome" to return URL of active tab of front window',
    "Chromium": 'tell application "Chromium" to return URL of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to return URL of active tab of front window',
    "Arc": 'tell application "Arc" to return URL of active tab of front window',
    "Safari": 'tell application "Safari" to return URL of front document',
    "Preview": 'tell application "Preview" to return path of front document',
    "TextEdit": 'tell application "TextEdit" to return path of front document',
    "Pages": 'tell application "Pages" to return file of front document',
    "Numbers": 'tell application "Numbers" to return file of front document',
    "Microsoft Word": 'tell application "Microsoft Word" to return full name of active document',
    "Microsoft Excel": 'tell application "Microsoft Excel" to return full name of active workbook',
}

# Editors show a path in the window title, which is cheaper and more reliable
# than their extension APIs. VS Code titles read "file.py - project - Visual
# Studio Code"; the first segment is the file, and the workspace folder is not
# in the title, so the name alone is what we get.
_TITLE_APPS = {"Code", "Cursor", "Windsurf", "Sublime Text", "Zed"}


@dataclass(frozen=True)
class Doc:
    kind: str          # "file" | "url" | "name"
    ref: str           # the path, the URL, or a bare filename
    app: str = ""

    @property
    def readable(self) -> bool:
        """Whether the reference is enough to fetch the content."""
        return self.kind in ("file", "url")

    def describe(self) -> str:
        if self.kind == "url":
            return self.ref
        return os.path.basename(self.ref) or self.ref


def _osascript(script: str, timeout: float = 8.0) -> str:
    try:
        done = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return ""
    if done.returncode != 0:
        return ""
    return (done.stdout or "").strip()


def _as_path(raw: str) -> str:
    """AppleScript hands back several path spellings for the same file.

    "alias Macintosh HD:Users:madan:x.pdf" and "file ..." are HFS colon paths
    from older scripting dictionaries; a POSIX path comes back unchanged.
    """
    text = raw.strip()
    for prefix in ("alias ", "file "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.startswith("/") or text.startswith("~"):
        return os.path.expanduser(text)
    if ":" in text and "/" not in text:
        parts = [p for p in text.split(":") if p]
        # The first component is the volume name, which is not part of the path.
        return "/" + "/".join(parts[1:]) if len(parts) > 1 else ""
    return text


def from_title(title: str) -> Doc | None:
    """Pull a filename out of an editor window title."""
    head = (title or "").split(" — ")[0].split(" - ")[0].strip()
    head = head.lstrip("●•* ").strip()      # unsaved-changes markers
    if not head or "." not in head:
        return None
    return Doc(kind="name", ref=head)


def frontmost(app: str, title: str = "") -> Doc | None:
    """What the given frontmost app has open, or None if it will not say."""
    if not app:
        return None
    if app in _TITLE_APPS:
        doc = from_title(title)
        return Doc(kind=doc.kind, ref=doc.ref, app=app) if doc else None

    script = _SCRIPTS.get(app)
    if not script:
        return None
    raw = _osascript(script)
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "file://")):
        return Doc(kind="url", ref=raw, app=app)
    path = _as_path(raw)
    if path and os.path.exists(path):
        return Doc(kind="file", ref=path, app=app)
    return Doc(kind="name", ref=path or raw, app=app) if (path or raw) else None

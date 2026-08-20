"""Find and photograph the window you are actually looking at.

Only the frontmost window is captured, never the whole screen. "Is this request
correct?" is a question about one window; capturing the desktop would also send
whatever Slack thread, terminal, or password manager happens to be visible
behind it. The narrow shot is smaller, cheaper, and leaks less.

Two macOS permissions are relevant and only one is needed. Screen Recording is
required (System Settings -> Privacy & Security -> Screen Recording) or the
capture comes back as a picture of the desktop wallpaper. Accessibility is *not*
required: window geometry comes from CoreGraphics, which any process may read.
Going through System Events instead fails with "osascript is not allowed
assistive access" until the user grants it by hand, so it is avoided.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

# Windows below this are menu-bar extras, notification banners and tooltips, not
# anything a person means by "this window". The tallest chrome seen is 33px.
MIN_WIDTH = 200
MIN_HEIGHT = 120

# Our own HUD is on screen whenever Jarvis is awake, and it is never the answer
# to "look at this".
IGNORED_APPS = {"Jarvis", "Window Server", "Dock", "Notification Center"}


@dataclass(frozen=True)
class Window:
    id: int
    app: str
    title: str
    width: int
    height: int

    def describe(self) -> str:
        """What Jarvis says it looked at, so you always know what was sent."""
        if self.title and self.title != self.app:
            return f"{self.app}, {self.title}"
        return self.app


def _candidates(windows: list[dict]) -> list[Window]:
    """Every window that could plausibly be the one a person means."""
    out: list[Window] = []
    for w in windows or []:
        if w.get("kCGWindowLayer") != 0:      # 0 is the normal document layer
            continue
        app = str(w.get("kCGWindowOwnerName") or "")
        if app in IGNORED_APPS:
            continue
        alpha = w.get("kCGWindowAlpha")
        if alpha is not None and float(alpha) <= 0:   # fully transparent overlay
            continue
        bounds = w.get("kCGWindowBounds") or {}
        width = int(bounds.get("Width") or 0)
        height = int(bounds.get("Height") or 0)
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            continue
        wid = w.get("kCGWindowNumber")
        if wid is None:
            continue
        out.append(Window(
            id=int(wid), app=app,
            title=str(w.get("kCGWindowName") or ""),
            width=width, height=height,
        ))
    return out


def pick(windows: list[dict]) -> Window | None:
    """Choose the window a person means from a CGWindowList dump.

    Front-to-back order alone is wrong, which only showed up end to end. Chrome
    keeps several layer-0 windows of its own: with one page open the list was a
    1470x41, a 1470x81, a 1470x158 and finally the 1470x801 content window
    titled "req.html". Taking the first survivor photographed the 158px helper
    and Jarvis reported the screen was blank.

    So: keep to the frontmost application, then take its largest window, and
    prefer a window that has a title -- helper windows are untitled and the
    document window is both titled and biggest. Titles need Screen Recording
    permission to read, so the size rule has to stand on its own.
    """
    found = _candidates(windows)
    if not found:
        return None
    front = found[0].app                       # the app that owns the frontmost window
    mine = [w for w in found if w.app == front]
    titled = [w for w in mine if w.title]
    return max(titled or mine, key=lambda w: w.width * w.height)


def _dump(every_space: bool = False) -> list[dict]:
    """Raw CGWindowList, or an empty list if CoreGraphics is unavailable.

    "On screen" means the current Space, not merely unoccluded. With Chrome one
    desktop over, the on-screen list held a single window -- Claude's -- while
    the full list held 40 across 14 apps. So looking at the screen wants the
    on-screen list, and finding a window by name wants all of them: the whole
    point of "bring Slack front" is that Slack is not in front.
    """
    try:
        import Quartz
    except Exception:
        return []
    try:
        scope = (
            Quartz.kCGWindowListOptionAll if every_space
            else Quartz.kCGWindowListOptionOnScreenOnly
        )
        opts = scope | Quartz.kCGWindowListExcludeDesktopElements
        return list(Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID))
    except Exception:
        return []


def frontmost() -> Window | None:
    """The window in front right now, or None if CoreGraphics is unavailable."""
    return pick(_dump())


def all_windows() -> list[Window]:
    """Every window a person could mean, including other Spaces, front to back.

    Works for every app, including the Electron ones whose AppleScript
    dictionaries have no notion of a window at all -- Slack and VS Code both
    answer "every window doesn't understand the count message", yet their
    titles appear here.
    """
    return _candidates(_dump(every_space=True))


def capture(window: Window, path: str | None = None) -> str:
    """Photograph one window to a PNG and return the path.

    -x suppresses the shutter sound, which would otherwise fire into a live
    microphone; -o drops the drop-shadow, which is a few hundred KB of blur.
    """
    out = path or os.path.join(tempfile.gettempdir(), f"jarvis-look-{window.id}.png")
    subprocess.run(
        ["screencapture", "-x", "-o", f"-l{window.id}", "-t", "png", out],
        check=True, capture_output=True, timeout=20,
    )
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError("capture produced no image")
    return out


def look() -> tuple[str, Window]:
    """Capture whatever is in front. Raises RuntimeError with a spoken-ready
    reason when it cannot, because the caller has to say something out loud."""
    win = frontmost()
    if win is None:
        raise RuntimeError("I cannot see any window in front right now.")
    try:
        return capture(win), win
    except Exception as exc:
        # Any capture failure gets the same advice. A person cannot tell a
        # denied permission from a timeout, and the permission is by far the
        # likeliest cause -- when it is missing, screencapture succeeds and
        # quietly hands back a picture of the wallpaper instead.
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(
            "I could not take the screenshot. Check Screen Recording permission "
            "in System Settings, Privacy and Security."
            + (f" ({detail or exc})" if (detail or str(exc)) else "")
        ) from exc

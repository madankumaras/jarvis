"""Chrome profiles by what you call them: "open office chrome".

Three facts about Chrome shape all of this, each measured rather than assumed.

**Profiles are only identifiable by email.** All three profiles on this machine
carry the display name "Madan", so `info_cache[dir]["name"]` is useless and
`user_name` -- the account email -- is the only key.

**A profile's windows cannot be identified after the fact.** Every Chrome window
shares one process (pid 2306 for all of them here) and its title is the page
title with no profile marker. So there is no way to look at the screen and say
which window is the office one.

**`open -na --profile-directory` accumulates.** Called for a profile that
already has a window, it opened a fifth window rather than focusing the fourth.
Repeating "open office chrome" would pile up windows.

Together those force the design: when Jarvis launches a profile it diffs
Chrome's own window ids across the launch, and remembers which id belongs to
which profile. Next time it raises that id directly. Chrome's window ids are
stable and `first window whose id is N` is addressable, which is what makes
raise-instead-of-relaunch possible at all.

The label-to-email mapping lives in ~/.jarvis/chrome_profiles.yaml, never in
this repository -- the repository is public and those are real addresses.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

LOCAL_STATE = "~/Library/Application Support/Google/Chrome/Local State"
CONFIG = "~/.jarvis/chrome_profiles.yaml"
WINDOW_CACHE = "~/.jarvis/chrome_windows.json"

# Mail domains that belong to a person rather than an employer. Used only to
# guess an initial labelling, which the config file then overrides.
FREE_MAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
}

# How each label is actually said. "Browser" is included because "office
# browser" is at least as natural as "office chrome".
ALIASES = {
    "office": ("office", "work", "official", "company"),
    "personal": ("personal", "personal one", "personal 1", "my own"),
    "personal 2": ("personal 2", "personal two", "second personal",
                   "other personal", "2nd personal"),
}


def _read_json(path: str) -> dict:
    try:
        with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except Exception:
        return {}


def profiles() -> dict[str, str]:
    """Profile directory -> account email, in Chrome's own order."""
    cache = (_read_json(LOCAL_STATE).get("profile") or {}).get("info_cache") or {}
    out: dict[str, str] = {}
    for directory, meta in cache.items():
        if isinstance(meta, dict):
            out[directory] = str(meta.get("user_name") or "")
    return out


def _domain(email: str) -> str:
    return email.rpartition("@")[2].lower()


def derive_labels(found: dict[str, str]) -> dict[str, str]:
    """Guess label -> email, for seeding the config on first use.

    An address at a domain nobody can sign up for is the work one, which is the
    label that matters most here: the office profile is the one signed in to the
    Shopify stores. Personal accounts are numbered in Chrome's own order, which
    is arbitrary -- so the config file exists to correct it.
    """
    labels: dict[str, str] = {}
    personal: list[str] = []
    for email in found.values():
        if not email:
            continue
        if _domain(email) and _domain(email) not in FREE_MAIL:
            labels.setdefault("office", email)
        else:
            personal.append(email)
    for index, email in enumerate(personal):
        labels["personal" if index == 0 else f"personal {index + 1}"] = email
    return labels


def _parse_yaml(text: str) -> dict[str, str]:
    """One `key: value` per line. A whole YAML parser would be overkill."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().strip("\"'"), value.strip().strip("\"'")
        if key and value:
            out[key.lower()] = value
    return out


def labels() -> dict[str, str]:
    """Label -> email, from the config file, seeded from Chrome on first use."""
    path = Path(os.path.expanduser(CONFIG))
    if path.exists():
        stored = _parse_yaml(path.read_text(encoding="utf-8", errors="replace"))
        if stored:
            return stored

    derived = derive_labels(profiles())
    if derived:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            body = [
                "# Which Chrome profile Jarvis means by each name.",
                "# Edit freely -- say the label, get the profile.",
                "# Kept out of the repository: these are real addresses.",
                "",
            ]
            body += [f"{label}: {email}" for label, email in derived.items()]
            path.write_text("\n".join(body) + "\n", encoding="utf-8")
        except OSError:
            pass
    return derived


def resolve_profile(phrase: str) -> tuple[str, str, str]:
    """Spoken phrase -> (profile directory, label, email). Empty if no match."""
    said = (phrase or "").strip().lower()
    if not said:
        return "", "", ""

    known = labels()
    found = profiles()
    by_email = {email.lower(): d for d, email in found.items() if email}

    # Longest label first, so "personal 2" is not swallowed by "personal".
    for label in sorted(known, key=len, reverse=True):
        spoken = ALIASES.get(label, (label,))
        if not any(word in said for word in spoken) and label not in said:
            continue
        directory = by_email.get(known[label].lower(), "")
        if directory:
            return directory, label, known[label]

    # An email or its local part said outright.
    for email, directory in by_email.items():
        if email in said or email.partition("@")[0] in said:
            return directory, "", email
    return "", "", ""


# --- window bookkeeping ---------------------------------------------------

def _osascript(script: str, timeout: float = 20.0) -> str:
    try:
        done = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return ""
    return (done.stdout or "").strip() if done.returncode == 0 else ""


def window_ids() -> set[str]:
    raw = _osascript('tell application "Google Chrome" to return id of every window')
    return {piece.strip() for piece in raw.split(",") if piece.strip()}


def _remembered() -> dict[str, str]:
    return {str(k): str(v) for k, v in _read_json(WINDOW_CACHE).items()}


def _remember(directory: str, window_id: str) -> None:
    data = _remembered()
    data[directory] = window_id
    try:
        path = Path(os.path.expanduser(WINDOW_CACHE))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def raise_window(window_id: str) -> bool:
    """Bring one Chrome window to the front by its id. True if it worked."""
    if not re.fullmatch(r"\d{1,20}", window_id or ""):
        return False
    out = _osascript(
        f'''tell application "Google Chrome"
          set index of (first window whose id is {window_id}) to 1
          activate
          return "yes"
        end tell'''
    )
    return out == "yes"


_URL_IN_BYTES = re.compile(rb"https?://[\x20-\x7e]{4,300}")
# How far ahead of the runner-up a match must be to be trusted. Measured on
# three real windows: each scored 1.00 against its own profile and at most 0.43
# against the others, so the gap is wide and this is conservative.
MIN_SCORE = 0.5
MIN_GAP = 0.2


def _hosts(urls) -> set[str]:
    out = set()
    for url in urls:
        parts = str(url).split("/")
        if len(parts) > 2 and "." in parts[2]:
            out.add(parts[2].lower())
    return out


def _session_hosts(directory: str, recent: int = 3) -> set[str]:
    """Hosts Chrome has recorded for one profile's open tabs.

    Chrome writes each profile's session to its own directory, so the session
    files are the one place on disk that ties a set of pages to a profile.
    """
    import glob

    folder = os.path.join(os.path.expanduser(LOCAL_STATE).rsplit("/", 1)[0],
                          directory, "Sessions")
    try:
        files = sorted(glob.glob(os.path.join(folder, "*")), key=os.path.getmtime)
    except OSError:
        return set()
    found: set[str] = set()
    for path in files[-recent:]:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        found |= _hosts(m.decode("ascii", "ignore") for m in _URL_IN_BYTES.findall(raw))
    return found


def _live_windows() -> dict[str, set[str]]:
    """Window id -> the hosts currently open in it."""
    raw = _osascript(
        '''tell application "Google Chrome"
          set out to ""
          repeat with w in windows
            set out to out & (id of w) & ">>"
            repeat with t in tabs of w
              set out to out & (URL of t) & " "
            end repeat
            set out to out & linefeed
          end repeat
          return out
        end tell''',
        timeout=60,
    )
    found: dict[str, set[str]] = {}
    for line in raw.splitlines():
        if ">>" not in line:
            continue
        wid, _, urls = line.partition(">>")
        hosts = _hosts(urls.split())
        if wid.strip() and hosts:
            found[wid.strip()] = hosts
    return found


def identify(directory: str) -> str:
    """Find an already-open window belonging to a profile Jarvis did not launch.

    Without this, "bring office chrome front" opened a fourth window while the
    office window sat there already -- the cache only knows what Jarvis started
    itself, and three windows were opened by hand long before it ran.

    Every Chrome window shares one process and shows only the page title, so the
    profile has to be inferred: match each window's open hosts against what
    Chrome recorded in that profile's own session files. Measured on three real
    windows, each scored 1.00 against its own profile and at most 0.43 against
    the others.
    """
    wanted = _session_hosts(directory)
    if not wanted:
        return ""
    others = {d: _session_hosts(d) for d in profiles() if d != directory}

    best, best_score, runner_up = "", 0.0, 0.0
    for wid, hosts in _live_windows().items():
        score = len(hosts & wanted) / len(hosts)
        rival = max((len(hosts & h) / len(hosts) for h in others.values()), default=0.0)
        # A window whose pages fit another profile better is not this one.
        if score < MIN_SCORE or score - rival < MIN_GAP:
            continue
        if score > best_score:
            best, best_score, runner_up = wid, score, rival
    return best


def open_profile(phrase: str) -> tuple[bool, str]:
    """Raise the named profile's window, or open it if there is none."""
    directory, label, email = resolve_profile(phrase)
    if not directory:
        known = ", ".join(sorted(labels())) or "none configured"
        return False, f"I don't know which Chrome profile that is. I know: {known}."

    spoken = label or email

    remembered = _remembered().get(directory, "")
    if remembered and remembered in window_ids() and raise_window(remembered):
        return True, f"{spoken} Chrome is at the front."

    # Nothing cached, but the window may well be open already -- opened by hand
    # before Jarvis ever ran.
    existing = identify(directory)
    if existing and raise_window(existing):
        _remember(directory, existing)
        return True, f"{spoken} Chrome is at the front."

    # No window we know of, so launch one and record which id appeared. Without
    # the diff a second "open office chrome" would open a second window rather
    # than raising the first.
    before = window_ids()
    try:
        subprocess.run(
            ["open", "-na", "Google Chrome", "--args", f"--profile-directory={directory}"],
            capture_output=True, text=True, timeout=25,
        )
    except Exception as exc:
        return False, f"Couldn't open {spoken} Chrome: {exc}"

    fresh = _new_window(before)
    if fresh:
        _remember(directory, fresh)
    return True, f"Opened {spoken} Chrome."


def _new_window(before: set[str], attempts: int = 12, pause: float = 0.5) -> str:
    """The window id that appeared after a launch, waiting for Chrome to draw."""
    import time

    for _ in range(attempts):
        time.sleep(pause)
        added = window_ids() - before
        if added:
            return sorted(added)[-1]
    return ""


# --- opening a page in a profile ------------------------------------------

URLS_CONFIG = "~/.jarvis/urls.yaml"
STORE_HOST = "admin.shopify.com"


def named_urls() -> dict[str, str]:
    """Label -> URL, from ~/.jarvis/urls.yaml. Absent is fine."""
    path = Path(os.path.expanduser(URLS_CONFIG))
    if not path.exists():
        return {}
    return _parse_yaml(path.read_text(encoding="utf-8", errors="replace"))


def store_slugs(directory: str = "Default", limit: int = 400) -> list[str]:
    """Shopify store slugs this profile has visited, most recent first.

    Read from Chrome's history rather than guessed, because store slugs carry a
    random suffix -- "mypostautomation-gs01o4wy" is not something anyone says
    aloud, and it cannot be constructed from the store's name.

    The database is locked while Chrome runs, so it is copied first.
    """
    import shutil
    import sqlite3
    import tempfile

    source = os.path.join(
        os.path.expanduser(LOCAL_STATE).rsplit("/", 1)[0], directory, "History"
    )
    if not os.path.exists(source):
        return []
    copy = os.path.join(tempfile.gettempdir(), f"jarvis-history-{abs(hash(directory))}.db")
    try:
        shutil.copy2(source, copy)
        conn = sqlite3.connect(copy)
        rows = conn.execute(
            "SELECT url FROM urls WHERE url LIKE ? ORDER BY last_visit_time DESC LIMIT ?",
            (f"%{STORE_HOST}/store/%", limit * 8),
        ).fetchall()
        conn.close()
    except Exception:
        return []
    finally:
        try:
            os.unlink(copy)
        except OSError:
            pass

    seen: list[str] = []
    for (url,) in rows:
        after = str(url).partition("/store/")[2]
        slug = after.split("/")[0].split("?")[0].strip()
        if slug and slug not in seen:
            seen.append(slug)
    return seen[:limit]


def resolve_store(phrase: str, directory: str = "Default") -> list[str]:
    """Store slugs matching what was said, best first.

    Returns every match rather than one, so an ambiguous "ajex" -- which finds
    both ajexautomation and ajexautomation2 -- can be reported instead of
    silently picking.
    """
    wanted = re.sub(r"\b(?:the|open|store|shop|admin|shopify)\b", " ", (phrase or "").lower())
    wanted = re.sub(r"[^a-z0-9]+", "", wanted)
    if not wanted:
        return []
    slugs = store_slugs(directory)
    exact = [s for s in slugs if re.sub(r"[^a-z0-9]+", "", s.lower()) == wanted]
    if exact:
        return exact
    return [s for s in slugs if wanted in re.sub(r"[^a-z0-9]+", "", s.lower())]


def open_url(url: str, directory: str) -> bool:
    """Open a URL in a specific profile."""
    if not re.match(r"^https?://", url or ""):
        return False
    try:
        subprocess.run(
            ["open", "-na", "Google Chrome", "--args",
             f"--profile-directory={directory}", url],
            capture_output=True, text=True, timeout=25,
        )
    except Exception:
        return False
    return True


def open_page(phrase: str, profile: str = "") -> tuple[bool, str]:
    """Open a named page or a Shopify store, in the profile that owns it.

    Defaults to the office profile: it is the account signed in to the stores,
    so anything work-shaped belongs there and saying so every time is noise.
    """
    directory, label, _ = resolve_profile(profile) if profile else ("", "", "")
    if not directory:
        directory, label, _ = resolve_profile("office")
    if not directory:
        directory, label = "Default", "office"

    said = (phrase or "").strip()
    if not said:
        return False, "Which page should I open?"

    known = named_urls()
    for name in sorted(known, key=len, reverse=True):
        if name in said.lower():
            if open_url(known[name], directory):
                return True, f"Opening {name} in {label} Chrome."
            return False, f"Couldn't open {name}."

    if re.match(r"^https?://", said):
        return (True, f"Opening that page in {label} Chrome.") if open_url(said, directory) \
            else (False, "Couldn't open that page.")

    matches = resolve_store(said, directory)
    if not matches:
        return False, (f"I couldn't find a store matching {said}. "
                       "I look in Chrome's history, so open it once by hand first.")
    slug = matches[0]
    if not open_url(f"https://{STORE_HOST}/store/{slug}", directory):
        return False, f"Couldn't open the {slug} store."
    if len(matches) > 1:
        return True, (f"Opening {slug} in {label} Chrome. "
                      f"There are {len(matches)} that match, say the full name for a different one.")
    return True, f"Opening the {slug} store in {label} Chrome."

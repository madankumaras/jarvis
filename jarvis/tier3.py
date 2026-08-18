"""Detached `claude -p` execution.

Two rules from the spec, both load-bearing:

  * A long run must never block the microphone. "Create a GLS carrier store"
    takes minutes, and those are exactly the minutes you might want to ask
    something else.
  * Its output is never read aloud verbatim. The caller gets the raw text and
    decides what to say.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import traceback
from typing import Callable

TIMEOUT_SECONDS = 600  # 10 minutes, per the spec

# Seen live: the CLI's OAuth session expired and every job came back with
# "Failed to authenticate: OAuth session expired and could not be refreshed".
# That text was then summarised and spoken as though it were an answer.
_AUTH_FAILED = re.compile(
    r"oauth session expired|failed to authenticate|not logged in|please (?:run )?/?login",
    re.I,
)


def looks_like_auth_failure(output: str) -> bool:
    return bool(_AUTH_FAILED.search(output or ""))

# `claude -p` is non-interactive: it cannot show a permission prompt, so
# anything needing approval simply stalls and reports back asking for it.
# Observed in a real session: "This command needs your approval to run (it
# makes a network call to the Trello API)."
#
# The default here is a READ-ONLY allowlist. That makes questions work while
# refusing to let a misheard sentence modify a repo unattended.
#
# Set JARVIS_TIER3_ALLOW to widen it, e.g.
#     JARVIS_TIER3_ALLOW='Read,Grep,Glob,Bash,Edit,Write'
# or JARVIS_TIER3_ALLOW=all to bypass permission checks entirely. `all` lets a
# voice command do anything in the target repo — including deleting things —
# so it is opt-in and never the default.
DEFAULT_ALLOWED_TOOLS = "Read,Grep,Glob,WebFetch"


def _permission_args() -> list[str]:
    allow = os.environ.get("JARVIS_TIER3_ALLOW", DEFAULT_ALLOWED_TOOLS).strip()
    if allow.lower() == "all":
        return ["--dangerously-skip-permissions"]
    return ["--allowedTools", allow]


def build_prompt(request: str, context: dict | None = None) -> str:
    """Give the agentic run the context Jarvis already has.

    Without this it received only the bare utterance, so "give me the AC" or
    "go through that card" arrived with nothing to act on and came back asking
    which card was meant -- while Jarvis knew perfectly well.
    """
    ctx = context or {}
    lines: list[str] = []

    if ctx.get("repo"):
        lines.append(f"You are in {ctx['repo']}. Use this repo's skills and docs.")
    if ctx.get("release"):
        lines.append(f"Active release: {ctx['release']}.")
    if ctx.get("card"):
        lines.append(f"The card under discussion is {ctx['card']} — assume this one unless told otherwise.")
    if ctx.get("my_cards"):
        lines.append("Cards assigned to me: " + ", ".join(ctx["my_cards"]) + ".")
    if ctx.get("person"):
        lines.append(f"Person last mentioned: {ctx['person']}.")

    if not lines:
        return request

    lines.append("")
    lines.append(
        "Answer for a voice assistant to read aloud: lead with the answer, keep "
        "it to a few sentences, no markdown. If you genuinely cannot resolve "
        "something, say what is missing in one line rather than asking a "
        "question — nobody is at the keyboard to answer it."
    )
    lines.append("")
    lines.append(f"Request: {request}")
    return "\n".join(lines)


class Tier3Runner:
    """Runs one job at a time, on a background thread."""

    def __init__(self, domain_path: str, command: list[str] | None = None) -> None:
        self.domain_path = domain_path
        self.command = command
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        text: str,
        on_done: Callable[[str], None],
        context: dict | None = None,
    ) -> bool:
        """Begin a job. Returns False if one is already running.

        Never raises: a failed spawn is reported through on_done like any
        other outcome, because the caller has no other channel to the user.
        """
        if self.busy:
            return False

        prompt = build_prompt(text, context)
        argv = self.command or ["claude", *_permission_args(), "-p", prompt]

        def run() -> None:
            try:
                proc = subprocess.run(
                    argv,
                    cwd=self.domain_path,
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_SECONDS,
                )
                out = f"{proc.stdout or ''}{proc.stderr or ''}"
            except subprocess.TimeoutExpired:
                out = f"timed out after {TIMEOUT_SECONDS}s"
            except Exception as exc:
                out = f"{type(exc).__name__}: {exc}"

            try:
                on_done(out.strip())
            except Exception:
                # The callback speaks and touches the network. If it throws,
                # the job is still over — do not leave `busy` stuck on.
                traceback.print_exc()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True

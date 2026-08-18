"""The always-on process. Wires ears, router, workers, and voice together."""
from __future__ import annotations

import re
import time

import numpy as np

from jarvis.dash.bus import BUS
from jarvis.dash.server import Dashboard
from jarvis.ears.stt import Transcriber
from jarvis.ears.wake import SETTLE_SECONDS, Capture, WakeListener
from jarvis.router.confirm import Confirmation
from jarvis.router.conversation import Conversation
from jarvis.memory.store import Store
from jarvis.router.core import handle_transcript
from jarvis.tier3 import Tier3Runner
from jarvis.watch.jobs import (
    due_reminders_job,
    slack_replies_job,
    trello_movement_job,
    zendesk_job,
)
from jarvis.watch.scheduler import Scheduler
from jarvis.types import Response, RpcError, Vocab
from jarvis.voice.greeting import greeting
from jarvis.voice.speak import say, speak
from jarvis.workers.manager import WorkerManager

CAPTURE_SECONDS = 6.0
# A yes/no reply is short; a long window just adds silence to transcribe.
CONFIRM_SECONDS = 4.0
# After an answer, keep listening this long for a follow-up before closing the
# conversation. One wake word should buy a conversation, not a single command.
FOLLOWUP_SECONDS = 8.0
# A guard against a feedback loop, not an expected limit.
MAX_TURNS = 12

# A domain name alone is not a switch request: "status of the fedex toggle"
# mentions a domain but should stay put. Only these phrasings move the
# sticky context.
SWITCH_PHRASES = ("switch to", "use ", "jarvis,")


_SUBJECT = re.compile(r"\b((?!MCSL\b)[A-Z]{2,6}-\d{1,5}|MCSL[\s-]?\d{2,4})\b", re.I)


def _acknowledge(question: str) -> str:
    """Say what is being fetched, not that a tool is running.

    Naming the subject back is what makes it feel like an assistant rather than
    a progress bar: "give me a minute, I'll get the details on ZI-687" tells you
    it heard you correctly, before the answer arrives.
    """
    found = _SUBJECT.search(question or "")
    if found:
        return f"Ok boss, give me a minute — I'll get the details on {found.group(1).upper()}."
    words = " ".join((question or "").split())
    if words:
        return f"Ok boss, give me a minute — looking into {words[:60]}."
    return "Ok boss, give me a minute."


class Jarvis:
    def __init__(self, domain: str = "mcsl") -> None:
        # Startup takes ~10s: worker spawn, a Trello vocabulary fetch, and the
        # wake-word model load. Without progress output it looks hung, and
        # anything clapped before "listening" appears is simply not heard yet.
        print(f"starting {domain} worker...", flush=True)
        self.manager = WorkerManager()
        self.domain = domain
        self.worker = self.manager.get(domain)
        print("loading vocabulary from Trello...", flush=True)
        self.vocab = self._load_vocab()
        print(f"  {len(self.vocab.cards)} ids, {len(self.vocab.people)} people", flush=True)
        self.transcriber = Transcriber()
        self.tier3 = Tier3Runner(self.manager._config(domain)["path"])
        self.store = Store()
        self.busy = False          # true while a turn is being handled
        self.conversation = Conversation()
        self.listener = WakeListener(self._on_wake)
        self.dash = Dashboard()
        self.listener.on_level = self._publish_level
        self.scheduler = self._build_scheduler()

    # ---- dashboard -----------------------------------------------------

    def _publish_level(self, peak: float, floor: float, needs: float) -> None:
        """Called for every audio chunk. Normalised so the ring does not need
        to know this mic peaks well above 1.0."""
        BUS.publish("level", peak=peak, floor=floor, needs=needs,
                    norm=min(1.0, peak / max(needs * 3, 0.25)))

    def _publish_state(self, state: str, line: str = "") -> None:
        BUS.publish("state", state=state, line=line)

    def _publish_context(self) -> None:
        """Refresh the right-hand rail. Best effort: a dashboard that cannot be
        populated is not a reason to interrupt a turn."""
        try:
            # The ACTIVE release, not the newest. A fresh intake list can exist
            # with nothing started while the real work sits one release back:
            # MCSL 386 had 8 untouched cards while 385 had 16 mid-test.
            active = self.worker.call("active_release")
            work = self.worker.call("my_work")
            items = work.get("items", [])
            todo = [i for i in items if i.get("actionable")]

            BUS.publish(
                "context",
                release=active.get("release", ""),
                progress=f"{active.get('verified', 0)} / {active.get('testable', 0)} verified",
                outstanding=active.get("outstanding", 0),
                skipped=active.get("skipped", 0),
                duplicates=active.get("duplicates", 0),
                mine=f"{len(todo)} to test · {len(items)} total",
                issues=len(self.vocab.zi_ids),
                reminders=len(self.store.due_tasks()),
                tickets=[
                    {
                        "id": i["id"],
                        "title": i.get("title", ""),
                        "state": i.get("state", ""),
                        "note": i.get("note", ""),
                        "actionable": bool(i.get("actionable")),
                    }
                    for i in items[:12]
                ],
            )
        except Exception:
            pass

    def _build_scheduler(self) -> Scheduler:
        s = Scheduler()
        s.add("reminders", 60, lambda: due_reminders_job(self.store))
        s.add("zendesk", 900, lambda: zendesk_job(self.worker, self.store))
        s.add("trello", 600, lambda: trello_movement_job(self.worker, self.store, self.domain))
        s.add("replies", 300, lambda: slack_replies_job(self.worker, self.store))
        return s

    def _say(self, response: Response) -> None:
        """Speak with the mic muted.

        Everything Jarvis says reaches its own microphone. Turn replies were
        already covered by the post-turn settle, but tier-3 completions and
        watcher announcements fire on background threads and were not — a long
        sentence from either would wake Jarvis up by itself.
        """
        listener = getattr(self, "listener", None)
        if listener is None:
            speak(response)
            return
        listener.muted.set()
        BUS.publish("state", state="speaking", line="")
        try:
            speak(response)
        finally:
            time.sleep(SETTLE_SECONDS)   # speakers lag `say` returning
            listener.reset()
            listener.muted.clear()
            # Without this the dial sticks on "speaking" forever: a watcher
            # announcement outside a turn has nothing else to reset it.
            if not self.busy:
                BUS.publish("state", state="idle",
                            line="listening for \u201chey jarvis\u201d")

    def _announce(self, lines: list[str]) -> None:
        """Speak watcher output — unless a turn is in progress, in which case
        hold it. Interrupting the user mid-sentence would be worse than a
        slightly late notification."""
        if self.busy:
            self.scheduler.pending.extend(lines)
            return
        for line in lines:
            print(f"\n  [watcher] {line}", flush=True)
            level = "alert" if line.lower().startswith("reminder") else "info"
            BUS.publish("card", level=level, title="watcher", message=line)
            self._say(Response(speech=line, detail=line))

    def _load_vocab(self) -> Vocab:
        raw = self.worker.call("vocab")
        return Vocab(**{k: raw.get(k, []) for k in ("cards", "people", "carriers", "zi_ids")})

    def _switch_domain(self, target: str) -> None:
        self.domain = target
        self.worker = self.manager.get(target)
        self.vocab = self._load_vocab()

    def handle_utterance(
        self, audio: np.ndarray, capture=None, announce_silence: bool = True
    ) -> Response:
        self._publish_state("thinking", "working on it")
        text = self.transcriber.transcribe(audio, self.vocab)
        print(f"  heard: {text!r}", flush=True)

        if not text.strip():
            quiet = Response(speech="Didn't catch that.", ok=False)
            if announce_silence:
                self._say(quiet)
            return quiet

        BUS.publish("turn", who="you", text=text)

        target = self.manager.resolve_alias(text) if text else None
        if target and target != self.domain and any(p in text.lower() for p in SWITCH_PHRASES):
            self._switch_domain(target)
            response = Response(speech=f"Switched to {target}.")
            self._say(response)
            return response

        response = handle_transcript(
            text, self.vocab, self.worker, store=self.store, domain=self.domain,
            conversation=self.conversation,
        )
        print(f"  reply [tier {response.tier} ok={response.ok}]: {response.speech}", flush=True)
        if response.speech:
            BUS.publish("turn", who="jarvis", text=response.speech)

        # Every command is logged, including the ones that fell through. After
        # a week, store.tier3_counts() says which intent to add next.
        try:
            self.store.log_command(
                raw=text, corrected=text, intent="", tier=response.tier, ok=response.ok
            )
        except Exception:  # logging must never break a turn
            pass

        if response.needs_confirm and response.pending is not None:
            return self._run_confirmation(response, capture)

        # "Did you mean MCSL-385?" — the correction layer asked a question but
        # has no action attached. Without this the question is spoken and
        # forgotten, which is a dead end for the user.
        if response.needs_confirm and response.pending is None:
            return self._run_clarification(response, capture)

        if response.tier == 3:
            return self._run_tier3(response)

        self._say(response)
        return response

    def _run_confirmation(self, response: Response, capture) -> Response:
        """Read the action back, listen once, execute only on a clear yes."""
        self._say(response)
        reply = ""
        if capture is not None:
            capture.drain()
            reply = self.transcriber.transcribe(capture.record(CONFIRM_SECONDS), self.vocab)
        print(f"  confirm reply: {reply!r}", flush=True)

        if not Confirmation(response.pending).resolve(reply):
            out = Response(speech="Cancelled, boss.", ok=False)
            self._say(out)
            return out

        try:
            payload = self.worker.call(response.pending.method, **response.pending.params)
        except RpcError as exc:
            out = Response(speech=str(exc), detail=str(exc), ok=False)
            self._say(out)
            return out

        # Record who was DM'd so the reply watcher knows whose answer to wait
        # for. Without this it has no conversations to poll.
        if response.pending.method == "send_dm":
            name = (response.detail or "").split(" (")[0].strip()
            if name:
                try:
                    self.store.mark_seen("dm_sent", name)
                except Exception:
                    pass

        out = Response(speech=payload.get("speech", "Done."), detail=payload.get("detail", ""))
        self._say(out)
        return out

    def _run_clarification(self, response: Response, capture) -> Response:
        """Ask, listen once, and re-run the corrected sentence on a yes."""
        self._say(response)
        if capture is None:
            return response

        capture.drain()
        reply = self.transcriber.transcribe(capture.record(CONFIRM_SECONDS), self.vocab)
        print(f"  clarify reply: {reply!r}", flush=True)

        from jarvis.router.confirm import interpret

        if interpret(reply) != "yes":
            out = Response(speech="Never mind then.", ok=False)
            self._say(out)
            return out

        # response.detail holds what was misheard; the guess is in the question.
        guess = response.speech.replace("Did you mean", "").strip(" ?.")
        return self.handle_utterance_text(f"status of {guess}", capture)

    def handle_utterance_text(self, text: str, capture=None) -> Response:
        """Route already-transcribed text. Used when re-running a corrected
        sentence, so the audio path is not repeated."""
        response = handle_transcript(
            text, self.vocab, self.worker, store=self.store, domain=self.domain,
            conversation=self.conversation,
        )
        print(f"  reply [tier {response.tier} ok={response.ok}]: {response.speech}", flush=True)
        if response.speech:
            BUS.publish("turn", who="jarvis", text=response.speech)
        if response.needs_confirm and response.pending is not None:
            return self._run_confirmation(response, capture)
        if response.tier == 3:
            return self._run_tier3(response)
        self._say(response)
        return response

    def _run_tier3(self, response: Response) -> Response:
        """Hand the work off and return immediately.

        Never names the mechanism aloud -- "Claude Code" is plumbing, not
        something an assistant says. The result is summarised and spoken when
        it lands, because a result nobody hears is not a result.
        """
        question = response.detail or response.speech

        def done(output: str) -> None:
            head = " ".join(output.split())[:200] or "no output"
            print(f"\n  [job finished] {head}", flush=True)
            BUS.publish("job", status="finished", what=question)

            # An expired session is not an answer. Summarising it produced a
            # spoken "your session has expired, what do you want to do?", which
            # the mic then heard and turned into another request.
            from jarvis.tier3 import looks_like_auth_failure

            if looks_like_auth_failure(output):
                self._say(Response(
                    speech="I can't run that — your Claude sign-in has expired. "
                           "Run claude login in a terminal and ask me again.",
                    detail=output[:600], tier=3, ok=False,
                ))
                return

            spoken = self._summarise(output, question)
            self._say(Response(speech=spoken, detail=output[:600], tier=3))

        if not self.tier3.start(question, done):
            busy = Response(speech="Still working on the last one, boss.", ok=False)
            self._say(busy)
            return busy

        BUS.publish("job", status="running", what=question)
        working = Response(speech=_acknowledge(question), detail=question, tier=3)
        self._say(working)
        return working

    def _summarise(self, output: str, question: str = "") -> str:
        """Condense job output into something worth hearing aloud.

        The API key lives in the target repo's .env, which the worker loads and
        jarvis/ deliberately does not -- so the summary is a worker call.
        """
        try:
            out = self.worker.call("summarise", text=output, question=question)
            said = (out or {}).get("speech", "").strip()
            if said:
                return f"{said} What do you want to do?"
        except Exception:
            pass
        return "That job finished, but I could not summarise it. Check the notification."

    def _on_wake(self, capture: Capture, source: str = "") -> None:
        """One wake, then a conversation until it goes quiet.

        Runs on the main thread, from the one live audio stream.
        """
        print(f"\n*** woken ({source or 'unknown'}) ***", flush=True)
        self.busy = True
        self.conversation = Conversation()
        self.dash.open_once()
        try:
            self._publish_state("speaking", "greeting you")
            self._say(Response(speech=greeting()))
            window = CAPTURE_SECONDS

            for turn in range(MAX_TURNS):
                print(f"  listening ({window:.0f}s)...", flush=True)
                self._publish_state("listening", "go ahead, boss")
                capture.drain()

                # Silence after an answer just means the conversation is over.
                # Announcing "Didn't catch that" every time the follow-up window
                # expires is noise -- it fired four times in one session.
                announce_silence = turn == 0
                response = self.handle_utterance(
                    capture.record(window), capture, announce_silence=announce_silence
                )

                if response.ends:
                    break
                if not response.ok and "didn't catch" in response.speech.lower():
                    break
                # A question Jarvis asked deserves a full window for the answer.
                window = CAPTURE_SECONDS if response.awaiting else FOLLOWUP_SECONDS
        finally:
            self.busy = False
            self._publish_state("idle", "listening for \u201chey jarvis\u201d")

        # Anything the watchers held during the turn goes out now.
        held = self.scheduler.drain()
        if held:
            self._announce(held)

    def run(self) -> None:
        if self.dash.start():
            print(f"dashboard on {self.dash.url}", flush=True)
        else:
            print(f"dashboard port busy; skipping ({self.dash.url})", flush=True)
        BUS.publish("meta", domain=self.domain,
                    wake=__import__("jarvis.ears.wake", fromlist=["WAKE_MODE"]).WAKE_MODE,
                    voice=__import__("jarvis.voice.speak", fromlist=["VOICE"]).VOICE)
        self._publish_context()
        self._publish_state("idle", "listening for \u201chey jarvis\u201d")

        print("loading wake word model...", flush=True)
        self.scheduler.start(self._announce)
        listener = self.listener
        listener._ensure_wakeword()
        paths = []
        if listener.use_clap:
            paths.append("clap twice")
        if listener.use_wakeword:
            paths.append("say 'hey jarvis'")
        print(f"\n=== jarvis listening ({self.domain}) — {' or '.join(paths)} ===", flush=True)
        try:
            listener.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.scheduler.stop()
            self.dash.stop()
            self.manager.shutdown()


def main() -> None:
    Jarvis().run()


if __name__ == "__main__":
    main()

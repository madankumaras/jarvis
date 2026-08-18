# Jarvis

Voice front-end to the MCSL, FedEx, and AU Post Domain Expert platforms.

Say something; Jarvis answers out loud. Fast questions are answered directly from
each repo's own code in under two seconds; real work is handed to Claude Code.

## Setup

```bash
brew install portaudio
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"
```

Python 3.12 specifically — the system default is 3.14, which has no wheels for the
ML dependencies. The wake-word model is not bundled with `openwakeword`; the last
line fetches it. The `faster-whisper` weights (~460MB) download on first use.

## Run

```bash
.venv/bin/python -m jarvis.daemon
```

Wake it by saying **"hey jarvis"**, then speak.

A wake word matches a specific acoustic pattern, so it rejects almost
everything else. Clap detection only knows loudness — any two loud sounds
inside ~640ms qualify — so it is off by default. Turn it on if you want
hands-free with wet or busy hands:

```bash
JARVIS_WAKE_MODE=clap      # double-clap only
JARVIS_WAKE_MODE=both      # either
JARVIS_CLAP_MIN_PEAK=0.4   # fussier claps, if you get phantom wakes
```

### Voice

```bash
.venv/bin/python -m jarvis.voice.voices          # list, then hear a sample of each
.venv/bin/python -m jarvis.voice.voices Rishi    # hear just one
JARVIS_VOICE=Rishi JARVIS_VOICE_RATE=170 .venv/bin/python -m jarvis.daemon
```

Eight usable English voices are installed by default. `Rishi`, `Aman` and
`Tara` are Indian English and are listed first — a voice whose accent matches
the person speaking to it pronounces names and place words correctly, which
matters more than it sounds like it should. **`Tara` (en_IN) is the default.**
`Daniel` (en_GB), `Samantha` (en_US), `Karen` (en_AU), `Moira` (en_IE) and
`Tessa` (en_ZA) are also available. Rate is words per minute; 190 is the
default, 165 is calmer.

No microphone handy? The same pipeline works typed:

```bash
.venv/bin/python -m jarvis.cli "what's in MCSL 386"
```

## What it answers today

| Say | Get |
|---|---|
| "what's in MCSL 386" | the release's cards and how many |
| "status of ZI-691" | that issue's list, assignee, and last comment |
| "what are my tasks" | cards assigned to you in the current releases |
| "any customer issues" | open ZI issues from the latest Zendesk intake |
| "who built the toggle flow" | answer from the repo's knowledge base |
| "in 385 how many tickets assigned to me" | just yours in that release |
| "did Ashok reply" | his answer to a DM Jarvis sent |
| "remind me to check ZI-653 at 4" | fires at 16:00, survives a restart |
| "note that the GLS store needs re-toggling" | remembered |
| "DM Ashok saying the toggle is off" | read back, waits for "ok" |
| "who is the dev for that" | Trello members plus whether a PR is open |
| "what is the testing plan for that" | the generated test cases for that ticket |
| "open slack" / "launch vs code" | launches the app |
| "post in qa-team saying ..." | read back, waits for "ok" |
| anything else | handed off, summarised, and read back to you |

Jarvis also speaks unprompted: due reminders, new Zendesk issues, cards added
to the current release, and replies to DMs it sent. Nothing is announced
between 22:00 and 08:00 — it is held, not dropped.

`claude -p` cannot show a permission prompt, so tier-3 work runs with an
explicit allowlist, read-only by default:

```bash
JARVIS_TIER3_ALLOW='Read,Grep,Glob,Bash,Edit,Write'   # widen it
JARVIS_TIER3_ALLOW=all                                # bypass all checks
```

`all` lets a misheard sentence modify anything in the target repo, unattended.
That is why it is opt-in.

Troubleshooting: `JARVIS_WAKE_DEBUG=1` prints the peak, noise floor and
threshold for every loud chunk; `JARVIS_STT_DEBUG=1` prints the confidence of
every rejected transcript.

Anything that leaves your machine — a Slack DM, a store creation — is read back
to you and waits for an explicit "ok". Anything that is not a bare affirmative
counts as no: "ok so actually make it the GLS store" is a correction, not
consent. Reminders and notes are local and need no confirmation.

## Domains

One sticky domain at a time. Say "switch to fedex" to move; a bare mention of a
domain name does not switch.

All three repos share one Trello board, distinguished by list prefix
(`SL MCSL 386:`, `SL AuPost v1.0.39:`, `SL v2.3.123 FedexApp:`). Each domain gets
its own worker process because the three repos have colliding `config.py` and
`pipeline/` modules and cannot share a Python interpreter.

## Test

```bash
.venv/bin/pytest -m "not audio"   # fast, no downloads
.venv/bin/pytest                  # includes the whisper model and a real worker
```

The whole product is testable without a microphone: everything routes through
`handle_transcript(text, vocab, worker)`, with audio above it and speech below.

## Design

`docs/superpowers/specs/2026-08-15-jarvis-voice-assistant-design.md` — the design.
`docs/superpowers/plans/2026-08-15-jarvis-slice-1.md` — this slice, task by task.

All four slices are built: voice in and out, write actions with spoken
confirmation, persistent tasks and reminders, and the proactive watcher loop.

Not yet done: FedEx and AU Post as additional domains (Phase B), barge-in while
Jarvis is speaking, and silence detection instead of the fixed 6-second capture
window.

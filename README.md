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

### Dashboard

A live HUD opens in your browser the first time Jarvis wakes, at
http://127.0.0.1:8777/ — then stays where you put it and simply updates. Every
animated element is a real signal, not decoration:

| On screen | Source |
|---|---|
| amplitude ring | the mic peak the wake detector already computes |
| ring colour | idle / listening / working / speaking |
| transcript, ids boxed | what was heard, after correction |
| job timer | the background job's elapsed time |
| cards sliding in | watcher announcements |

Server-Sent Events over stdlib HTTP, bound to localhost only — the transcript of
everything you say has no business being reachable from the network. If the
port is taken the dashboard is skipped and Jarvis keeps listening.

```bash
JARVIS_DASH_PORT=9001   # if 8777 clashes
```

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
| "what cards assigned to me" | your cards **with QA state** — verified ones are not offered as work |
| "what should I test" | only what actually needs testing, duplicates flagged |
| "is 385 done" | verified / testable, excluding support-closed cards |
| "which release are we working on" | the release with outstanding QA work, not the newest |
| "who is the dev for that" | Trello members plus whether a PR is open |
| "what is the testing plan for that" | the generated test cases for that ticket |
| "open slack" / "launch vs code" | launches the app |
| "bring chrome to the front" | raises it, from any Space |
| "bring the 383 window front" | raises **that window**, not just the app |
| "minimise this" | sends the front window to the Dock |
| "post in qa-team saying ..." | read back, waits for "ok" |
| "open the ajex store" | opens it in the office profile — or focuses the tab already showing it |
| "see this request, is it correct" | reads the window in front and **judges** it |
| "go through this doc and ..." | reads the actual file or page, not a picture of it |
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

### Apps and windows

`open X` launches, `bring X front` / `switch to X` / `go to X` raises. One
exception: "bring **up** X" is launching, the only spoken form where "bring"
does not mean move-to-front.

Naming a **window** works too — "bring the 383 window front" picks that Chrome
window out of three, rather than raising Chrome and leaving whichever tab
happened to be in front. Windows are found by title across every app and every
Space, including Electron apps that have no AppleScript window dictionary at all
(Slack and VS Code both answer "every window doesn't understand the count
message"). For those, only the app can be raised, and Jarvis says so instead of
claiming otherwise.

Two things worth knowing:

**"On screen" means the current Space, not merely unoccluded.** With Chrome one
desktop over, the on-screen window list held exactly one window while the full
list held 40 across 14 apps — so finding a window has to search all of them.
The whole point of "bring Slack front" is that Slack is not in front.

**No Accessibility permission is needed** for any of this. Minimising uses each
app's own scripting dictionary, so it works for browsers, Terminal, Finder and
Preview but not for Electron apps.

Every app in `/Applications`, `~/Applications`, `/System/Applications`, its
`Utilities`, and `CoreServices/Applications` — plus one level of nesting, so
Setapp and Microsoft Office folders work. Two apps needed finding by hand:
**Finder** is in `/System/Library/CoreServices`, whose other 117 entries are
internal agents like `AirPlayUIAgent` — putting those in the pool would let one
mishear launch a system daemon — and **Keychain Access** has moved out of
Utilities on current macOS, so it resolved to nothing.

Anything still unfound falls through to Spotlight, which knows every app bundle
on the disk in under 100ms. It is the fallback rather than the primary source
on purpose: 321 bundles against 90, and most of the difference is helpers, so
searching it first would make a mishear far likelier to land somewhere odd. An
app that genuinely is not installed is refused rather than guessed at — opening
the wrong app is more confusing than admitting no match.

### Chrome profiles

`open office chrome` / `bring the office browser front` / `switch to my personal
chrome`. Which profile each name means lives in
`~/.jarvis/chrome_profiles.yaml`, seeded from Chrome on first use and yours to
edit — **never in this repository**, because those are real addresses:

```yaml
office: you@company.com
personal: you@gmail.com
personal 2: other@gmail.com
```

The office profile is guessed as the one whose mail domain nobody can sign up
for, which is the label that matters — it is the account signed in to the
Shopify stores, so the wrong profile is a wasted trip. Personal accounts are
numbered in Chrome's own order, which is arbitrary: it labelled the two the
opposite way round from how they were actually referred to. That is what the
config file is for.

Three measured facts shape this:

**Profiles are identifiable only by email.** All three profiles carried the
display name "Madan", so `info_cache[dir]["name"]` is useless.

**A profile's windows cannot be identified afterwards.** Every Chrome window
shares one process — pid 2306 for all seven here — and carries the page title
with no profile marker. Nothing on screen says which window is the office one.

**`open -na --profile-directory` accumulates.** Called for a profile that
already had a window, it opened a fifth rather than focusing the fourth, so
repeating the command would pile up windows.

And `-n` asks macOS for a **new instance** — used repeatedly against a running
Chrome it restarted the browser, taking three windows of open tabs with it.

So everything goes through the running instance wherever it can. Launching a
profile diffs Chrome's own window ids across the launch and remembers which id
belongs to which profile, so the next call raises that id directly. Opening a
page first looks for a tab already showing it and focuses that; failing that,
the page is added as a tab to the profile's own window. `open -na` is left for
the one case that needs it — a profile with no window at all.

Verified: 3 windows, "bring office chrome front" raised the existing 31-tab
office window, "open the moody store" added one tab to *that* window, and
saying it again focused the tab with no change at all. The tab needle drops the
query string, because Shopify appends an `appLoadId` to store URLs and matching
the whole thing would never hit an open tab.

### Looking at the screen

"See this request, is it correct?" photographs **only the frontmost window** and
answers. Never the whole screen: the question is about one window, and capturing
the desktop would also send whatever Slack thread or terminal is behind it. The
screenshot is deleted as soon as the question is answered.

Needs Screen Recording permission (System Settings → Privacy & Security).
Without it macOS quietly returns a picture of the wallpaper rather than failing.
Accessibility is **not** needed — window geometry comes from CoreGraphics.

Two things about this were measured rather than assumed, and both were wrong at
first guess:

**Asking is not judging.** On a rate request carrying `totalPackageCount: 2`
beside a single package line item, the plain prompt described the screen
accurately and called it correct **three times out of three**. A prompt that
says to cross-check counts against entries present named the wrong field **three
out of three**. Same model, same screenshot. So a question containing "correct",
"wrong", "check", "verify" and the like switches to judging; "what's on my
screen" still gets a description.

**Screens get Sonnet, documents get Haiku.** On that same screenshot Haiku
missed the mismatch and answered that the dimensions "seem quite large" —
plausible, confident, wrong. Reading small text off pixels is where the cheap
model fails. Document text arrives exact rather than inferred, so there is
nothing to misread and Haiku is fine.

```bash
JARVIS_VISION_MODEL=claude-opus-5   # screens
JARVIS_DOC_MODEL=claude-sonnet-4-6  # documents
```

### Reading a document

"Go through this doc" asks the frontmost app what it has open and reads the real
file or URL — whole, exact, and scrollable, unlike a photograph of one visible
page. PDFs go through `pypdf`, Word and RTF through `textutil`, web pages
through tag-stripping. An app that will not name its file falls back to looking
at the screen, and says so.

A document noun is required, because `go through that card` and `go through the
doc` are otherwise the same sentence — the first is a Trello lookup.

Non-text files are identified by **magic bytes**, not extension or character
statistics. That last one was a failed attempt: this PDF's first 4000 bytes are
its XMP metadata header — 47% letters, 22% spaces, no control characters, no
replacement characters, statistically identical to prose. Its first four bytes
are `%PDF`.

### When a job hits a problem

`claude -p` is one-shot: by the time you hear about a problem the run has
already exited, so its question cannot be answered in place. Jarvis classifies
how a job ended and responds differently to each:

| Ending | What you hear |
|---|---|
| an answer | the summary, then "what do you want to do?" |
| **blocked on a permission** | "that job is blocked on a permission I don't have. It needs `<tool>`. Widen JARVIS_TIER3_ALLOW." |
| **it asked you something** | the question — and your next sentence **re-runs the job carrying your answer** |
| **sign-in expired** | "run claude login in a terminal and ask me again" |

Only the first is treated as a result. Reading the others out as though they
were answers is how Jarvis once spoke "what do you want to do?", heard itself,
and started another job.

Troubleshooting:

```bash
JARVIS_WAKE_DEBUG=1     # peak, noise floor and threshold for every loud chunk
JARVIS_STT_DEBUG=1      # confidence of every rejected transcript
JARVIS_SPEECH_DEBUG=1   # the exact string handed to `say`
```

That last one exists because "it spoke another language" turned out to be two
separate things, both readable rather than guessable: emoji in Trello comments
(macOS `say` reads 🧪 as "test tube") and code identifiers (`bkg_ref_id` read
as "bkg ref id"). Both are now rewritten before speaking — the first removed,
the second expanded to "booking reference I D".

Capitals are the third. Spacing out every capitalised run read
`USE_SCHEDULED_PICKUP` as "U S E SCHEDULED PICKUP" and `LIST` as "L I S T".
Measured against 28 real tokens, no single rule works: carrier acronyms contain
vowels but must be spelled (`UPS`, `USPS`) while `USE` and `LIST` must not. So
domain terms are named explicitly and everything else goes by whether it has a
vowel — correct on every remaining token, and it fails safe, since an odd
pronunciation beats eight spelled-out letters. A token with a vowel is left
verbatim rather than reworded, so a service code like `PARSPL` survives intact.

Two more sources of noise are dropped outright: file extensions (`.md` read as
"dot m d") and the random tail Shopify puts on every store slug —
`mypostautomation-gs01o4wy` is spoken as "mypostautomation", since the readable
half is what identifies the store to a person.

Anything that leaves your machine — a Slack DM, a store creation — is read back
to you and waits for an explicit "ok". Anything that is not a bare affirmative
counts as no: "ok so actually make it the GLS store" is a correction, not
consent. Reminders and notes are local and need no confirmation.

## Workflows

Multi-step flows live in `workflows.yaml`. Add one there and Jarvis can run it
with no code change:

```yaml
  - name: new_build
    triggers: [new build deployed, build deployed]
    steps:
      - say: Ok boss, pulling dev and wiki then re-indexing.
        kind: agent
        request: Using the mcsl-rag-sync skill, pull dev and wiki, re-index.
      - offer: Indexing done. Shall I write the AC for {card}?
        needs: [card]
        kind: agent
        request: Using mcsl-ac-writer-reviewer, write the AC for {card}.
```

How it runs:

```
you>    hey jarvis, new build deployed
jarvis> Ok boss, pulling dev and wiki then re-indexing. Give me a few minutes.
jarvis> Indexing done. Shall I write the AC for ZI-667?
you>    yes
jarvis> ...ten scenarios. Shall I write the test cases as well?
you>    no
jarvis> Ok boss, stopping there.
```

Three rules the engine enforces:

**Every step after the first waits for a yes.** A misheard workflow name must
not commit you to six actions unattended.

**Declining ends the flow, it does not skip ahead.** Refusing "shall I write the
AC?" means stop — the test-case step depends on that output.

**A missing `{placeholder}` is asked for, not spoken literally.** `{card}` is
seeded from the conversation when you have already named one, and asked for when
you have not.

Three ship by default: `new_build`, `prep_card`, `toggle_request`.

## Labels are the workflow

The board carries 50 labels. These decide whether a card needs testing, and
Jarvis reads them rather than treating every card as work:

| Label | Meaning |
|---|---|
| `QA` without `QA_VERIFIED` | needs testing — your actual queue |
| `QA_VERIFIED` | done |
| `QA Reported` | bug raised, back with dev |
| `Dev Done` | ready for QA |
| `SL: Closed By Support` | **do not test** |
| `SL: 🔄 Duplicate` | likely a sanity check, not a full pass |
| `Spill Over` | carried from an earlier release |

Two consequences worth knowing:

**Support-closed cards are excluded from the denominator.** Counting them as
outstanding would make every release look unfinished forever — MCSL 385 has 7.

**The active release is the one with the most outstanding QA work, not the
highest number.** A fresh intake list can exist with nothing started while the
real work sits one release back: 386 had 8 untouched cards while 385 had 16
mid-test, and "newest" gave the wrong answer.

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

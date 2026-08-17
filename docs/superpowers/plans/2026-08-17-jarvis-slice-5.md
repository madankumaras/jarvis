# Jarvis Slice 5 Implementation Plan — Conversation

**Goal:** Jarvis behaves like an assistant rather than a command line. It says what it is doing in plain language, asks for what it is missing, remembers what "that card" means, speaks the result of long jobs, and stays open for a follow-up without another wake word.

## The gap this closes

Today every wake is one-shot: hear one sentence, answer, forget. Observed in a real session:

| Said | Happened | Should have |
|---|---|---|
| "card 667 in MCSL-380" | listed all 31 cards in the release | answered about ZI-667 |
| "go through card ZI-667" | fell to tier 3 | answered at tier 1 |
| (long job finished) | "That job finished." | spoken summary, then "what next?" |
| (any long job) | "Running that in Claude Code" | "Ok boss, fetching that now" |

## Global Constraints

- **The follow-up window is ~8s of silence**, then the conversation closes. During it, no wake word is needed.
- The microphone stays muted whenever Jarvis speaks — already true, must remain true across the new turns.
- **Write actions still require an explicit "ok".** Slot-filling gathers the pieces; it does not replace consent.
- Never say "Claude Code", "tier 3", "worker", or any other internal name aloud.
- `jarvis/` must not import `pipeline`, `config`, or `rag`.
- No Domain Expert repo is modified.

## Pieces, in build order

### 1. Card-in-release routing

`card_status` must win when a card is named, even alongside a release, and must accept
ordinary verbs ("go through", "open", "check") rather than only "status".
A bare number after "card"/"ticket" resolves to the release's ID prefix.

### 2. Plain-language progress, and spoken results

- On dispatch: "Ok boss, fetching that now." — never the mechanism.
- On completion: Haiku condenses the output to one or two sentences, spoken, then
  "What do you want to do?"
- Full text still goes to the notification and the log.

### 3. Conversation continuation

`jarvis/router/conversation.py` holds per-conversation state: the last card, release
and person mentioned, plus any half-filled action. The daemon loops turns until 8s
of silence or an explicit "that's all".

### 4. Slot-filling

An action with missing pieces asks for them one at a time. "send a message" →
"To whom?" → "What should I say?" → read-back → "ok". Cancel at any point abandons it.

### 5. Referring back

"that card", "it", "the same one" resolve against conversation state. If nothing has
been mentioned yet, Jarvis says so rather than guessing.

## Out of scope

Barge-in while Jarvis is speaking; multi-domain conversations; anaphora beyond the
last-mentioned entity of each type.

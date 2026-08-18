"""Tier-1 handlers. These are the only code allowed to touch pipeline.*

Two rules govern every handler here, both forced by the real board's scale
(83 lists, 2382 cards):
  1. Scope to this domain's release lists, newest first. Never walk the board.
  2. Fetch only the fields in use. TrelloClient._build_trello_card issues three
     extra API calls per card, so get_cards_in_list costs 1 + 3n requests.
     Use _raw_cards for anything that needs names or membership only.
"""
from __future__ import annotations

import re
from typing import Any

# Injected by main.py from domains.yaml before any handler runs.
RELEASE_PATTERN = ""
RELEASE_TOKEN = ""

_lists_cache: list = []


def _trello():
    from pipeline.trello_client import TrelloClient

    return TrelloClient()


def _slack():
    from pipeline.slack_client import SlackClient

    return SlackClient()


def resolve_person(name: str) -> dict[str, Any]:
    """Map a spoken name to a Slack user id.

    Never picks between two people. A DM to the wrong colleague is not
    recoverable, so ambiguity is handed back for the caller to ask about.
    """
    matches = _slack().search_users(name) or []

    def _label(m: dict) -> str:
        return m.get("real_name") or m.get("display_name") or ""

    if len(matches) == 1:
        m = matches[0]
        return {"id": m.get("id", ""), "name": _label(m), "ambiguous": []}
    if not matches:
        return {"id": "", "name": "", "ambiguous": []}
    return {
        "id": "",
        "name": "",
        "ambiguous": [{"id": m.get("id", ""), "name": _label(m)} for m in matches[:5]],
    }


def read_replies(person: str = "", limit: int = 10) -> dict[str, Any]:
    """Read recent human messages in the DM channel with `person`.

    Bot messages are skipped — those are ours. Returns newest first, which is
    what "did they reply?" actually means.
    """
    import os

    import requests

    found = resolve_person(person)
    if found.get("ambiguous"):
        names = ", ".join(p["name"] for p in found["ambiguous"])
        return {"speech": f"More than one match — {names}. Which one?", "detail": names}
    if not found.get("id"):
        return {"speech": f"I don't know who {person} is.", "detail": ""}

    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        return {"speech": "Slack token is not set.", "detail": ""}
    headers = {"Authorization": f"Bearer {token}"}

    opened = requests.post(
        "https://slack.com/api/conversations.open",
        headers=headers, json={"users": found["id"]}, timeout=15,
    ).json()
    if not opened.get("ok"):
        return {"speech": f"Could not open the DM: {opened.get('error')}", "detail": ""}
    channel = opened["channel"]["id"]

    hist = requests.get(
        "https://slack.com/api/conversations.history",
        headers=headers, params={"channel": channel, "limit": limit}, timeout=15,
    ).json()
    if not hist.get("ok"):
        return {"speech": f"Could not read the DM: {hist.get('error')}", "detail": ""}

    human = [
        m for m in hist.get("messages", [])
        if m.get("subtype") != "bot_message" and not m.get("bot_id") and (m.get("text") or "").strip()
    ]
    if not human:
        return {"speech": f"No reply from {found['name']} yet.", "detail": ""}

    newest = human[0]
    speech = f"{found['name']} said: {newest.get('text', '')[:200]}"
    detail = "\n".join(f"- {m.get('text','')}" for m in human[:limit])
    return {"speech": speech, "detail": detail}


def send_dm(user_id: str, text: str) -> dict[str, Any]:
    """Send a Slack DM. Only ever called after an explicit spoken confirmation."""
    if not (text or "").strip():
        raise ValueError("refusing to send an empty message")
    ts = _slack().send_dm(user_id, text)
    return {"speech": "Sent.", "detail": f"to {user_id} at {ts}: {text}"}


def _lists(client) -> list:
    global _lists_cache
    if not _lists_cache:
        _lists_cache = client.get_lists()
    return _lists_cache


def _version_key(raw: str) -> tuple:
    """Order '386' and '1.0.39' correctly."""
    return tuple(int(p) for p in raw.split(".") if p.isdigit())


def _release_lists(client) -> list:
    """This domain's release lists, newest first."""
    if not RELEASE_PATTERN:
        return []
    rx = re.compile(RELEASE_PATTERN)
    matched = []
    for lst in _lists(client):
        m = rx.search(lst.name)
        if m:
            matched.append((_version_key(m.group(1)), lst))
    matched.sort(key=lambda pair: pair[0], reverse=True)
    return [lst for _, lst in matched]


def _raw_cards(client, list_id: str, fields: str = "name,idMembers") -> list[dict]:
    """One API call. get_cards_in_list would issue 1 + 3n.

    Deliberate use of the private _get: TrelloClient exposes no field-limited
    card fetch, and Global Constraints forbid modifying the Domain Expert repo
    to add one. Revisit if _get's signature drifts.
    """
    data = client._get(f"lists/{list_id}/cards", fields=fields)
    return data if isinstance(data, list) else []


def _short(name: str) -> str:
    """'From SL: ZI-691 - title' -> 'ZI-691'."""
    m = re.search(r"\b([A-Z]{2,4}-\d{1,5})\b", name)
    return m.group(1) if m else name[:40]


def card_status(card_id: str) -> dict[str, Any]:
    """Status of an issue card. Cards are named 'From SL: ZI-691 - <title>',
    so the ZI id is matched against the name rather than passed to get_card —
    Trello's card endpoint wants its own id or shortLink and 400s on 'ZI-691'.
    """
    client = _trello()
    token = card_id.upper()

    for lst in _release_lists(client):
        for raw in _raw_cards(client, lst.id, fields="name"):
            if token in raw.get("name", "").upper():
                return _describe(client, raw, lst.name)

    return {"speech": f"Couldn't find {card_id} in the current releases.", "detail": ""}


def _describe(client, raw: dict, list_name: str) -> dict[str, Any]:
    name = raw.get("name", "")
    title = name.split("—")[-1].strip() if "—" in name else name
    card_id = raw.get("id", "")

    members = client.get_card_members(card_id) if card_id else []
    who = (members[0].get("fullName") or members[0].get("username")) if members else "nobody"

    comments = client.get_card_comments(card_id) if card_id else []
    latest = comments[0] if comments else ""

    speech = f"{title[:90]} is in {list_name}, assigned to {who}."
    if latest:
        speech += f" Last comment: {latest[:160]}"
    return {"speech": speech, "detail": f"{name}\nList: {list_name}\nMembers: {who}\n\n{latest}"}


def release_status(release: str) -> dict[str, Any]:
    """Summarise a release list, e.g. 'MCSL 386' -> 'SL MCSL 386: Iteration backlog'."""
    client = _trello()
    wanted = re.sub(r"[^0-9.]", "", release)
    if not wanted:
        return {"speech": f"Didn't recognise the release {release}.", "detail": ""}

    for lst in _release_lists(client):
        if wanted in lst.name.replace(" ", ""):
            cards = _raw_cards(client, lst.id, fields="name")
            names = [c.get("name", "") for c in cards]
            speech = f"{lst.name} has {len(names)} cards"
            if names:
                speech += ": " + "; ".join(_short(n) for n in names[:4])
            return {"speech": speech, "detail": "\n".join(names)}

    return {"speech": f"No release list matching {release}.", "detail": ""}


def my_release_cards(release: str) -> dict[str, Any]:
    """Cards in ONE named release that are assigned to you.

    Distinct from release_status (everything in the release) and from my_tasks
    (your cards across the newest few releases). "In MCSL 385 how many tickets
    are assigned to me" needs both the scope and the filter honoured.
    """
    client = _trello()
    releases = _release_lists(client)
    wanted = re.sub(r"[^0-9.]", "", release)

    target = None
    if not wanted:
        # No release named: the newest one is what "my tickets" means.
        target = releases[0] if releases else None
    else:
        for lst in releases:
            if wanted in lst.name.replace(" ", ""):
                target = lst
                break
    if target is None:
        return {"speech": f"No release list matching {release}.", "detail": ""}

    me = client._get("members/me", fields="id")["id"]
    mine = [
        _short(raw.get("name", ""))
        for raw in _raw_cards(client, target.id)
        if me in (raw.get("idMembers") or [])
    ]

    if not mine:
        return {
            "speech": f"Nothing in {target.name} is assigned to you.",
            "detail": target.name,
            "ids": [],
            "release": target.name,
        }

    label = "ticket" if len(mine) == 1 else "tickets"
    return {
        "speech": f"{len(mine)} {label} assigned to you in {target.name}: " + ", ".join(mine),
        "detail": "\n".join(mine),
        "ids": mine,
        "release": target.name,
    }


def my_tasks() -> dict[str, Any]:
    """Cards assigned to the authenticated Trello member in the current releases."""
    client = _trello()
    # Deliberate use of the private _get: TrelloClient exposes no public
    # "who am I" call, and Global Constraints forbid modifying the Domain
    # Expert repo to add one.
    me = client._get("members/me", fields="id")["id"]

    mine = []
    for lst in _release_lists(client)[:3]:
        for raw in _raw_cards(client, lst.id, fields="name,idMembers"):
            if me in (raw.get("idMembers") or []):
                name = raw.get("name", "")
                after = name.split("\u2014", 1)[-1] if "\u2014" in name else name
                mine.append({
                    "id": _short(name),
                    "release": lst.name,
                    "title": re.sub(r"\s*\[#\d+\]\s*$", "", after).strip(),
                })

    if not mine:
        return {
            "speech": "Nothing assigned to you in the current releases.",
            "detail": "", "items": [],
        }

    speech = f"You have {len(mine)} cards. " + ", ".join(
        f"{m['id']} in {m['release']}" for m in mine[:5]
    )
    return {
        "speech": speech,
        "detail": "\n".join(f"{m['id']} \u2014 {m['release']}" for m in mine),
        "items": mine,
    }


def dev_status(query: str) -> dict[str, Any]:
    """Answer from the RAG store over wiki plus codebase."""
    from rag.vectorstore import search_filtered

    docs = search_filtered(query, k=3) or []
    if not docs:
        return {"speech": "Nothing in the knowledge base on that.", "detail": ""}

    top = docs[0]
    body = getattr(top, "page_content", str(top))
    source = getattr(top, "metadata", {}).get("source", "wiki")
    return {"speech": body[:240], "detail": f"source: {source}\n\n{body}"}


def customer_issues() -> dict[str, Any]:
    """Open ZI issues from the newest Zendesk intake file in the wiki.

    Reads the wiki directly rather than through RAG. The RAG path in
    requirement_research.py filters on a category string that wiki_loader.py
    never emits, so it returns nothing; reading the source file is both
    correct today and independent of that fix.
    """
    import re
    from pathlib import Path

    import config

    zdir = Path(config.WIKI_PATH) / "zendesk"
    files = sorted(zdir.glob("20*.md")) if zdir.is_dir() else []
    if not files:
        return {"speech": "No Zendesk intake files found.", "detail": ""}

    body = files[-1].read_text(errors="ignore")
    rows = re.findall(r"^\|\s*(ZI-\d+)\s*\|\s*([^|]+?)\s*\|", body, re.M)
    if not rows:
        return {"speech": f"No open issues in {files[-1].stem}.", "detail": body[:800]}

    speech = f"{len(rows)} open issues in {files[-1].stem}. " + "; ".join(
        f"{zi}: {title[:70]}" for zi, title in rows[:3]
    )
    detail = "\n".join(f"{zi} — {title}" for zi, title in rows)
    return {"speech": speech, "detail": detail}


def _classify(labels) -> "object":
    from qa_labels import classify

    return classify([l.get("name", "") if isinstance(l, dict) else str(l) for l in (labels or [])])


def _release_states(client, lst) -> list:
    """QA state for every card in a release list."""
    raw = _raw_cards(client, lst.id, fields="name,idMembers,labels")
    return [(c, _classify(c.get("labels"))) for c in raw]


def active_release() -> dict[str, Any]:
    """The release QA is actually working on.

    Not the highest number: a fresh intake list can exist with nothing started,
    while the real work sits one release back. Verified on the live board --
    MCSL 386 had 8 untouched cards while MCSL 385 had 17 sitting at QA. The
    active release is the one with outstanding testable work, most recent first.
    """
    from qa_labels import progress

    client = _trello()
    releases = _release_lists(client)
    if not releases:
        return {"release": "", "id": "", "outstanding": 0}

    # Where the bulk of the work is, not merely the first list with any.
    # Verified on the live board: MCSL 386 had 2 outstanding cards from a fresh
    # intake while MCSL 385 had 16 mid-test. Taking the first non-empty list
    # newest-first chose 386, which is the wrong answer to "what are we
    # working on". Ties break towards the newer release.
    scored = []
    for lst in releases[:4]:
        p = progress([st for _, st in _release_states(client, lst)])
        scored.append((p["outstanding"], lst, p))

    scored.sort(key=lambda t: t[0], reverse=True)
    outstanding, lst, p = scored[0]
    if not outstanding:
        lst, p = releases[0], progress([st for _, st in _release_states(client, releases[0])])
    return {"release": lst.name, "id": lst.id, **p}


def release_progress(release: str = "") -> dict[str, Any]:
    """Spoken answer to "is 385 done" / "what is left in 385"."""
    from qa_labels import progress

    client = _trello()
    releases = _release_lists(client)
    wanted = re.sub(r"[^0-9.]", "", release)

    target = None
    if wanted:
        target = next((l for l in releases if wanted in l.name.replace(" ", "")), None)
    else:
        active = active_release()
        target = next((l for l in releases if l.name == active.get("release")), None)
    if target is None:
        return {"speech": f"No release list matching {release}.", "detail": ""}

    pairs = _release_states(client, target)
    p = progress([st for _, st in pairs])

    if p["complete"]:
        speech = f"{target.name} is complete: all {p['verified']} testable cards verified."
    else:
        speech = (
            f"{target.name} is in progress: {p['verified']} of {p['testable']} verified, "
            f"{p['outstanding']} still to test."
        )
    extras = []
    if p["skipped"]:
        extras.append(f"{p['skipped']} closed by support")
    if p["duplicates"]:
        extras.append(f"{p['duplicates']} marked duplicate")
    if p["spilled"]:
        extras.append(f"{p['spilled']} spilled over")
    if extras:
        speech += " Also " + ", ".join(extras) + "."

    detail = "\n".join(
        f"{_short(c.get('name',''))}  {st.state}" + (f"  ({st.note})" if st.note else "")
        for c, st in pairs
    )
    return {"speech": speech, "detail": detail, **p, "release": target.name}


def my_work(release: str = "") -> dict[str, Any]:
    """Your cards and what each one actually needs.

    The point of this over my_tasks: it reads the QA labels, so a verified card
    is not presented as work, a support-closed card is not presented as
    testing, and a duplicate is flagged as a sanity check.
    """
    client = _trello()
    me = client._get("members/me", fields="id")["id"]
    releases = _release_lists(client)
    wanted = re.sub(r"[^0-9.]", "", release)
    if wanted:
        releases = [l for l in releases if wanted in l.name.replace(" ", "")]
    else:
        releases = releases[:3]

    items = []
    for lst in releases:
        for card, st in _release_states(client, lst):
            if me not in (card.get("idMembers") or []):
                continue
            name = card.get("name", "")
            after = name.split("\u2014", 1)[-1] if "\u2014" in name else name
            items.append({
                "id": _short(name),
                "release": lst.name,
                "title": re.sub(r"\s*\[#\d+\]\s*$", "", after).strip(),
                "state": st.state,
                "meaning": st.meaning,
                "note": st.note,
                "duplicate": st.duplicate,
                "actionable": st.actionable,
            })

    todo = [i for i in items if i["actionable"]]
    done = [i for i in items if not i["actionable"]]

    if not items:
        return {"speech": "Nothing assigned to you in the current releases.", "detail": "", "items": []}
    if not todo:
        return {
            "speech": f"All {len(done)} of your cards are done — nothing to test.",
            "detail": "\n".join(f"{i['id']} {i['state']}" for i in items),
            "items": items,
        }

    bits = []
    for i in todo[:4]:
        bit = f"{i['id']}, {i['meaning']}"
        if i["note"]:
            bit += f" — {i['note']}"
        bits.append(bit)
    speech = f"{len(todo)} to test. " + ". ".join(bits) + "."
    if done:
        speech += f" {len(done)} already done."
    return {
        "speech": speech,
        "detail": "\n".join(
            f"{i['id']}  {i['state']}  {i['release']}" + (f"  ({i['note']})" if i["note"] else "")
            for i in items
        ),
        "items": items,
    }


def _find_card(client, card_id: str):
    """Locate a card by its ZI id across the release lists. Returns raw dict."""
    token = card_id.upper()
    for lst in _release_lists(client):
        for raw in _raw_cards(client, lst.id, fields="name,desc,idMembers"):
            if token in raw.get("name", "").upper():
                return raw, lst
    return None, None


def card_devs(card_id: str) -> dict[str, Any]:
    """Who is working on a card.

    Trello members plus the PR link from the description -- the PR is usually
    the most direct answer to "who is the dev", since a card carries the whole
    squad as members but only the author opens the pull request.
    """
    client = _trello()
    raw, lst = _find_card(client, card_id)
    if raw is None:
        return {"speech": f"Couldn't find {card_id} in the current releases.", "detail": ""}

    people = [
        m.get("fullName") or m.get("username", "")
        for m in client.get_card_members(raw["id"])
    ]
    prs = re.findall(r"https?://\S*?(?:bitbucket|github)\S*?pull-?requests?/\d+", raw.get("desc", ""))

    if not people and not prs:
        return {"speech": f"Nobody is assigned to {card_id}.", "detail": ""}

    if people and prs:
        speech = f"{card_id} has {', '.join(people)} on it, and a pull request open."
    elif people:
        speech = f"{card_id} has {', '.join(people)} on it."
    else:
        speech = f"{card_id} has nobody assigned, but a pull request is open."
    return {
        "speech": speech,
        "detail": f"{raw.get('name','')}\nMembers: {', '.join(people)}\nPRs: {chr(10).join(prs)}",
    }


def test_plan(card_id: str) -> dict[str, Any]:
    """The generated test cases for a card.

    These are produced by the AC/TC pipeline into output/<release>/<ZI>/tc.md,
    so this reads what the team already generated rather than inventing a plan.
    """
    from pathlib import Path

    token = card_id.upper()
    root = Path.cwd() / "output"
    if not root.is_dir():
        return {"speech": "No generated test plans on disk.", "detail": ""}

    hits = sorted(root.glob(f"*/{token}/tc.md")) or sorted(root.glob(f"*/{token}*/tc.md"))
    if not hits:
        return {"speech": f"No test plan generated for {card_id} yet.", "detail": ""}

    body = hits[0].read_text(errors="ignore")
    titles = re.findall(r"^###\s*(TC-\d+):\s*(.+)$", body, re.M)
    if not titles:
        return {"speech": f"Found a test plan for {card_id} but no numbered cases.", "detail": body[:1500]}

    label = "case" if len(titles) == 1 else "cases"
    head = "; ".join(f"{n} {t[:70]}" for n, t in titles[:3])
    extra = f", and {len(titles) - 3} more" if len(titles) > 3 else ""
    return {
        "speech": f"{len(titles)} test {label} for {card_id}. {head}{extra}.",
        "detail": body[:4000],
    }


def post_channel(channel: str, text: str) -> dict[str, Any]:
    """Post to a Slack channel. Only ever called after an explicit spoken yes."""
    if not (text or "").strip():
        raise ValueError("refusing to post an empty message")
    client = _slack()
    target = channel if channel.startswith(("#", "C")) else f"#{channel}"
    client.post_to_channel(text, channel=target)
    return {"speech": f"Posted to {target}.", "detail": f"{target}: {text}"}


def zendesk_latest() -> dict[str, Any]:
    """The newest Zendesk intake file and the ZI ids it introduced.

    Read straight from the wiki working tree. No Zendesk API credentials
    exist, and the release-batch workflow does not need minute-by-minute
    tickets -- a new intake commit is the event that matters.
    """
    from pathlib import Path

    import config

    zdir = Path(config.WIKI_PATH) / "zendesk"
    files = sorted(zdir.glob("20*.md")) if zdir.is_dir() else []
    if not files:
        return {"file": "", "ids": [], "titles": {}}

    newest = files[-1]
    body = newest.read_text(errors="ignore")
    rows = re.findall(r"^\|\s*(ZI-\d+)\s*\|\s*([^|]+?)\s*\|", body, re.M)
    # The same id appears in more than one table in these files; keep the
    # first occurrence and its title.
    ids: list[str] = []
    titles: dict[str, str] = {}
    for zi, title in rows:
        if zi not in titles:
            ids.append(zi)
            titles[zi] = title
    return {"file": newest.stem, "ids": ids, "titles": titles}


def release_card_ids() -> dict[str, Any]:
    """Card ids in the newest release list, for diffing against last check."""
    client = _trello()
    releases = _release_lists(client)
    if not releases:
        return {"release": "", "ids": []}

    newest = releases[0]
    ids: list[str] = []
    titles: dict[str, str] = {}
    for raw in _raw_cards(client, newest.id, fields="name"):
        name = raw.get("name", "")
        token = _short(name)
        if re.fullmatch(r"[A-Z]{2,4}-\d{1,5}", token):
            ids.append(token)
            # "From SL: ZI-691 - Title [#399431]" -> "Title"
            after = name.split("\u2014", 1)[-1] if "\u2014" in name else name
            titles[token] = re.sub(r"\s*\[#\d+\]\s*$", "", after).strip()
    return {"release": newest.name, "ids": sorted(set(ids)), "titles": titles}


def summarise(text: str, question: str = "") -> dict[str, Any]:
    """Condense long output into one or two spoken sentences.

    Runs here rather than in jarvis/ because the API key lives in this repo's
    .env, which the worker already loads and jarvis/ deliberately does not.

    Falls back to a truncated head if the model is unavailable — a degraded
    answer beats silence.
    """
    body = (text or "").strip()
    if not body:
        return {"speech": "", "detail": ""}

    fallback = " ".join(body.split())[:240]
    try:
        import config
        from langchain_anthropic import ChatAnthropic

        if not config.ANTHROPIC_API_KEY:
            return {"speech": fallback, "detail": body}

        llm = ChatAnthropic(
            model=config.CLAUDE_HAIKU_MODEL,
            api_key=config.ANTHROPIC_API_KEY,
            temperature=0,
            max_tokens=200,
        )
        asked = f"The user asked: {question}\n\n" if question else ""
        prompt = (
            "You are summarising output for a voice assistant to read aloud.\n"
            "Rules: one or two short sentences. Plain spoken English. No markdown, "
            "no bullet points, no file paths, no code. Lead with the answer. If the "
            "output is an error or a request for permission, say plainly what is "
            "blocked.\n\n"
            f"{asked}Output to summarise:\n{body[:6000]}"
        )
        said = llm.invoke(prompt).content
        if isinstance(said, list):  # some versions return content blocks
            said = " ".join(b.get("text", "") for b in said if isinstance(b, dict))
        said = " ".join(str(said).split()).strip()
        return {"speech": said or fallback, "detail": body}
    except Exception as exc:
        return {"speech": fallback, "detail": f"[summary unavailable: {exc}]\n\n{body}"}


def vocab() -> dict[str, Any]:
    """Live entity snapshot for the correction layer.

    Scoped to the three newest release lists — one API call each. A full board
    walk would be ~7000 requests and take the better part of an hour.
    """
    client = _trello()
    releases = _release_lists(client)

    ids: set[str] = set()
    for lst in releases[:3]:
        for raw in _raw_cards(client, lst.id, fields="name"):
            for m in re.finditer(r"\b([A-Z]{2,4}-\d{1,5})\b", raw.get("name", "")):
                ids.add(m.group(1))

    # Release tokens so "status of MCSL 386" can snap to a real release.
    # Only emitted where releases are plain integers; AuPost/FedEx use dotted
    # versions that the ID pattern cannot represent, so they get none.
    if RELEASE_TOKEN:
        rx = re.compile(RELEASE_PATTERN)
        for lst in releases:
            m = rx.search(lst.name)
            if m and m.group(1).isdigit():
                ids.add(f"{RELEASE_TOKEN}-{m.group(1)}")

    people = [m.get("fullName", "") for m in client.get_board_members() if m.get("fullName")]

    return {
        "cards": sorted(ids),
        "people": sorted(set(people)),
        "carriers": ["gls", "ups", "fedex", "dhl", "usps", "india post", "canada post"],
        "zi_ids": _zi_ids(),
    }


def _zi_ids() -> list[str]:
    """Parse valid ZI ranges from the wiki's zendesk intake frontmatter."""
    import re
    from pathlib import Path

    import config

    zdir = Path(config.WIKI_PATH) / "zendesk"
    if not zdir.is_dir():
        return []

    ids: set[str] = set()
    for path in zdir.glob("*.md"):
        head = path.read_text(errors="ignore")[:1200]
        m = re.search(r"new_ids_assigned:\s*\"?ZI-(\d+)\s*→\s*ZI-(\d+)", head)
        if m:
            for n in range(int(m.group(1)), int(m.group(2)) + 1):
                ids.add(f"ZI-{n}")
    return sorted(ids)


HANDLERS = {
    "card_status": card_status,
    "resolve_person": resolve_person,
    "send_dm": send_dm,
    "read_replies": read_replies,
    "card_devs": card_devs,
    "test_plan": test_plan,
    "post_channel": post_channel,
    "zendesk_latest": zendesk_latest,
    "release_card_ids": release_card_ids,
    "summarise": summarise,
    "release_status": release_status,
    "my_tasks": my_tasks,
    "my_release_cards": my_release_cards,
    "my_work": my_work,
    "active_release": active_release,
    "release_progress": release_progress,
    "dev_status": dev_status,
    "customer_issues": customer_issues,
    "vocab": vocab,
}

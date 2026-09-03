"""Answer questions about a screenshot, and about the document in front.

Lives in the worker rather than in jarvis/ for the same reason `summarise` does:
the API key is in this repo's .env, which the worker already loads. Reading PDFs
needs `pypdf`, which is likewise installed here and not in the jarvis venv.

Images cross the socket as a *path*, never as base64. Both processes are on this
machine, so sending a megabyte of encoded PNG down a local socket would be a
megabyte of pointless framing.
"""
from __future__ import annotations

import base64
import os
import re
from typing import Any

# Claude's own limit is 5MB per image; a window screenshot is well under 1MB, so
# hitting this means something is wrong rather than merely large.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Enough of a document to answer a question about it. Roughly 15k tokens.
MAX_DOC_CHARS = 60_000

_SPOKEN = (
    "You are answering out loud for a voice assistant. Two or three short "
    "sentences of plain spoken English. No markdown, no headings, no bullet "
    "points, no code blocks. Lead with the answer. If the answer depends on "
    "something you cannot see, say what is missing in one line."
)

# Asking "is this correct?" and being told what is on the screen is useless. On
# a rate request whose totalPackageCount was 2 beside a single package line
# item, the prompt above described the screen accurately and called it correct
# three times out of three; this one named the wrong field three out of three.
# Same model, same screenshot -- the instruction to judge rather than describe
# was the entire difference.
_JUDGE = (
    "You are checking a QA engineer's work and answering OUT LOUD, so the reply "
    "is heard once and cannot be re-read.\n"
    "Check it properly: counts against the entries actually present, totals "
    "against the sum of their parts, units, and any figure appearing twice.\n"
    "Then report ONLY the verdict, in at most 45 words and at most three "
    "sentences. Rules for the reply:\n"
    "- Start with the verdict itself. No preamble, no 'working this out', no "
    "restating the question.\n"
    "- Do not show arithmetic and do not list the figures that were fine. Name a "
    "number only when it IS the problem.\n"
    "- Never contradict yourself: no 'wait', no 'actually', no 'that checks out' "
    "followed by a reversal.\n"
    "- If it is self-consistent but still suspicious, say what is suspicious and "
    "that a human should confirm.\n"
    "Plain spoken English. No markdown."
)
# Judging needs room, but the room goes into checking rather than into the
# answer. Three measurements on one 43,000-character rate log:
#   400 tokens, "judge it"           -> 0/3. Cut off mid-sentence, and one reply
#                                       talked itself out of the finding.
#   1200 tokens, "work it out first" -> 4/4 correct but 126 words, ~45 seconds
#                                       of speech, opening with "Working this
#                                       out before writing anything:" -- it
#                                       showed the thinking it was told to do.
#   1200 tokens, verdict-only rules  -> 4/4 correct at 44-53 words.
# So the budget stays high and the output limit is stated in the prompt.
JUDGE_TOKENS = 1200

# "Is this right", "does this look correct", "anything wrong here", "check this".
_CHECKING = re.compile(
    r"\b(?:correct|right|wrong|ok|okay|fine|valid|accurate|matching?|mismatch|"
    r"issue|issues|problem|error|off|broken|check|verify|sanity)\b",
    re.I,
)


def judging(question: str) -> bool:
    """Whether the question asks for a verdict rather than a description."""
    return bool(_CHECKING.search(question or ""))


def _screen_prompt(question: str) -> str:
    """Judge when asked whether something is right; describe when asked what."""
    return _JUDGE if judging(question) else _SPOKEN


def _api(task: str) -> tuple[Any, str] | None:
    """Client and model. Screens and documents do not get the same model.

    Measured on one screenshot of a FedEx rate request whose totalPackageCount
    was 2 while it carried a single package line item -- a real MCSL-class bug.
    Sonnet named it exactly. Haiku missed it and answered that the dimensions
    "seem quite large", which is plausible, confident, and wrong. Reading small
    text off pixels is where the cheaper model fails, and a wrong answer to "is
    this correct?" is worse than no answer.

    Documents keep Haiku for *reading*: their text arrives exact rather than
    inferred from an image, so there is nothing to misread, and it answered real
    support guides correctly at a third of the cost.

    Judging a document is a different job, and that reasoning did not cover it.
    Asked whether a fuel surcharge in a 43,000-character rate log was right,
    Haiku spotted the two FUEL entries but never landed the verdict in three
    tries -- one answer talked itself out of the finding with "the math does
    check out". So a question that asks for a verdict gets the stronger model
    whatever it is looking at.
    """
    try:
        import config
        from anthropic import Anthropic

        if not config.ANTHROPIC_API_KEY:
            return None
        strong = task == "screen" or task == "judge"
        default = config.CLAUDE_SONNET_MODEL if strong else config.CLAUDE_HAIKU_MODEL
        model = os.environ.get(
            "JARVIS_VISION_MODEL" if task == "screen" else "JARVIS_DOC_MODEL", ""
        ).strip() or default
        return Anthropic(api_key=config.ANTHROPIC_API_KEY), model
    except Exception:
        return None


# Below this width, dense text in a screenshot is not reliably legible. The
# same rate request, same model, same prompt: rendered 1470 wide at 2x the
# duplicate package line item was found 3 times out of 3; rendered 1100 wide at
# 1x it was called correct 3 times out of 3. Resolution was the whole
# difference, not the wording. A real `screencapture` on a Retina display is 2x,
# so an ordinary window clears this comfortably -- it is a small or
# low-resolution window that does not.
MIN_JUDGE_WIDTH = 1200


def png_width(path: str) -> int:
    """Pixel width from the PNG header. No image library needed.

    Bytes 16-20 of a PNG are the IHDR width, big-endian.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
            return 0
        return int.from_bytes(head[16:20], "big")
    except OSError:
        return 0


def _say(client, model: str, blocks: list[dict], max_tokens: int = 400) -> str:
    reply = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": blocks}],
    )
    parts = [b.text for b in reply.content if getattr(b, "type", "") == "text"]
    return " ".join(" ".join(parts).split()).strip()


# ---- looking at the screen ------------------------------------------------

def look(path: str, question: str = "", window: str = "") -> dict[str, Any]:
    """Answer a question about a captured window.

    `window` is what Jarvis said it photographed. It goes into the prompt as
    well as the reply, because "is this correct?" is much easier to answer when
    you know whether you are looking at a browser or a terminal.
    """
    if not path or not os.path.exists(path):
        return {"speech": "The screenshot did not save, so I have nothing to look at.", "ok": False}
    size = os.path.getsize(path)
    if size == 0:
        return {"speech": "The screenshot came out empty.", "ok": False}
    if size > MAX_IMAGE_BYTES:
        return {"speech": "That window is too large to send. Try a smaller window.", "ok": False}

    api = _api("screen")
    if api is None:
        return {"speech": "I cannot look at the screen without an API key.", "ok": False}
    client, model = api

    asked = (question or "").strip() or "What is on this screen, and is anything wrong with it?"
    where = f"This is the {window} window. " if window else ""
    try:
        data = base64.standard_b64encode(open(path, "rb").read()).decode()
        said = _say(client, model, [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": data}},
            {"type": "text", "text": f"{where}{asked}\n\n{_screen_prompt(question)}"},
        ], max_tokens=JUDGE_TOKENS if judging(question) else 400)
        # A verdict from a small screenshot is not trustworthy, and saying so is
        # better than sounding certain: on a 1100px render the same question was
        # answered "the request is correct" three times out of three.
        if said and judging(question) and 0 < png_width(path) < MIN_JUDGE_WIDTH:
            said += (" That window is small, so I may have misread it -- "
                     "point me at the file if you have one.")
        return {"speech": said or "I could not make anything out on that screen.",
                "detail": f"[looked at {window or path}]", "ok": bool(said)}
    except Exception as exc:
        return {"speech": f"I could not read that screen. {type(exc).__name__}.",
                "detail": str(exc), "ok": False}
    finally:
        # The capture is a photograph of the user's screen. Keeping it on disk
        # after the question is answered serves nobody.
        try:
            os.unlink(path)
        except OSError:
            pass


# ---- reading a document --------------------------------------------------

def _pdf_text(path: str) -> str:
    from pypdf import PdfReader

    pages = []
    for page in PdfReader(path).pages:
        pages.append(page.extract_text() or "")
        if sum(len(p) for p in pages) > MAX_DOC_CHARS:
            break
    return "\n".join(pages)


# Formats that are not text and have no business being decoded as text. The
# first bytes of a file say what it is far more reliably than its extension.
_MAGIC = {
    b"%PDF": "a PDF",
    b"PK\x03\x04": "a zip or Office file",
    b"\x89PNG": "an image",
    b"\xff\xd8\xff": "an image",
    b"GIF8": "an image",
    b"\x1f\x8b": "a gzip file",
}
# Content types that are prose. Anything else fetched over http is refused.
_TEXTUAL = ("text/", "application/json", "application/xml", "+xml",
            "application/javascript", "application/x-yaml")


def _binary_kind(path: str) -> str:
    """What sort of non-text file this is, or "" when it is text.

    Character statistics cannot do this job. This PDF's first 4000 bytes are its
    XMP metadata header -- 47% letters, 22% spaces, no control characters and no
    replacement characters, statistically indistinguishable from prose. Its
    first four bytes, however, are "%PDF".
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return ""
    for magic, name in _MAGIC.items():
        if head.startswith(magic):
            return name
    # A NUL byte in the first block is the classic text/binary test and catches
    # formats not listed above.
    return "a binary file" if b"\x00" in head else ""


def extract(ref: str, kind: str) -> tuple[str, str]:
    """Get the text of a document. Returns (text, problem); one is always empty."""
    # A file:// URL is a file. Chrome hands one back for any local PDF it has
    # open, and routing it down the web path skips PDF extraction entirely.
    if kind == "url" and ref.startswith("file://"):
        from urllib.parse import unquote, urlparse

        return extract(unquote(urlparse(ref).path), "file")

    if kind == "url":
        try:
            import urllib.request

            req = urllib.request.Request(ref, headers={"User-Agent": "jarvis"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if ctype and not any(t in ctype for t in _TEXTUAL):
                    return "", ("That page is not text I can read. "
                                "Ask me to look at the screen instead.")
                raw = resp.read(2 * MAX_DOC_CHARS).decode("utf-8", errors="replace")
        except Exception as exc:
            return "", f"I could not open that page. {type(exc).__name__}."
        return _strip_html(raw), ""

    if not os.path.exists(ref):
        return "", f"I cannot find {os.path.basename(ref)} on disk."
    if os.path.getsize(ref) == 0:
        return "", f"{os.path.basename(ref)} is empty."

    lower = ref.lower()
    try:
        if lower.endswith(".pdf"):
            return _pdf_text(ref), ""
        if lower.endswith((".docx", ".rtf", ".doc")):
            import subprocess

            done = subprocess.run(
                ["textutil", "-stdout", "-convert", "txt", ref],
                capture_output=True, text=True, timeout=30,
            )
            if done.returncode != 0:
                return "", f"I could not convert {os.path.basename(ref)} to text."
            return done.stdout, ""
        binary = _binary_kind(ref)
        if binary:
            return "", (f"{os.path.basename(ref)} is {binary}, not something I "
                        "can read as text. Ask me to look at the screen instead.")
        with open(ref, errors="replace") as fh:
            return fh.read(2 * MAX_DOC_CHARS), ""
    except Exception as exc:
        return "", f"I could not read that document. {type(exc).__name__}."


def _strip_html(raw: str) -> str:
    import re

    body = re.sub(r"(?is)<(script|style|nav|footer)\b.*?</\1>", " ", raw)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        body = body.replace(entity, char)
    return re.sub(r"[ \t]+", " ", body)


def read_doc(ref: str, kind: str, question: str = "", name: str = "") -> dict[str, Any]:
    """Answer a question about the document open in front."""
    text, problem = extract(ref, kind)
    if problem:
        return {"speech": problem, "ok": False}
    text = text.strip()
    if not text:
        label = name or os.path.basename(ref) or "that document"
        return {"speech": f"I opened {label} but found no text in it. "
                          "If it is a scan, ask me to look at the screen instead.",
                "ok": False}

    verdict = judging(question)
    api = _api("judge" if verdict else "doc")
    if api is None:
        return {"speech": "I cannot read documents without an API key.", "ok": False}
    client, model = api

    asked = (question or "").strip() or "What is this document about, and what should I know from it?"
    label = name or os.path.basename(ref) or ref
    clipped = text[:MAX_DOC_CHARS]
    try:
        said = _say(client, model, [{"type": "text", "text": (
            f"Document: {label}\n\n{clipped}\n\n"
            f"---\nQuestion: {asked}\n\n{_JUDGE if verdict else _SPOKEN}"
        )}], max_tokens=JUDGE_TOKENS if verdict else 400)
        note = "" if len(text) <= MAX_DOC_CHARS else " That is from the first part of the document."
        return {"speech": (said or f"I read {label} but could not answer that.") + note,
                "detail": f"[read {label}, {len(text)} characters]", "ok": bool(said)}
    except Exception as exc:
        return {"speech": f"I could not read that document. {type(exc).__name__}.",
                "detail": str(exc), "ok": False}

"""The worker side of seeing: model choice, extraction, and failure text.

Every API import in worker/vision.py is lazy, so this runs in the jarvis venv
without `anthropic` or `pypdf` installed.
"""
import sys
import types

import pytest

from worker import vision


@pytest.fixture
def fake_config(monkeypatch):
    """Stand in for the Domain Expert repo's config module."""
    mod = types.ModuleType("config")
    mod.ANTHROPIC_API_KEY = "sk-test"
    mod.CLAUDE_SONNET_MODEL = "claude-sonnet-4-6"
    mod.CLAUDE_HAIKU_MODEL = "claude-haiku-4-5"
    monkeypatch.setitem(sys.modules, "config", mod)

    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = lambda api_key=None: object()
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    return mod


# --- which model reads which thing ---------------------------------------

def test_screens_get_the_stronger_model(fake_config, monkeypatch):
    """Measured: on a rate request with totalPackageCount 2 and one package
    line item, Sonnet named the mismatch and Haiku answered that the dimensions
    "seem quite large" -- confident and wrong."""
    monkeypatch.delenv("JARVIS_VISION_MODEL", raising=False)
    assert vision._api("screen")[1] == "claude-sonnet-4-6"


def test_documents_get_the_cheaper_model(fake_config, monkeypatch):
    """Document text arrives exact rather than inferred from pixels, so there
    is nothing to misread."""
    monkeypatch.delenv("JARVIS_DOC_MODEL", raising=False)
    assert vision._api("doc")[1] == "claude-haiku-4-5"


def test_either_model_can_be_overridden(fake_config, monkeypatch):
    monkeypatch.setenv("JARVIS_VISION_MODEL", "claude-opus-5")
    monkeypatch.setenv("JARVIS_DOC_MODEL", "claude-opus-5")
    assert vision._api("screen")[1] == "claude-opus-5"
    assert vision._api("doc")[1] == "claude-opus-5"


def test_no_key_means_no_client(monkeypatch, fake_config):
    fake_config.ANTHROPIC_API_KEY = ""
    assert vision._api("screen") is None


def test_a_missing_config_module_is_not_a_crash(monkeypatch):
    monkeypatch.setitem(sys.modules, "config", None)
    assert vision._api("screen") is None


# --- looking, without ever reaching the API ------------------------------

def test_a_missing_screenshot_is_reported_in_words():
    out = vision.look("/tmp/definitely-not-here.png", question="is this right")
    assert out["ok"] is False
    assert "screenshot" in out["speech"].lower()


def test_an_empty_screenshot_is_reported(tmp_path):
    empty = tmp_path / "e.png"
    empty.touch()
    out = vision.look(str(empty))
    assert out["ok"] is False
    assert "empty" in out["speech"].lower()


def test_an_oversized_image_is_refused_before_upload(tmp_path, monkeypatch):
    big = tmp_path / "big.png"
    big.write_bytes(b"x")
    monkeypatch.setattr(vision.os.path, "getsize", lambda p: vision.MAX_IMAGE_BYTES + 1)
    out = vision.look(str(big))
    assert out["ok"] is False
    assert "too large" in out["speech"]


def test_the_screenshot_is_deleted_once_answered(tmp_path, fake_config, monkeypatch):
    """It is a photograph of the user's screen; it has no business outliving
    the answer."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    monkeypatch.setattr(vision, "_say", lambda *a, **k: "all fine")
    out = vision.look(str(shot), question="ok?")
    assert out["ok"] is True
    assert not shot.exists()


def test_the_screenshot_is_deleted_even_when_the_call_fails(tmp_path, fake_config, monkeypatch):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(vision, "_say", boom)
    out = vision.look(str(shot))
    assert out["ok"] is False
    assert not shot.exists()


def test_the_window_name_reaches_the_prompt(tmp_path, fake_config, monkeypatch):
    """"Is this correct?" is much easier to answer knowing whether you are
    looking at a browser or a terminal."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    seen = {}

    def capture_say(client, model, blocks, max_tokens=400):
        seen["text"] = blocks[-1]["text"]
        return "fine"

    monkeypatch.setattr(vision, "_say", capture_say)
    vision.look(str(shot), question="is the weight right", window="Google Chrome, Trello")
    assert "Google Chrome" in seen["text"]
    assert "is the weight right" in seen["text"]


# --- extracting document text -------------------------------------------

def test_plain_text_is_read_as_is(tmp_path):
    doc = tmp_path / "a.md"
    doc.write_text("# Guide\nStep one.")
    text, problem = vision.extract(str(doc), "file")
    assert problem == ""
    assert "Step one" in text


def test_a_missing_file_names_itself(tmp_path):
    text, problem = vision.extract(str(tmp_path / "gone.pdf"), "file")
    assert text == ""
    assert "gone.pdf" in problem


def test_an_empty_file_is_reported(tmp_path):
    doc = tmp_path / "e.md"
    doc.touch()
    _, problem = vision.extract(str(doc), "file")
    assert "empty" in problem


def test_a_bad_url_is_reported_not_raised():
    text, problem = vision.extract("http://127.0.0.1:1/nothing", "url")
    assert text == ""
    assert problem and "could not open" in problem


@pytest.mark.parametrize("raw,expected,absent", [
    ("<p>Rate is 250 INR</p>", "Rate is 250 INR", "<p>"),
    ("<script>var x=1</script><p>hi</p>", "hi", "var x"),
    ("<p>a &amp; b &lt;c&gt;</p>", "a & b <c>", "&amp;"),
])
def test_html_is_reduced_to_its_text(raw, expected, absent):
    out = vision._strip_html(raw)
    assert expected in out
    assert absent not in out


def test_a_document_with_no_text_suggests_looking_instead(tmp_path, fake_config):
    """A scanned PDF extracts to nothing, and the screen is the way in."""
    doc = tmp_path / "scan.md"
    doc.write_text("   \n  ")
    out = vision.read_doc(str(doc), "file", name="scan.md")
    assert out["ok"] is False
    assert "look at the screen" in out["speech"]


def test_a_clipped_document_says_so(tmp_path, fake_config, monkeypatch):
    """Answering from the first third of a document without saying so invites
    a confident wrong answer about what a later section contains."""
    doc = tmp_path / "long.md"
    doc.write_text("x" * (vision.MAX_DOC_CHARS + 5000))
    monkeypatch.setattr(vision, "_say", lambda *a, **k: "It is about rates.")
    out = vision.read_doc(str(doc), "file", name="long.md")
    assert "first part" in out["speech"]


def test_a_short_document_does_not_claim_to_be_clipped(tmp_path, fake_config, monkeypatch):
    doc = tmp_path / "short.md"
    doc.write_text("Rates are fine.")
    monkeypatch.setattr(vision, "_say", lambda *a, **k: "Rates are fine.")
    assert "first part" not in vision.read_doc(str(doc), "file")["speech"]


# --- judging versus describing -------------------------------------------

@pytest.mark.parametrize("question", [
    "is this correct",
    "see this rate request going out for that order, is it correct",
    "does this look right",
    "anything wrong here",
    "check this request",
    "is the weight correct",
    "any issue with this",
    "verify this",
])
def test_a_correctness_question_gets_the_judging_prompt(question):
    """Measured on the same screenshot of a rate request carrying
    totalPackageCount 2 beside a single package line item: the describing
    prompt called it correct 3 times out of 3, the judging prompt named the
    wrong field 3 out of 3."""
    assert vision._screen_prompt(question) is vision._JUDGE


@pytest.mark.parametrize("question", [
    "what is on my screen",
    "what am I looking at",
    "look at this",
    "what do you see",
])
def test_an_open_question_still_gets_a_description(question):
    """"What's on my screen" answered with "everything is consistent" would be
    a non-answer."""
    assert vision._screen_prompt(question) is vision._SPOKEN


def test_no_question_at_all_describes():
    assert vision._screen_prompt("") is vision._SPOKEN
    assert vision._screen_prompt(None) is vision._SPOKEN


def test_the_judging_prompt_reaches_the_model(tmp_path, fake_config, monkeypatch):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    seen = {}
    monkeypatch.setattr(vision, "_say",
                        lambda c, m, blocks, max_tokens=400: seen.setdefault("t", blocks[-1]["text"]) or "x")
    vision.look(str(shot), question="is this request correct")
    assert "judge it" in seen["t"]


# --- telling text from bytes ---------------------------------------------

@pytest.mark.parametrize("head,expected", [
    (b"%PDF-1.7\nstuff", "a PDF"),
    (b"PK\x03\x04rest", "a zip or Office file"),
    (b"\x89PNG\r\n", "an image"),
    (b"\xff\xd8\xff\xe0", "an image"),
    (b"\x1f\x8b\x08", "a gzip file"),
    (b"plain\x00bytes", "a binary file"),
])
def test_binary_files_are_identified_by_their_first_bytes(tmp_path, head, expected):
    """Character statistics cannot do this. This PDF's first 4000 bytes are its
    XMP metadata header: 47% letters, 22% spaces, no control characters -- the
    same profile as prose. Its first four bytes are "%PDF"."""
    f = tmp_path / "mystery"
    f.write_bytes(head)
    assert vision._binary_kind(str(f)) == expected


@pytest.mark.parametrize("body", [
    b"# Guide\nStep one.\n", b'{"rate": 250}', b"plain text\r\n\ttabbed",
])
def test_text_files_are_not_flagged(tmp_path, body):
    f = tmp_path / "doc.md"
    f.write_bytes(body)
    assert vision._binary_kind(str(f)) == ""


def test_an_extensionless_binary_is_refused_with_a_suggestion(tmp_path):
    """Extension-based dispatch cannot catch this one; the bytes can."""
    f = tmp_path / "mystery"
    f.write_bytes(b"%PDF-1.7 junk")
    text, problem = vision.extract(str(f), "file")
    assert text == ""
    assert "a PDF" in problem
    assert "look at the screen" in problem


def test_a_missing_file_does_not_look_binary(tmp_path):
    assert vision._binary_kind(str(tmp_path / "nope")) == ""


# --- a file:// URL is a file ---------------------------------------------

def test_a_file_url_is_read_as_a_file_not_fetched_as_a_page(tmp_path, monkeypatch):
    """Chrome hands back a file:// URL for any local PDF it has open. Routing
    that down the web path skipped PDF extraction and produced 53,000
    characters of PDF internals decoded as HTML."""
    doc = tmp_path / "guide.md"
    doc.write_text("# Rates\nAll good.")
    text, problem = vision.extract(f"file://{doc}", "url")
    assert problem == ""
    assert "All good" in text


def test_a_file_url_with_escaped_spaces_resolves(tmp_path):
    doc = tmp_path / "my guide.md"
    doc.write_text("content here")
    text, problem = vision.extract(f"file://{str(doc).replace(' ', '%20')}", "url")
    assert problem == ""
    assert "content here" in text


def test_a_file_url_to_a_pdf_reaches_the_pdf_reader(tmp_path, monkeypatch):
    seen = {}

    def fake_pdf(path):
        seen["path"] = path
        return "page one"

    monkeypatch.setattr(vision, "_pdf_text", fake_pdf)
    doc = tmp_path / "g.pdf"
    doc.write_bytes(b"%PDF-1.7")
    text, problem = vision.extract(f"file://{doc}", "url")
    assert seen["path"] == str(doc)
    assert text == "page one"


# --- a page that is not a page -------------------------------------------

class _Resp:
    def __init__(self, ctype, body=b"<p>hi</p>"):
        self.headers = {"Content-Type": ctype}
        self._body = body

    def read(self, n):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/octet-stream"])
def test_a_non_text_content_type_is_refused(monkeypatch, ctype):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(ctype))
    text, problem = vision.extract("https://x/thing", "url")
    assert text == ""
    assert "look at the screen" in problem


@pytest.mark.parametrize("ctype", [
    "text/html; charset=utf-8", "text/plain", "application/json", "application/xhtml+xml", "",
])
def test_a_textual_content_type_is_read(monkeypatch, ctype):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(ctype))
    text, problem = vision.extract("https://x/page", "url")
    assert problem == ""
    assert "hi" in text

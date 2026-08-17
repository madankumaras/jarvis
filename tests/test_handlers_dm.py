"""Worker-side Slack handlers.

Every test injects a fake client. No test may reach the live Slack API — a
sent DM is not recoverable.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))


class _FakeSlack:
    def __init__(self, users=None, fail=None):
        self._users = users if users is not None else []
        self._fail = fail
        self.sent = []

    def search_users(self, query):
        q = (query or "").lower()
        return [
            u for u in self._users
            if q in (u.get("real_name") or "").lower()
            or q in (u.get("display_name") or "").lower()
        ]

    def send_dm(self, user_id, text):
        if self._fail:
            raise RuntimeError(self._fail)
        self.sent.append((user_id, text))
        return "1699999999.0001"


@pytest.fixture
def handlers():
    import handlers as h

    return h


def test_resolve_person_finds_a_single_match(handlers, monkeypatch):
    fake = _FakeSlack([{"id": "U01", "real_name": "Ashok Kumar", "display_name": "ashok"}])
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    out = handlers.resolve_person("ashok")
    assert out["id"] == "U01"
    assert out["name"] == "Ashok Kumar"
    assert out["ambiguous"] == []


def test_resolve_person_reports_ambiguity_rather_than_guessing(handlers, monkeypatch):
    """A DM to the wrong colleague cannot be taken back, so never pick one."""
    fake = _FakeSlack([
        {"id": "U01", "real_name": "Ashok Kumar", "display_name": "ashok"},
        {"id": "U02", "real_name": "Ashok Verma", "display_name": "ashokv"},
    ])
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    out = handlers.resolve_person("ashok")
    assert out["id"] == ""
    assert len(out["ambiguous"]) == 2
    assert {p["name"] for p in out["ambiguous"]} == {"Ashok Kumar", "Ashok Verma"}


def test_resolve_person_unknown_returns_empty(handlers, monkeypatch):
    monkeypatch.setattr(handlers, "_slack", lambda: _FakeSlack([]))
    out = handlers.resolve_person("nobody")
    assert out["id"] == ""
    assert out["ambiguous"] == []


def test_resolve_person_falls_back_to_display_name(handlers, monkeypatch):
    fake = _FakeSlack([{"id": "U09", "real_name": "", "display_name": "ashok"}])
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    assert handlers.resolve_person("ashok")["name"] == "ashok"


def test_send_dm_delivers_and_reports(handlers, monkeypatch):
    fake = _FakeSlack()
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    out = handlers.send_dm("U01", "toggle is still off")
    assert fake.sent == [("U01", "toggle is still off")]
    assert "sent" in out["speech"].lower()


def test_send_dm_surfaces_the_error(handlers, monkeypatch):
    monkeypatch.setattr(handlers, "_slack", lambda: _FakeSlack(fail="not_in_channel"))
    with pytest.raises(RuntimeError):
        handlers.send_dm("U01", "hi")


def test_send_dm_refuses_an_empty_body(handlers, monkeypatch):
    fake = _FakeSlack()
    monkeypatch.setattr(handlers, "_slack", lambda: fake)
    with pytest.raises(ValueError):
        handlers.send_dm("U01", "   ")
    assert fake.sent == []


def test_both_handlers_are_registered(handlers):
    assert "resolve_person" in handlers.HANDLERS
    assert "send_dm" in handlers.HANDLERS

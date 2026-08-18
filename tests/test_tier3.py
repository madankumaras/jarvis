import pytest
import time

from jarvis.tier3 import Tier3Runner


def _wait(pred, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_start_runs_and_calls_back_with_output(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/echo", "hello from claude"])
    done = []
    r.start("create a store", done.append)
    assert _wait(lambda: done), "callback never fired"
    assert "hello" in done[0]


def test_start_returns_immediately(tmp_path):
    """A store-creation run takes minutes. start() must not block the caller,
    because the caller is the thread that owns the microphone."""
    r = Tier3Runner(str(tmp_path), command=["/bin/sleep", "2"])
    t0 = time.monotonic()
    r.start("slow thing", lambda out: None)
    assert time.monotonic() - t0 < 0.5
    assert r.busy is True


def test_second_job_is_refused_while_one_runs(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/sleep", "2"])
    assert r.start("first", lambda out: None) is True
    assert r.start("second", lambda out: None) is False


def test_runner_is_free_again_once_the_job_ends(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/echo", "quick"])
    done = []
    r.start("x", done.append)
    assert _wait(lambda: done)
    assert _wait(lambda: not r.busy)
    assert r.start("y", lambda out: None) is True


def test_failure_output_is_reported_not_swallowed(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/sh", "-c", "echo boom >&2; exit 3"])
    done = []
    r.start("x", done.append)
    assert _wait(lambda: done)
    assert "boom" in done[0]


def test_a_missing_binary_reports_rather_than_raising(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/definitely/not/a/binary"])
    done = []
    r.start("x", done.append)
    assert _wait(lambda: done)
    assert done[0], "expected an error description, got empty output"


def test_callback_exception_does_not_escape(tmp_path):
    """The callback speaks and hits the network; if it throws, the runner must
    still finish cleanly rather than leaving `busy` stuck on."""
    r = Tier3Runner(str(tmp_path), command=["/bin/echo", "hi"])

    def boom(out):
        raise RuntimeError("callback exploded")

    r.start("x", boom)
    assert _wait(lambda: not r.busy)


def test_runs_in_the_given_directory(tmp_path):
    r = Tier3Runner(str(tmp_path), command=["/bin/pwd"])
    done = []
    r.start("x", done.append)
    assert _wait(lambda: done)
    assert str(tmp_path) in done[0]


# --- permissions: `claude -p` cannot prompt, so it must be told upfront ---

def test_the_default_allowlist_is_read_only(monkeypatch):
    monkeypatch.delenv("JARVIS_TIER3_ALLOW", raising=False)
    from jarvis.tier3 import _permission_args

    args = _permission_args()
    assert args[0] == "--allowedTools"
    for writer in ("Edit", "Write", "Bash"):
        assert writer not in args[1], f"{writer} must not be allowed by default"


def test_the_allowlist_can_be_widened(monkeypatch):
    monkeypatch.setenv("JARVIS_TIER3_ALLOW", "Read,Edit,Write")
    from jarvis.tier3 import _permission_args

    assert _permission_args() == ["--allowedTools", "Read,Edit,Write"]


def test_all_bypasses_permissions_entirely(monkeypatch):
    """Opt-in only: a misheard sentence could otherwise modify a repo."""
    monkeypatch.setenv("JARVIS_TIER3_ALLOW", "all")
    from jarvis.tier3 import _permission_args

    assert _permission_args() == ["--dangerously-skip-permissions"]


def test_permission_args_are_passed_to_claude(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_TIER3_ALLOW", raising=False)
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        raise RuntimeError("stop here")

    monkeypatch.setattr("subprocess.run", fake_run)
    r = Tier3Runner(str(tmp_path))
    done = []
    r.start("create a store", done.append)
    assert _wait(lambda: done)
    assert seen["argv"][0] == "claude"
    assert "--allowedTools" in seen["argv"]
    assert seen["argv"][-2] == "-p"
    assert seen["argv"][-1] == "create a store"


# --- an expired session is not an answer ---

def test_an_expired_oauth_session_is_recognised():
    """Seen live: every job returned "Failed to authenticate: OAuth session
    expired", which was then summarised and spoken as though it were an answer
    -- and the mic heard it and started another job."""
    from jarvis.tier3 import looks_like_auth_failure

    assert looks_like_auth_failure(
        "Failed to authenticate: OAuth session expired and could not be refreshed"
    )


@pytest.mark.parametrize("text", [
    "not logged in", "please run /login", "Failed to authenticate",
])
def test_other_sign_in_failures_are_recognised(text):
    from jarvis.tier3 import looks_like_auth_failure

    assert looks_like_auth_failure(text)


@pytest.mark.parametrize("text", [
    "The store is ready with 12 products.",
    "ZI-687 is verified and merged.",
    "",
])
def test_a_real_answer_is_not_mistaken_for_an_auth_failure(text):
    from jarvis.tier3 import looks_like_auth_failure

    assert looks_like_auth_failure(text) is False

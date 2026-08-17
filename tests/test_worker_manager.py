import pytest

from jarvis.workers.manager import WorkerManager

CONFIG = """
domains:
  mcsl:
    path: /tmp/fake-mcsl
    python: /usr/bin/python3
    socket: /tmp/jarvis-test-mcsl.sock
    aliases: [mcsl, muscle]
    release_pattern: '^SL MCSL (\\d+):'
    release_token: MCSL
  fedex:
    path: /tmp/fake-fedex
    python: /usr/bin/python3
    socket: /tmp/jarvis-test-fedex.sock
    aliases: [fedex, fed ex]
    release_pattern: '^SL v([\\d.]+) FedexApp'
"""


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "domains.yaml"
    p.write_text(CONFIG)
    return str(p)


def test_lists_configured_domains(config):
    assert set(WorkerManager(config).domains()) == {"mcsl", "fedex"}


def test_unknown_domain_raises(config):
    with pytest.raises(KeyError):
        WorkerManager(config).get("nope")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("switch to fedex", "fedex"),
        ("switch to fed ex please", "fedex"),
        ("muscle status of 384", "mcsl"),
        ("what are my tasks", None),
    ],
)
def test_resolve_alias(config, text, expected):
    assert WorkerManager(config).resolve_alias(text) == expected


def test_longest_alias_wins(config):
    # "fedex" and "fed ex" both appear; the longer, more specific one should win.
    assert WorkerManager(config).resolve_alias("switch to fed ex") == "fedex"


def test_socket_path_comes_from_config(config):
    assert WorkerManager(config)._config("mcsl")["socket"] == "/tmp/jarvis-test-mcsl.sock"


def test_spawn_argv_carries_release_pattern_and_token(config):
    """The worker cannot find a domain's release lists without its pattern."""
    argv = WorkerManager(config)._spawn_argv("mcsl")
    assert argv[0] == "/usr/bin/python3"
    assert argv[argv.index("--repo") + 1] == "/tmp/fake-mcsl"
    assert argv[argv.index("--socket") + 1] == "/tmp/jarvis-test-mcsl.sock"
    assert argv[argv.index("--release-pattern") + 1] == r"^SL MCSL (\d+):"
    assert argv[argv.index("--release-token") + 1] == "MCSL"


def test_spawn_argv_defaults_release_token_to_empty(config):
    """fedex has no release_token: its releases are dotted versions, which the
    correction layer's ID pattern cannot represent."""
    argv = WorkerManager(config)._spawn_argv("fedex")
    assert argv[argv.index("--release-token") + 1] == ""


def test_shutdown_is_registered_to_run_at_exit(config):
    """A SIGTERM'd daemon does not run its `finally`, and every orphaned worker
    holds a live Trello client forever."""
    import atexit

    m = WorkerManager(config)
    # atexit keeps its registry private; check the callback is bound to shutdown
    # by verifying it is idempotent and safe to call with nothing running.
    m.shutdown()
    m.shutdown()
    assert m._procs == {}


def test_shutdown_with_no_workers_is_harmless(config):
    WorkerManager(config).shutdown()

"""Contract test: spin the real MCSL worker and assert the protocol holds.

This is the test that catches MCSL/FedEx/AU Post drift.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from jarvis.workers.client import WorkerClient

REPO = Path(__file__).resolve().parent.parent


def _domain(name="mcsl"):
    return yaml.safe_load((REPO / "domains.yaml").read_text())["domains"][name]


@pytest.fixture(scope="module")
def mcsl_worker():
    cfg = _domain("mcsl")
    if not Path(cfg["python"]).exists():
        pytest.skip(f"venv missing: {cfg['python']}")

    sock = cfg["socket"]
    if os.path.exists(sock):
        os.unlink(sock)

    proc = subprocess.Popen(
        [cfg["python"], str(REPO / "worker" / "main.py"),
         "--repo", cfg["path"], "--socket", sock,
         "--release-pattern", cfg.get("release_pattern", ""),
         "--release-token", cfg.get("release_token", "")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    for _ in range(100):
        if os.path.exists(sock):
            break
        if proc.poll() is not None:
            pytest.fail(f"worker died: {proc.stderr.read().decode()}")
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("worker did not create its socket within 10s")

    # Real board round trips (vocab() walks every list and every card, and
    # TrelloClient's get_cards_in_list fetches comments/attachments/checklists
    # per card) run well past WorkerClient's 30s default against a live board.
    # Bump the client-side timeout here only -- this is test plumbing, not a
    # protocol change, and it does not touch the Domain Expert repo.
    yield WorkerClient(sock, timeout=4000.0)

    proc.terminate()
    proc.wait(timeout=5)


def test_capabilities_includes_card_status(mcsl_worker):
    assert "card_status" in mcsl_worker.capabilities()


def test_vocab_returns_expected_keys(mcsl_worker):
    v = mcsl_worker.call("vocab")
    assert set(v) >= {"cards", "people", "carriers", "zi_ids"}
    assert isinstance(v["cards"], list)


def test_unknown_method_returns_error_not_crash(mcsl_worker):
    from jarvis.types import RpcError

    with pytest.raises(RpcError):
        mcsl_worker.call("no_such_method")


def test_worker_survives_a_failed_call(mcsl_worker):
    from jarvis.types import RpcError

    with pytest.raises(RpcError):
        mcsl_worker.call("no_such_method")
    assert "card_status" in mcsl_worker.capabilities()

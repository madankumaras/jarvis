"""Spawn, reuse, and reap per-domain workers.

Each Domain Expert repo has its own venv and its own top-level `config.py` and
`pipeline/` package, so two of them cannot share a Python process. Every domain
therefore gets a worker running under its own interpreter, and Jarvis talks to
it over a unix socket.
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import time
from pathlib import Path

import yaml

from jarvis.types import RpcError
from jarvis.workers.client import WorkerClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IDLE_TIMEOUT_SECONDS = 600  # 10 minutes, per the spec
STARTUP_TIMEOUT_SECONDS = 20


class WorkerManager:
    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = config_path or str(REPO_ROOT / "domains.yaml")
        self._cfg = yaml.safe_load(Path(self.config_path).read_text())["domains"]
        self._procs: dict[str, subprocess.Popen] = {}
        self._clients: dict[str, WorkerClient] = {}
        # Workers are children, but a SIGTERM'd daemon does not run its
        # `finally`, and each orphan holds a live Trello client forever. In one
        # session's testing this leaked 20 idle Python processes.
        atexit.register(self.shutdown)
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        """Reap workers on SIGTERM/SIGINT as well as normal exit.

        Best-effort: signal handlers can only be installed from the main
        thread, and a previously installed handler is chained rather than
        replaced.
        """
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                previous = signal.getsignal(sig)

                def handler(signum, frame, _prev=previous):
                    self.shutdown()
                    if callable(_prev):
                        _prev(signum, frame)
                    else:
                        raise SystemExit(128 + signum)

                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass  # not the main thread, or not permitted

    def domains(self) -> list[str]:
        return list(self._cfg)

    def _config(self, domain: str) -> dict:
        if domain not in self._cfg:
            raise KeyError(f"unknown domain: {domain}")
        return self._cfg[domain]

    def resolve_alias(self, text: str) -> str | None:
        """Find a domain named anywhere in the text. Longest alias wins, so
        "fed ex" beats "fedex" rather than depending on dict order."""
        lowered = (text or "").lower()
        best: tuple[int, str] | None = None
        for name, cfg in self._cfg.items():
            for alias in cfg.get("aliases", []):
                if alias in lowered and (best is None or len(alias) > best[0]):
                    best = (len(alias), name)
        return best[1] if best else None

    def _spawn_argv(self, domain: str) -> list[str]:
        """Command line for a domain's worker.

        release_pattern tells the worker which lists are this domain's releases;
        without it the handlers see none. release_token is optional and only set
        where releases are plain integers.
        """
        cfg = self._config(domain)
        return [
            cfg["python"],
            str(REPO_ROOT / "worker" / "main.py"),
            "--repo", cfg["path"],
            "--socket", cfg["socket"],
            "--release-pattern", cfg.get("release_pattern", ""),
            "--release-token", cfg.get("release_token", ""),
        ]

    def get(self, domain: str) -> WorkerClient:
        cfg = self._config(domain)
        proc = self._procs.get(domain)
        if proc is not None and proc.poll() is None:
            return self._clients[domain]
        return self._spawn(domain, cfg)

    def _spawn(self, domain: str, cfg: dict) -> WorkerClient:
        sock = cfg["socket"]
        if os.path.exists(sock):
            os.unlink(sock)

        proc = subprocess.Popen(
            self._spawn_argv(domain),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        deadline = time.time() + STARTUP_TIMEOUT_SECONDS
        while time.time() < deadline:
            if os.path.exists(sock):
                break
            if proc.poll() is not None:
                err = proc.stderr.read().decode() if proc.stderr else ""
                raise RpcError(f"{domain} worker died on startup: {err[-500:]}")
            time.sleep(0.1)
        else:
            proc.kill()
            raise RpcError(f"{domain} worker did not start within {STARTUP_TIMEOUT_SECONDS}s")

        self._procs[domain] = proc
        self._clients[domain] = WorkerClient(sock)
        return self._clients[domain]

    def shutdown(self) -> None:
        for proc in self._procs.values():
            proc.terminate()
        for proc in self._procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._procs.clear()
        self._clients.clear()

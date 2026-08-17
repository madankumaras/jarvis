"""Socket server. Launched with a Domain Expert's own python, inside its own repo."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import traceback


def _bootstrap(repo: str) -> None:
    """Put the target repo on sys.path and load its .env before importing handlers."""
    sys.path.insert(0, repo)
    os.chdir(repo)
    env = os.path.join(repo, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _dispatch(handlers, method: str, params: dict) -> dict:
    if method == "capabilities":
        return {"methods": sorted(handlers)}
    if method not in handlers:
        raise ValueError(f"unknown method: {method}")
    return handlers[method](**params)


def serve(repo: str, sock_path: str, release_pattern: str = "", release_token: str = "") -> None:
    _bootstrap(repo)
    import handlers  # noqa: E402  (must follow _bootstrap)

    # Domain-specific config from domains.yaml. Handlers read these to decide
    # which lists are this domain's releases; without a pattern they see none.
    handlers.RELEASE_PATTERN = release_pattern
    handlers.RELEASE_TOKEN = release_token
    HANDLERS = handlers.HANDLERS

    if os.path.exists(sock_path):
        os.unlink(sock_path)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(8)
    print(f"jarvis worker ready: {repo} -> {sock_path}", flush=True)

    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                try:
                    buf = b""
                    while not buf.endswith(b"\n"):
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    if not buf:
                        continue
                    req = json.loads(buf.decode())
                    result = _dispatch(HANDLERS, req.get("method", ""), req.get("params", {}))
                    reply = {"id": req.get("id"), "ok": True, "result": result}
                except Exception as exc:  # a bad call must never kill the worker
                    traceback.print_exc()
                    reply = {"id": locals().get("req", {}).get("id"), "ok": False, "error": str(exc)}
                conn.sendall((json.dumps(reply) + "\n").encode())
    finally:
        if os.path.exists(sock_path):
            os.unlink(sock_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--release-pattern", default="")
    parser.add_argument("--release-token", default="")
    args = parser.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    serve(args.repo, args.socket, args.release_pattern, args.release_token)

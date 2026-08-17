"""Newline-delimited JSON-RPC over a unix socket."""
from __future__ import annotations

import json
import socket
import uuid
from typing import Any

from jarvis.types import RpcError

DEFAULT_TIMEOUT = 30.0


class WorkerClient:
    """One client per domain. Connects per call; the *worker* is what stays warm."""

    def __init__(self, socket_path: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, method: str, **params: Any) -> dict:
        request = {"id": str(uuid.uuid4()), "method": method, "params": params}
        try:
            encoded = (json.dumps(request) + "\n").encode()
        except Exception as exc:
            raise RpcError(f"cannot serialize params for {method}: {exc}") from exc

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                sock.sendall(encoded)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise RpcError(f"worker closed connection during {method}")
                    buf += chunk
        except OSError as exc:
            raise RpcError(f"cannot reach worker at {self.socket_path}: {exc}") from exc

        try:
            payload = json.loads(buf.decode())
        except ValueError as exc:
            raise RpcError(f"malformed response from worker during {method}: {exc}") from exc

        if not isinstance(payload, dict):
            raise RpcError(
                f"worker returned {type(payload).__name__}, expected object, during {method}"
            )

        if not payload.get("ok"):
            raise RpcError(payload.get("error", "unknown worker error"))

        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise RpcError(
                f"worker returned {type(result).__name__} result, expected object, during {method}"
            )
        return result

    def capabilities(self) -> list[str]:
        methods = self.call("capabilities").get("methods", [])
        if not isinstance(methods, list):
            raise RpcError(f"worker returned {type(methods).__name__} methods, expected list")
        return methods

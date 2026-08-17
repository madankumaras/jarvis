import json
import socket
import threading

import pytest

from jarvis.types import RpcError
from jarvis.workers.client import WorkerClient


def _fake_server(sock_path, responder):
    """Serve exactly one connection, then stop."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        with conn:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            req = json.loads(buf.decode())
            conn.sendall((json.dumps(responder(req)) + "\n").encode())
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _fake_server_raw(sock_path, raw_response):
    """Serve exactly one connection, replying with raw bytes (not JSON-encoded)."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        with conn:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            conn.sendall(raw_response)
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_call_returns_result(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": True, "result": {"speech": "hi"}})

    client = WorkerClient(sock_path)
    assert client.call("card_status", card_id="MCSL-384") == {"speech": "hi"}


def test_call_sends_method_and_params(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    seen = {}

    def responder(req):
        seen.update(req)
        return {"id": req["id"], "ok": True, "result": {}}

    _fake_server(sock_path, responder)
    WorkerClient(sock_path).call("card_status", card_id="MCSL-384")

    assert seen["method"] == "card_status"
    assert seen["params"] == {"card_id": "MCSL-384"}


def test_error_response_raises(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": False, "error": "card not found"})

    with pytest.raises(RpcError, match="card not found"):
        WorkerClient(sock_path).call("card_status", card_id="NOPE-1")


def test_missing_socket_raises(short_tmp_path):
    with pytest.raises(RpcError):
        WorkerClient(str(short_tmp_path / "absent.sock")).call("card_status")


def test_capabilities_returns_list(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server(
        sock_path,
        lambda req: {"id": req["id"], "ok": True, "result": {"methods": ["card_status"]}},
    )
    assert WorkerClient(sock_path).capabilities() == ["card_status"]


def test_malformed_json_response_raises_rpc_error(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server_raw(sock_path, b"this is not json\n")

    with pytest.raises(RpcError, match="malformed"):
        WorkerClient(sock_path).call("card_status", card_id="MCSL-384")


def test_non_utf8_response_raises_rpc_error(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server_raw(sock_path, b"\xff\xfe\n")

    with pytest.raises(RpcError):
        WorkerClient(sock_path).call("card_status", card_id="MCSL-384")


@pytest.mark.parametrize("body", [b"[]\n", b'"x"\n', b"42\n", b"null\n"])
def test_non_object_json_response_raises_rpc_error(short_tmp_path, body):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server_raw(sock_path, body)

    with pytest.raises(RpcError):
        WorkerClient(sock_path).call("card_status", card_id="MCSL-384")


@pytest.mark.parametrize("bad_result", ["not-a-dict", [], 42])
def test_non_dict_result_raises_rpc_error(short_tmp_path, bad_result):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": True, "result": bad_result})

    with pytest.raises(RpcError):
        WorkerClient(sock_path).call("card_status", card_id="MCSL-384")


def test_capabilities_with_non_dict_result_raises_rpc_error_not_attribute_error(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": True, "result": "not-a-dict"})

    with pytest.raises(RpcError):
        WorkerClient(sock_path).capabilities()


def test_unserializable_params_raise_rpc_error(short_tmp_path):
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": True, "result": {}})

    with pytest.raises(RpcError, match="serialize"):
        WorkerClient(sock_path).call("x", weird=object())


def test_deeply_nested_params_raise_rpc_error():
    """Deeply nested params that exceed recursion limit raise RpcError, not RecursionError."""
    deep = current = {}
    for _ in range(200000):
        current["n"] = {}
        current = current["n"]

    with pytest.raises(RpcError, match="serialize"):
        WorkerClient("/nonexistent.sock").call("x", deep=deep)


def test_capabilities_with_non_list_methods_raises_rpc_error(short_tmp_path):
    """capabilities() raises RpcError when methods is not a list."""
    sock_path = str(short_tmp_path / "t.sock")
    _fake_server(sock_path, lambda req: {"id": req["id"], "ok": True, "result": {"methods": "oops"}})

    with pytest.raises(RpcError, match="methods"):
        WorkerClient(sock_path).capabilities()

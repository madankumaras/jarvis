"""Serve the dashboard and stream events to it.

Runs on a daemon thread inside the Jarvis process. Deliberately stdlib-only and
deliberately bound to localhost: this exposes the transcript of everything you
say, which has no business being reachable from the network.
"""
from __future__ import annotations

import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty

from jarvis.dash.bus import BUS, Bus, heartbeat

HOST = "127.0.0.1"
PORT = int(os.environ.get("JARVIS_DASH_PORT", "8777"))
PAGE = Path(__file__).resolve().parent / "index.html"


def _handler(bus: Bus):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass  # the voice log is the interesting one

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/events"):
                return self._stream()
            if self.path in ("/", "/index.html"):
                return self._page()
            self.send_error(404)

        def _page(self) -> None:
            try:
                body = PAGE.read_bytes()
            except OSError:
                self.send_error(500, "dashboard page missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _stream(self) -> None:
            q = bus.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    try:
                        event = q.get(timeout=20)
                    except Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self.wfile.write(event.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # tab closed; entirely normal
            finally:
                bus.unsubscribe(q)

    return Handler


class Dashboard:
    """Owns the HTTP server thread and the heartbeat."""

    def __init__(self, bus: Bus | None = None, port: int = PORT) -> None:
        self.bus = bus or BUS
        self.port = port
        self._srv: ThreadingHTTPServer | None = None
        self._stop = threading.Event()
        self._opened = False

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    def start(self) -> bool:
        """Returns False if the port is taken -- never fatal. A dashboard that
        will not start must not stop Jarvis from listening."""
        try:
            self._srv = ThreadingHTTPServer((HOST, self.port), _handler(self.bus))
        except OSError:
            return False
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        threading.Thread(target=heartbeat, args=(self.bus, self._stop), daemon=True).start()
        return True

    def open_once(self) -> None:
        """Open the browser the first time only.

        A window that steals focus on every wake gets old fast; after this it
        stays where you put it and simply updates.
        """
        if self._opened:
            return
        self._opened = True
        try:
            webbrowser.open(self.url)
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

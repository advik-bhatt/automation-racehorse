"""`racehorse serve`: an HTTP + Server-Sent-Events server for the dashboard.

Zero dependencies (stdlib http.server): local-first, no build step, no npm.

Three endpoints:
  GET /api/state   -> full current snapshot (JSON), for the page's first load
  GET /api/events  -> text/event-stream, one `data:` line per new event as
                      it's appended to events.jsonl, plus a periodic full
                      `event: state` snapshot so late joiners self-correct
  GET /, /app.js, /style.css -> the static dashboard
"""
from __future__ import annotations

import json
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import store

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

SSE_HEARTBEAT_SECONDS = 5
POLL_INTERVAL_SECONDS = 0.3


class EventBroker:
    """Fans out newly-appended events to every connected SSE client."""

    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            q.put(event)


broker = EventBroker()


def _tail_loop(stop_event: threading.Event) -> None:
    path = store.events_path()
    pos = path.stat().st_size if path.exists() else 0
    while not stop_event.is_set():
        try:
            size = path.stat().st_size if path.exists() else 0
            if size < pos:
                pos = 0  # log was replaced/truncated; restart from the top
            if size > pos:
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            broker.publish(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                    pos = f.tell()
        except FileNotFoundError:
            pos = 0
        stop_event.wait(POLL_INTERVAL_SECONDS)


class Handler(BaseHTTPRequestHandler):
    server_version = "racehorse/0.1"

    def log_message(self, fmt: str, *args) -> None:  # quiet by default
        pass

    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        if self.path in STATIC_FILES:
            self._serve_static(*STATIC_FILES[self.path])
        elif self.path == "/api/state":
            self._serve_state()
        elif self.path == "/api/events":
            self._serve_sse()
        else:
            self.send_error(404, "not found")

    def _serve_static(self, filename: str, content_type: str) -> None:
        file_path = DASHBOARD_DIR / filename
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_state(self) -> None:
        body = json.dumps(store.build_snapshot()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = broker.subscribe()
        last_heartbeat = 0.0
        try:
            while True:
                now = time.time()
                if now - last_heartbeat > SSE_HEARTBEAT_SECONDS:
                    snapshot = json.dumps(store.build_snapshot())
                    self.wfile.write(f"event: state\ndata: {snapshot}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_heartbeat = now
                try:
                    event = q.get(timeout=1.0)
                except queue.Empty:
                    continue
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            broker.unsubscribe(q)


def serve(port: int = 8790, no_open: bool = False, bind: str = "127.0.0.1") -> None:
    store.racehorse_dir()  # ensure it exists before we start tailing it
    stop_event = threading.Event()
    tail_thread = threading.Thread(target=_tail_loop, args=(stop_event,), daemon=True)
    tail_thread.start()

    httpd = ThreadingHTTPServer((bind, port), Handler)
    url = f"http://{bind}:{port}"
    print(f"racehorse: serving the dashboard at {url}")
    print(f"racehorse: watching {store.events_path()}")
    if not no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        httpd.shutdown()

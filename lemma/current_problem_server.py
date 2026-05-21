"""Tiny HTTP surface for the public active-problem snapshot."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import urlsplit

from lemma.common.config import LemmaSettings
from lemma.current_problems import CurrentProblemsSnapshot, build_current_problems_snapshot

SnapshotBuilder = Callable[..., CurrentProblemsSnapshot]


class CurrentProblemService:
    def __init__(
        self,
        settings: LemmaSettings,
        *,
        tempo: int | None = None,
        snapshot_builder: SnapshotBuilder = build_current_problems_snapshot,
        cache_seconds: float = 300.0,
    ) -> None:
        self.settings = settings
        self.tempo = tempo
        self.snapshot_builder = snapshot_builder
        self.cache_seconds = cache_seconds
        self._cache: tuple[float, bytes] | None = None
        self._lock = Lock()

    def response(self, raw_path: str) -> tuple[int, bytes]:
        path = urlsplit(raw_path).path
        if path == "/healthz":
            return HTTPStatus.OK, b'{"ok":true}\n'
        if path in {"/", "/current-problems.json"}:
            try:
                return HTTPStatus.OK, self._snapshot_body()
            except Exception:
                return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"problem feed unavailable"}\n'
        return HTTPStatus.NOT_FOUND, b'{"error":"not found"}\n'

    def _snapshot_body(self) -> bytes:
        now = time.monotonic()
        if self._cache and self._cache[0] > now:
            return self._cache[1]
        with self._lock:
            now = time.monotonic()
            if self._cache and self._cache[0] > now:
                return self._cache[1]
            snapshot = self.snapshot_builder(self.settings, tempo=self.tempo)
            payload = snapshot.model_dump(mode="json", exclude_none=True)
            body = (json.dumps(payload, sort_keys=True) + "\n").encode()
            self._cache = (now + self.cache_seconds, body)
            return body


def make_handler(service: CurrentProblemService) -> type[BaseHTTPRequestHandler]:
    class CurrentProblemHandler(BaseHTTPRequestHandler):
        server_version = "LemmaCurrentProblems/1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._headers(b"")
            self.end_headers()

        def do_HEAD(self) -> None:
            self._send(*service.response(self.path), write_body=False)

        def do_GET(self) -> None:
            self._send(*service.response(self.path), write_body=True)

        def _send(self, status: int, body: bytes, *, write_body: bool) -> None:
            self.send_response(status)
            self._headers(body)
            self.end_headers()
            if write_body:
                self.wfile.write(body)

        def _headers(self, body: bytes) -> None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))

    return CurrentProblemHandler


def run_server(host: str, port: int, service: CurrentProblemService) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    try:
        server.serve_forever()
    finally:
        server.server_close()

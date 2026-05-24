"""Tiny HTTP surface for the public active-problem snapshot."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from lemma.common.config import LemmaSettings
from lemma.current_problems import CurrentProblemsSnapshot, build_current_problems_snapshot

SnapshotBuilder = Callable[..., CurrentProblemsSnapshot]


@dataclass(frozen=True)
class _CachedResponse:
    expires_at: float
    status: int
    body: bytes


@dataclass(frozen=True)
class _CachedFile:
    mtime_ns: int
    size: int
    body: bytes


class CurrentProblemService:
    def __init__(
        self,
        settings: LemmaSettings,
        *,
        tempo: int | None = None,
        snapshot_path: Path | None = None,
        snapshot_builder: SnapshotBuilder = build_current_problems_snapshot,
        cache_ttl_s: float = 600.0,
    ) -> None:
        self.settings = settings
        self.tempo = tempo
        self.snapshot_path = snapshot_path
        self.snapshot_builder = snapshot_builder
        self.cache_ttl_s = max(0.0, cache_ttl_s)
        self._cached_response: _CachedResponse | None = None
        self._cached_file: _CachedFile | None = None

    def response(self, raw_path: str) -> tuple[int, bytes]:
        path = urlsplit(raw_path).path
        if path == "/healthz":
            return HTTPStatus.OK, b'{"ok":true}\n'
        if path in {"/", "/current-problems.json"}:
            if self.snapshot_path is not None:
                return self._file_response()
            now = monotonic()
            if self._cached_response is not None and self._cached_response.expires_at > now:
                return self._cached_response.status, self._cached_response.body
            try:
                snapshot = self.snapshot_builder(self.settings, tempo=self.tempo)
            except Exception:
                if self._cached_response is not None:
                    return self._cached_response.status, self._cached_response.body
                return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"problem feed unavailable"}\n'
            payload = snapshot.model_dump(mode="json", exclude_none=True)
            body = (json.dumps(payload, sort_keys=True) + "\n").encode()
            self._cached_response = _CachedResponse(now + self.cache_ttl_s, HTTPStatus.OK, body)
            return HTTPStatus.OK, body
        return HTTPStatus.NOT_FOUND, b'{"error":"not found"}\n'

    def _file_response(self) -> tuple[int, bytes]:
        try:
            stat = self.snapshot_path.stat() if self.snapshot_path is not None else None
            if (
                stat is not None
                and self._cached_file is not None
                and self._cached_file.mtime_ns == stat.st_mtime_ns
                and self._cached_file.size == stat.st_size
            ):
                return HTTPStatus.OK, self._cached_file.body
            if self.snapshot_path is None:
                raise FileNotFoundError
            body = self.snapshot_path.read_bytes()
            self._cached_file = _CachedFile(stat.st_mtime_ns, stat.st_size, body)
            return HTTPStatus.OK, body
        except Exception:
            if self._cached_file is not None:
                return HTTPStatus.OK, self._cached_file.body
            return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"problem feed unavailable"}\n'


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

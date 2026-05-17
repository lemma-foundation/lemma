"""Dedicated Lean verify worker (HTTP). Pair with ``LEMMA_LEAN_VERIFY_REMOTE_URL``."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any

from loguru import logger

from lemma.common.config import LemmaSettings
from lemma.lean.problem_codec import problem_from_payload
from lemma.lean.verify_runner import lean_sandbox_from_settings


def _bind_host_is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    if h == "localhost":
        return True
    try:
        return ip_address(h.strip("[]")).is_loopback
    except ValueError:
        return False


def lean_worker_bind_error(host: str, settings: LemmaSettings) -> str | None:
    if (settings.lean_verify_remote_bearer or "").strip():
        return None
    if settings.lean_worker_allow_unauthenticated_non_loopback:
        return None
    if _bind_host_is_loopback(host):
        return None
    return (
        "lemma worker --serve refuses unauthenticated non-loopback binds. "
        "Set LEMMA_LEAN_VERIFY_REMOTE_BEARER, bind to localhost, or set "
        "LEMMA_LEAN_WORKER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=1 for explicit dev-only exposure."
    )


class _VerifyHandler(BaseHTTPRequestHandler):
    """POST ``/verify`` — JSON body; optional Bearer auth from worker ``LemmaSettings``."""

    server_version = "LemmaLeanWorker/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("worker_http {} {}", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth_ok(self, settings: LemmaSettings) -> bool:
        expected = (settings.lean_verify_remote_bearer or "").strip()
        if not expected:
            return True
        auth = self.headers.get("Authorization") or ""
        return auth.strip() == f"Bearer {expected}"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0].rstrip("/") == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0].rstrip("/") != "/verify":
            self._send_json(404, {"detail": "not found"})
            return

        settings = LemmaSettings()
        if not self._auth_ok(settings):
            self._send_json(401, {"detail": "unauthorized"})
            return

        length_raw = self.headers.get("Content-Length")
        try:
            length = int(length_raw or "0")
        except ValueError:
            self._send_json(400, {"detail": "bad Content-Length"})
            return
        if length <= 0 or length > 64 * 1024 * 1024:
            self._send_json(400, {"detail": "body too large or empty"})
            return

        try:
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"detail": "invalid JSON"})
            return

        if not isinstance(body, dict):
            self._send_json(400, {"detail": "JSON object expected"})
            return

        try:
            prob = problem_from_payload(body["problem"])
            proof_script = str(body["proof_script"])
        except (KeyError, TypeError, ValueError) as e:
            self._send_json(400, {"detail": str(e)})
            return

        timeout_s = body.get("verify_timeout_s")
        if timeout_s is not None:
            try:
                vt = max(1, int(timeout_s))
            except (TypeError, ValueError):
                vt = settings.lean_verify_timeout_s
        else:
            vt = settings.lean_verify_timeout_s

        sandbox = lean_sandbox_from_settings(settings, vt)
        policy = str(body.get("submission_policy") or "")
        vr = sandbox.verify(prob, proof_script, submission_policy=policy or None)
        self._send_json(200, vr.model_dump())


def serve_forever(host: str, port: int, settings: LemmaSettings | None = None) -> None:
    """Run ``ThreadingHTTPServer`` until Ctrl+C."""
    settings = settings or LemmaSettings()
    err = lean_worker_bind_error(host, settings)
    if err:
        raise ValueError(err)
    httpd = ThreadingHTTPServer((host, port), _VerifyHandler)
    logger.info("lemma worker --serve listening on http://{}:{}/verify (POST)", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("lean-worker stopped")
    finally:
        httpd.server_close()

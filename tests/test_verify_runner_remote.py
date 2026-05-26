"""Remote Lean verify HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest
from lemma.common.config import LemmaSettings
from lemma.lean.verify_runner import run_lean_verify
from lemma.problems.base import Problem


@pytest.fixture
def tiny_problem() -> Problem:
    return Problem(
        id="test-id",
        theorem_name="p",
        type_expr="True",
        split="easy",
        lean_toolchain="leanprover/lean4:v4.22.0",
        mathlib_rev="abc",
        imports=("Mathlib",),
        extra={},
    )


def test_remote_verify_http_success(monkeypatch: pytest.MonkeyPatch, tiny_problem: Problem) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["verify_timeout_s"] == 120
        assert body["problem"]["id"] == "test-id"
        assert "proof_script" in body
        assert body["submission_policy"] == "strict_envelope"
        return httpx.Response(
            200,
            json={
                "passed": True,
                "reason": "ok",
                "stderr_tail": "",
                "stdout_tail": "",
                "build_seconds": 1.5,
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def _client(**kwargs: object) -> httpx.Client:
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("lemma.lean.verify_runner.httpx.Client", _client)

    s = LemmaSettings().model_copy(
        update={
            "lean_verify_remote_url": "http://localhost:8787",
            "lean_verify_timeout_s": 120,
        },
    )
    proof = "import Mathlib\n\nnamespace Submission\n\ntheorem p : True := by\n  trivial\n\nend Submission\n"
    vr = run_lean_verify(s, verify_timeout_s=120, problem=tiny_problem, proof_script=proof)
    assert vr.passed is True
    assert vr.reason == "ok"


def test_remote_verify_transport_error(monkeypatch: pytest.MonkeyPatch, tiny_problem: Problem) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
    real_client = httpx.Client

    def _client(**kwargs: object) -> httpx.Client:
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("lemma.lean.verify_runner.httpx.Client", _client)

    s = LemmaSettings().model_copy(update={"lean_verify_remote_url": "http://worker.invalid"})
    proof = "import Mathlib\n\nnamespace Submission\n\ntheorem p : True := by\n  trivial\n\nend Submission\n"
    vr = run_lean_verify(s, verify_timeout_s=60, problem=tiny_problem, proof_script=proof)
    assert vr.passed is False
    assert vr.reason == "remote_error"


def test_remote_verify_policy_scan_happens_before_http(
    monkeypatch: pytest.MonkeyPatch,
    tiny_problem: Problem,
) -> None:
    def _client(**kwargs: object) -> httpx.Client:  # noqa: ARG001
        raise AssertionError("remote HTTP should not run for rejected proof text")

    monkeypatch.setattr("lemma.lean.verify_runner.httpx.Client", _client)

    s = LemmaSettings().model_copy(update={"lean_verify_remote_url": "http://localhost:8787"})
    vr = run_lean_verify(s, verify_timeout_s=60, problem=tiny_problem, proof_script="theorem p : True := by sorry")
    assert vr.passed is False
    assert vr.reason == "policy_violation"


def test_remote_verify_skipped_when_url_unset(tiny_problem: Problem, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without remote URL, run_lean_verify uses LeanSandbox — stub verify to avoid lake."""

    def fake_verify(self: object, problem: Problem, submission_src: str, **kwargs: object):  # noqa: ARG001
        from lemma.lean.sandbox import VerifyResult

        return VerifyResult(passed=True, reason="ok", build_seconds=0.1)

    monkeypatch.setattr("lemma.lean.verify_runner.LeanSandbox.verify", fake_verify)

    s = LemmaSettings().model_copy(
        update={
            "lean_verify_remote_url": None,
            "lean_use_docker": False,
        },
    )
    proof = "import Mathlib\n\nnamespace Submission\n\ntheorem p : True := by\n  trivial\n\nend Submission\n"
    vr = run_lean_verify(s, verify_timeout_s=300, problem=tiny_problem, proof_script=proof)
    assert vr.passed is True


def test_local_verify_policy_scan_happens_before_sandbox(
    tiny_problem: Problem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_verify(self: object, problem: Problem, submission_src: str, **kwargs: object):  # noqa: ARG001
        raise AssertionError("sandbox should not run for a policy violation")

    monkeypatch.setattr("lemma.lean.verify_runner.LeanSandbox.verify", fake_verify)

    s = LemmaSettings().model_copy(
        update={
            "lean_verify_remote_url": None,
            "lean_use_docker": False,
        },
    )
    vr = run_lean_verify(s, verify_timeout_s=300, problem=tiny_problem, proof_script="theorem p : True := by sorry")
    assert vr.passed is False
    assert vr.reason == "policy_violation"

"""Task verification through the existing Lean runner."""

from __future__ import annotations

import pytest
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.tasks import LemmaTask


def _submission_stub() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem test_true : True := by",
            "  sorry",
            "",
            "end Submission",
            "",
        ]
    )


def _proof(theorem_type: str = "True", body: str = "  trivial", imports: tuple[str, ...] = ("Mathlib",)) -> str:
    return "\n".join(
        [
            *(f"import {name}" for name in imports),
            "",
            "namespace Submission",
            "",
            f"theorem test_true : {theorem_type} := by",
            body,
            "",
            "end Submission",
            "",
        ]
    )


def _task() -> LemmaTask:
    return LemmaTask(
        id="lemma.test.true",
        task_version=1,
        title="True task",
        source_stream="human_curated",
        source_ref={"kind": "unit_test", "name": "pytest"},
        source_license="CC-BY-4.0",
        imports=("Mathlib",),
        theorem_name="test_true",
        type_expr="True",
        statement="theorem test_true : True := by\n  sorry",
        submission_stub=_submission_stub(),
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        policy="restricted_helpers",
    )


def test_verification_accepts_known_good_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_verify(self: object, problem: object, submission_src: str, **kwargs: object) -> VerifyResult:
        assert problem.id == "lemma.test.true"
        assert "trivial" in submission_src
        assert kwargs["submission_policy"] == "restricted_helpers"
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.LeanSandbox.verify", fake_verify)

    result = run_lean_verify(
        LemmaSettings(_env_file=None, lean_use_docker=False),
        verify_timeout_s=60,
        problem=_task().to_problem(),
        proof_script=_proof(),
        submission_policy=_task().policy,
    )

    assert result.passed is True


@pytest.mark.parametrize(
    "proof",
    [
        _proof(body="  sorry"),
        _proof(imports=("Mathlib", "Other")),
        _proof(body="axiom bad : False\n\ntheorem test_true : True := by\n  trivial"),
        _proof(theorem_type="False"),
    ],
)
def test_verification_rejects_policy_violations(monkeypatch: pytest.MonkeyPatch, proof: str) -> None:
    def fake_verify(self: object, problem: object, submission_src: str, **kwargs: object) -> VerifyResult:  # noqa: ARG001
        raise AssertionError("sandbox should not run for policy violations")

    monkeypatch.setattr("lemma.lean.verify_runner.LeanSandbox.verify", fake_verify)

    result = run_lean_verify(
        LemmaSettings(_env_file=None, lean_use_docker=False),
        verify_timeout_s=60,
        problem=_task().to_problem(),
        proof_script=proof,
        submission_policy=_task().policy,
    )

    assert result.passed is False
    assert result.reason == "policy_violation"

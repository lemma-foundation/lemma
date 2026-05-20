"""Submission.lean allowlist policy."""

from __future__ import annotations

import pytest
from lemma.lean.submission_policy import scan_submission_policy, submission_axiom_check_names
from lemma.problems.base import Problem


def _problem(split: str = "easy") -> Problem:
    return Problem(
        id=f"test/{split}",
        theorem_name="target",
        type_expr="True",
        split=split,
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        imports=("Mathlib",),
    )


def _src(*body: str, imports: tuple[str, ...] = ("Mathlib",), namespace: str = "Submission") -> str:
    return "\n".join(
        [
            *(f"import {name}" for name in imports),
            "",
            f"namespace {namespace}",
            "",
            *body,
            "",
            f"end {namespace}",
            "",
        ]
    )


def _target(type_expr: str = "True") -> str:
    return f"theorem target : {type_expr} := by\n  trivial"


def _strict_source() -> str:
    return _src(_target())


def _helper_source() -> str:
    return _src(
        "def helperNat : Nat := 2",
        "lemma helperTrue : True := by\n  trivial",
        "theorem target : True := by\n  exact helperTrue",
    )


def _assert_rejected(src: str, *, policy: str = "strict_envelope") -> None:
    problem = _problem("training" if policy == "restricted_helpers" else "easy")
    scan = scan_submission_policy(problem, src, policy=policy)
    assert not scan.ok


def test_strict_envelope_accepts_exact_submission() -> None:
    scan = scan_submission_policy(_problem(), _strict_source(), policy="strict_envelope")

    assert scan.ok, scan.reason


@pytest.mark.parametrize(
    "src",
    [
        _src(_target(), imports=("Mathlib", "Other")),
        _src("lemma helper : True := by\n  trivial", _target()),
        _src(_target("False")),
        _src(_target(), namespace="Other"),
        _src("axiom bad : False", _target()),
        _src("constant bad : False", _target()),
        _src("unsafe def bad : Nat := 0", _target()),
        _src("extern bad : Nat", _target()),
        _src("@[implemented_by bad]\ndef x : Nat := 0", _target()),
        _src("set_option debug.skipKernelTC true", _target()),
        _src("@[simp] theorem target : True := by\n  trivial"),
        _src('macro "boom" : term => `(True)', _target()),
        _src('syntax "boom" : term', _target()),
        _src('elab "boom" : term => pure ()', _target()),
        _src('notation "boom" => True', _target()),
        _src('run_cmd IO.println "boom"', _target()),
    ],
)
def test_strict_envelope_rejects_non_allowlisted_shapes(src: str) -> None:
    _assert_rejected(src)


def test_restricted_helpers_accepts_helper_declarations() -> None:
    problem = _problem("training")
    scan = scan_submission_policy(problem, _helper_source(), policy="restricted_helpers")

    assert scan.ok, scan.reason
    assert submission_axiom_check_names(problem, _helper_source(), policy="restricted_helpers") == [
        "helperTrue",
        "target",
    ]


def test_restricted_helpers_tracks_dotted_theorem_names() -> None:
    problem = Problem(
        id="test/dotted",
        theorem_name="Associated.neg_neg",
        type_expr="True",
        split="training",
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        imports=("Mathlib.Algebra.Ring.Associated",),
    )
    source = _src(
        "theorem Associated.neg_neg : True := by\n  trivial",
        imports=("Mathlib.Algebra.Ring.Associated",),
    )

    scan = scan_submission_policy(problem, source, policy="restricted_helpers")

    assert scan.ok, scan.reason
    assert submission_axiom_check_names(problem, source, policy="restricted_helpers") == ["Associated.neg_neg"]


@pytest.mark.parametrize(
    "src",
    [
        _src(_target(), imports=("Mathlib", "Other")),
        _src("@[simp] lemma helper : True := by\n  trivial", _target()),
        _src('macro "boom" : term => `(True)', _target()),
        _src('notation "boom" => True', _target()),
        _src(_target("False")),
        _src("axiom bad : False", _target()),
    ],
)
def test_restricted_helpers_rejects_dangerous_or_changed_shapes(src: str) -> None:
    _assert_rejected(src, policy="restricted_helpers")

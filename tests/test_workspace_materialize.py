"""Lake workspace materialize + template cache key."""

from pathlib import Path

from lemma.lean.workspace import (
    materialize_workspace,
    workspace_template_cache_key,
    workspace_verify_cache_key,
)
from lemma.problems.base import Problem


def _minimal_problem() -> Problem:
    return Problem(
        id="gen/test_k",
        theorem_name="t_test",
        type_expr="True",
        split="easy",
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        imports=("Mathlib",),
    )


def test_workspace_template_cache_key_stable() -> None:
    p = _minimal_problem()
    assert workspace_template_cache_key(p) == workspace_template_cache_key(p)


def test_workspace_verify_cache_key_matches_template_when_no_fingerprint() -> None:
    p = _minimal_problem()
    assert workspace_verify_cache_key(p, "namespace Submission\n", include_submission_fingerprint=False) == (
        workspace_template_cache_key(p)
    )


def test_workspace_verify_cache_key_splits_on_proof_when_enabled() -> None:
    p = _minimal_problem()
    a = workspace_verify_cache_key(p, "a", include_submission_fingerprint=True)
    b = workspace_verify_cache_key(p, "b", include_submission_fingerprint=True)
    assert a != b
    assert a.startswith(workspace_template_cache_key(p))
    assert "_" in a


def test_materialize_preserve_lake_keeps_dot_lake(tmp_path: Path) -> None:
    p = _minimal_problem()
    dest = tmp_path / "work"
    dest.mkdir()
    (dest / ".lake").mkdir()
    (dest / ".lake" / "warm_marker").write_text("ok", encoding="utf-8")
    materialize_workspace(dest, p, "namespace Submission\n", preserve_lake=True)
    assert (dest / ".lake" / "warm_marker").read_text() == "ok"
    assert (dest / "Submission.lean").read_text().startswith("namespace Submission")


def test_materialize_records_requested_lean_build_target(tmp_path: Path) -> None:
    p = _minimal_problem()
    p = Problem(**{**p.__dict__, "extra": {"lean_build_target": "Challenge"}})
    dest = tmp_path / "work"

    materialize_workspace(dest, p, p.submission_stub())

    assert (dest / ".lemma_build_target").read_text(encoding="utf-8").strip() == "Challenge"


def test_materialize_adds_extra_challenge_fingerprint_names(tmp_path: Path) -> None:
    p = _minimal_problem()
    p = Problem(
        **{
            **p.__dict__,
            "extra": {
                "lean_build_target": "Challenge",
                "lean_fingerprint_names": ("LemmaProceduralGate.prop_gate",),
            },
        }
    )
    dest = tmp_path / "work"

    materialize_workspace(dest, p, p.submission_stub())

    axiom_check = (dest / "AxiomCheck.lean").read_text(encoding="utf-8")
    assert "import Challenge" in axiom_check
    assert "#print LemmaProceduralGate.prop_gate" in axiom_check
    assert "#print axioms LemmaProceduralGate.prop_gate" not in axiom_check

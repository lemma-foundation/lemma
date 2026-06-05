"""Accept/reject and validator-parity tests for the shared preflight verdict."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.preflight import preflight_submission
from lemma.submissions import build_patch_submission
from lemma.tasks import LemmaTask, TaskRegistry, target_type_sha256
from lemma.validator import validate_once

FIXTURE_ROOT = Path("tests/fixtures/lean_patch_project")


def _task() -> LemmaTask:
    source = (FIXTURE_ROOT / "PatchFixture.lean").read_text(encoding="utf-8")
    return LemmaTask(
        id="lemma.fixture.patch.add_zero",
        task_version=1,
        title="Patch fixed Nat.add_zero fixture",
        task_format="patch",
        task_class="source_sorry",
        source_stream="fixed_fixture",
        source_ref={
            "kind": "fixed_fixture",
            "name": "lean_patch_project",
            "path": "tests/fixtures/lean_patch_project/PatchFixture.lean",
        },
        source_license="CC-BY-4.0",
        imports=(),
        allowed_files=("PatchFixture.lean",),
        allowed_imports=(),
        theorem_name="PatchFixture.add_zero_fixture",
        type_expr="forall n : Nat, n + 0 = n",
        statement=source,
        submission_stub=source,
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        policy="restricted_helpers",
        target_type_sha256=target_type_sha256("forall n : Nat, n + 0 = n"),
        reproduction_command="lake build PatchFixture",
    )


def _valid_patch() -> str:
    return (FIXTURE_ROOT / "patches/add_zero_fixture.patch").read_text(encoding="utf-8")


def _patch(*body: str) -> str:
    return "\n".join(["diff --git a/PatchFixture.lean b/PatchFixture.lean", *body, ""])


def _settings() -> LemmaSettings:
    return LemmaSettings(_env_file=None, lean_use_docker=False)


def _ok_reproduction(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")


def _mock_reproduction(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    monkeypatch.setattr("lemma.lean.patch_task._run_reproduction_command", fn)


def _verdict(monkeypatch: pytest.MonkeyPatch, patch_text: str, **kwargs):
    _mock_reproduction(monkeypatch, kwargs.pop("reproduction", _ok_reproduction))
    return preflight_submission(
        _task(),
        build_patch_submission(_task(), solver_hotkey="hk", patch_text=patch_text),
        settings=_settings(),
        source_root=FIXTURE_ROOT,
        **kwargs,
    )


def test_preflight_accepts_valid_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _verdict(monkeypatch, _valid_patch())
    assert verdict.accepted is True
    assert verdict.rejection_class == "ok"


def test_preflight_rejects_new_sorry(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = _patch(
        "--- a/PatchFixture.lean",
        "+++ b/PatchFixture.lean",
        "@@ -1 +1,2 @@",
        " namespace PatchFixture",
        "+def helperNat : Nat := 0",
    )
    verdict = _verdict(monkeypatch, patch, run_reproduction=False)
    assert verdict.accepted is False
    assert verdict.rejection_class == "new_sorry_detected"


def test_preflight_rejects_new_admit(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = _patch("--- a/PatchFixture.lean", "+++ b/PatchFixture.lean", "@@ -4 +4 @@", "-  sorry", "+  admit")
    verdict = _verdict(monkeypatch, patch, run_reproduction=False)
    assert verdict.accepted is False
    assert verdict.rejection_class == "new_admit_detected"


def test_preflight_rejects_target_type_change(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = _patch(
        "--- a/PatchFixture.lean",
        "+++ b/PatchFixture.lean",
        "@@ -3 +3 @@",
        "-theorem add_zero_fixture (n : Nat) : n + 0 = n := by",
        "+theorem add_zero_fixture (n : Nat) : n = n := by",
    )
    verdict = _verdict(monkeypatch, patch, run_reproduction=False)
    assert verdict.accepted is False
    assert verdict.rejection_class == "target_type_changed"


def test_preflight_rejects_new_axiom(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = _patch(
        "--- a/PatchFixture.lean",
        "+++ b/PatchFixture.lean",
        "@@ -1 +1,2 @@",
        " namespace PatchFixture",
        "+axiom bad : False",
    )
    verdict = _verdict(monkeypatch, patch, run_reproduction=False)
    assert verdict.accepted is False
    assert verdict.rejection_class == "new_axiom_detected"


def test_preflight_rejects_forbidden_import(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = _patch(
        "--- a/PatchFixture.lean",
        "+++ b/PatchFixture.lean",
        "@@ -1 +1,2 @@",
        "+import Mathlib",
        " namespace PatchFixture",
    )
    verdict = _verdict(monkeypatch, patch, run_reproduction=False)
    assert verdict.accepted is False
    assert verdict.rejection_class == "forbidden_import"


def test_preflight_rejects_disallowed_file_as_patch_apply_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _verdict(monkeypatch, _valid_patch().replace("PatchFixture.lean", "Other.lean"), run_reproduction=False)
    assert verdict.accepted is False
    assert verdict.rejection_class == "patch_apply_failed"
    assert verdict.detail == "disallowed_file"


def test_preflight_rejects_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timed_out(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout_s)

    verdict = _verdict(monkeypatch, _valid_patch(), reproduction=timed_out)
    assert verdict.accepted is False
    assert verdict.rejection_class == "timeout"


def test_preflight_rejects_compile_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="unsolved goals")

    verdict = _verdict(monkeypatch, _valid_patch(), reproduction=failed)
    assert verdict.accepted is False
    assert verdict.rejection_class == "lean_compile_error"


def test_preflight_rejects_duplicate_solution(monkeypatch: pytest.MonkeyPatch) -> None:
    submission = build_patch_submission(_task(), solver_hotkey="hk", patch_text=_valid_patch())
    verdict = preflight_submission(
        _task(),
        submission,
        settings=_settings(),
        source_root=FIXTURE_ROOT,
        solved_proof_hashes=frozenset({submission.proof_sha256}),
    )
    assert verdict.accepted is False
    assert verdict.rejection_class == "duplicate_solution"


def test_preflight_rejects_task_already_solved() -> None:
    verdict = preflight_submission(
        _task(),
        build_patch_submission(_task(), solver_hotkey="hk", patch_text=_valid_patch()),
        settings=_settings(),
        source_root=FIXTURE_ROOT,
        solved_task_ids=frozenset({_task().id}),
    )
    assert verdict.accepted is False
    assert verdict.rejection_class == "task_already_solved"


def test_preflight_rejects_project_already_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    def base_fails(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="base broken")

    verdict = _verdict(monkeypatch, _valid_patch(), reproduction=base_fails, check_project_builds=True)
    assert verdict.accepted is False
    assert verdict.rejection_class == "project_already_broken"


def test_preflight_reports_validator_internal_error_for_missing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_reproduction(monkeypatch, _ok_reproduction)
    verdict = preflight_submission(
        _task(),
        build_patch_submission(_task(), solver_hotkey="hk", patch_text=_valid_patch()),
        settings=_settings(),
        source_root=Path("tests/fixtures/does_not_exist"),
    )
    assert verdict.accepted is False
    assert verdict.rejection_class == "validator_internal_error"


@pytest.mark.parametrize(
    ("patch_factory", "run_reproduction"),
    [
        (lambda: _valid_patch(), True),
        (lambda: _valid_patch().replace("PatchFixture.lean", "Other.lean"), False),
        (
            lambda: _patch(
                "--- a/PatchFixture.lean",
                "+++ b/PatchFixture.lean",
                "@@ -4 +4 @@",
                "-  sorry",
                "+  admit",
            ),
            False,
        ),
    ],
)
def test_preflight_matches_validator_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_factory,
    run_reproduction: bool,
) -> None:
    _mock_reproduction(monkeypatch, _ok_reproduction)
    patch_text = patch_factory()
    task = _task().model_copy(update={"metadata": {"source_root": str(FIXTURE_ROOT)}})
    submission = build_patch_submission(task, solver_hotkey="hk-parity", patch_text=patch_text)

    settings = _settings().model_copy(
        update={"operator_data_dir": tmp_path / "operator", "corpus_output_dir": tmp_path / "corpus"}
    )
    validator_result = validate_once(
        settings,
        [submission],
        registry=TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64),
        tempo=1,
        no_set_weights=True,
    )
    verdict = preflight_submission(
        task,
        submission,
        settings=settings,
        source_root=FIXTURE_ROOT,
        run_reproduction=run_reproduction,
    )

    assert validator_result.verification_records[0].reason == verdict.rejection_class

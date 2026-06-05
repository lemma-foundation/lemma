"""Patch-task validation for real-source Lean tasks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.lean.patch_task import validate_patch_task
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


def _fixture_patch() -> str:
    return (FIXTURE_ROOT / "patches/add_zero_fixture.patch").read_text(encoding="utf-8")


def _completed(command: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode=returncode, stdout="ok", stderr="failed")


def _settings(tmp_path: Path) -> LemmaSettings:
    return LemmaSettings(
        _env_file=None,
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
        lean_use_docker=False,
    )


def test_patch_task_validator_applies_allowed_patch_and_runs_reproduction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], Path, int]] = []

    def fake_reproduction(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd, timeout_s))
        assert (cwd / "PatchFixture.lean").read_text(encoding="utf-8").count("Nat.add_zero") == 1
        return _completed(command)

    monkeypatch.setattr("lemma.lean.patch_task._run_reproduction_command", fake_reproduction)

    result = validate_patch_task(_task(), source_root=FIXTURE_ROOT, patch_text=_fixture_patch(), timeout_s=7)

    assert result.accepted is True
    assert result.reason == "ok"
    assert result.changed_files == ("PatchFixture.lean",)
    assert calls[0][0] == ["lake", "build", "PatchFixture"]
    assert calls[0][2] == 7


def test_patch_task_validator_rejects_disallowed_files() -> None:
    patch = _fixture_patch().replace("PatchFixture.lean", "Other.lean")

    result = validate_patch_task(_task(), source_root=FIXTURE_ROOT, patch_text=patch, run_reproduction=False)

    assert result.accepted is False
    assert result.reason == "disallowed_file"
    assert result.stderr_tail == "Other.lean"


def test_patch_task_validator_rejects_statement_changes() -> None:
    patch = "\n".join(
        [
            "diff --git a/PatchFixture.lean b/PatchFixture.lean",
            "--- a/PatchFixture.lean",
            "+++ b/PatchFixture.lean",
            "@@ -3 +3 @@",
            "-theorem add_zero_fixture (n : Nat) : n + 0 = n := by",
            "+theorem add_zero_fixture (n : Nat) : n = n := by",
            "",
        ]
    )

    result = validate_patch_task(_task(), source_root=FIXTURE_ROOT, patch_text=patch, run_reproduction=False)

    assert result.accepted is False
    assert result.reason == "target_statement_changed"


def test_patch_task_validator_rejects_remaining_holes() -> None:
    patch = "\n".join(
        [
            "diff --git a/PatchFixture.lean b/PatchFixture.lean",
            "--- a/PatchFixture.lean",
            "+++ b/PatchFixture.lean",
            "@@ -1 +1,2 @@",
            " namespace PatchFixture",
            "+def helperNat : Nat := 0",
            "",
        ]
    )

    result = validate_patch_task(_task(), source_root=FIXTURE_ROOT, patch_text=patch, run_reproduction=False)

    assert result.accepted is False
    assert result.reason == "hole"


def test_patch_task_validator_rejects_trust_expansion() -> None:
    patch = "\n".join(
        [
            "diff --git a/PatchFixture.lean b/PatchFixture.lean",
            "--- a/PatchFixture.lean",
            "+++ b/PatchFixture.lean",
            "@@ -1 +1,2 @@",
            " namespace PatchFixture",
            "+axiom bad : False",
            "",
        ]
    )

    result = validate_patch_task(_task(), source_root=FIXTURE_ROOT, patch_text=patch, run_reproduction=False)

    assert result.accepted is False
    assert result.reason == "trust_expansion"


def test_patch_task_validator_rejects_forbidden_imports() -> None:
    patch = "\n".join(
        [
            "diff --git a/PatchFixture.lean b/PatchFixture.lean",
            "--- a/PatchFixture.lean",
            "+++ b/PatchFixture.lean",
            "@@ -1 +1,2 @@",
            "+import Mathlib",
            " namespace PatchFixture",
            "",
        ]
    )

    result = validate_patch_task(_task(), source_root=FIXTURE_ROOT, patch_text=patch, run_reproduction=False)

    assert result.accepted is False
    assert result.reason == "forbidden_import"


def test_patch_task_validator_allows_existing_source_imports(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    shutil.copytree(FIXTURE_ROOT, source_root)
    source_file = source_root / "PatchFixture.lean"
    source_file.write_text("import Mathlib\n\n" + source_file.read_text(encoding="utf-8"), encoding="utf-8")
    patch = "\n".join(
        [
            "diff --git a/PatchFixture.lean b/PatchFixture.lean",
            "--- a/PatchFixture.lean",
            "+++ b/PatchFixture.lean",
            "@@ -6 +6 @@",
            "-  sorry",
            "+  exact Nat.add_zero n",
            "",
        ]
    )

    result = validate_patch_task(_task(), source_root=source_root, patch_text=patch, run_reproduction=False)

    assert result.accepted is True
    assert result.reason == "ok"


def test_patch_task_validator_allows_existing_source_axioms(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    shutil.copytree(FIXTURE_ROOT, source_root)
    source_file = source_root / "PatchFixture.lean"
    source_file.write_text("axiom existing : True\n\n" + source_file.read_text(encoding="utf-8"), encoding="utf-8")
    patch = "\n".join(
        [
            "diff --git a/PatchFixture.lean b/PatchFixture.lean",
            "--- a/PatchFixture.lean",
            "+++ b/PatchFixture.lean",
            "@@ -6 +6 @@",
            "-  sorry",
            "+  exact Nat.add_zero n",
            "",
        ]
    )

    result = validate_patch_task(_task(), source_root=source_root, patch_text=patch, run_reproduction=False)

    assert result.accepted is True
    assert result.reason == "ok"


def test_patch_task_validator_preserves_multiline_target_declaration(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    shutil.copytree(FIXTURE_ROOT, source_root)
    source = "\n".join(
        [
            "namespace PatchFixture",
            "",
            "theorem multi_line",
            "  (n : Nat)",
            "  : n = n := by",
            "  sorry",
            "",
            "end PatchFixture",
            "",
        ]
    )
    (source_root / "PatchFixture.lean").write_text(source, encoding="utf-8")
    task = _task().model_copy(
        update={
            "statement": source,
            "submission_stub": source,
            "theorem_name": "PatchFixture.multi_line",
            "type_expr": "forall n : Nat, n = n",
            "target_type_sha256": target_type_sha256("forall n : Nat, n = n"),
        }
    )
    patch = "\n".join(
        [
            "diff --git a/PatchFixture.lean b/PatchFixture.lean",
            "--- a/PatchFixture.lean",
            "+++ b/PatchFixture.lean",
            "@@ -6 +6 @@",
            "-  sorry",
            "+  rfl",
            "",
        ]
    )

    result = validate_patch_task(task, source_root=source_root, patch_text=patch, run_reproduction=False)

    assert result.accepted is True
    assert result.reason == "ok"


def test_patch_task_validator_reports_reproduction_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_reproduction(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        return _completed(command, returncode=1)

    monkeypatch.setattr("lemma.lean.patch_task._run_reproduction_command", fake_reproduction)

    result = validate_patch_task(_task(), source_root=FIXTURE_ROOT, patch_text=_fixture_patch())

    assert result.accepted is False
    assert result.reason == "reproduction_failed"
    assert result.stderr_tail == "failed"


def test_validator_accepts_patch_submission_through_default_verifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task().model_copy(update={"metadata": {"source_root": str(FIXTURE_ROOT)}})

    def fake_reproduction(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        return _completed(command)

    monkeypatch.setattr("lemma.lean.patch_task._run_reproduction_command", fake_reproduction)

    result = validate_once(
        _settings(tmp_path),
        [build_patch_submission(task, solver_hotkey="hk-patch", patch_text=_fixture_patch())],
        registry=TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64),
        tempo=1,
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-patch": 1}
    assert result.verification_records[0].passed is True
    assert result.corpus_rows[0].artifact_kind == "patch"
    assert result.corpus_rows[0].proof_script is None
    assert result.corpus_rows[0].patch_text == _fixture_patch()
    assert result.corpus_rows[0].patch_sha256 == result.corpus_rows[0].proof_sha256


def test_validator_rejects_patch_submission_cheating_through_default_verifier(tmp_path: Path) -> None:
    task = _task().model_copy(update={"metadata": {"source_root": str(FIXTURE_ROOT)}})
    bad_patch = _fixture_patch().replace("PatchFixture.lean", "Other.lean")

    result = validate_once(
        _settings(tmp_path),
        [build_patch_submission(task, solver_hotkey="hk-patch", patch_text=bad_patch)],
        registry=TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64),
        tempo=1,
        no_set_weights=True,
    )

    assert result.score.credits == {}
    assert result.verification_records[0].passed is False
    assert result.verification_records[0].reason == "disallowed_file"
    assert result.corpus_rows == ()

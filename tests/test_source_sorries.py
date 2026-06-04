"""Source-sorry task ingestion."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.common.config import LemmaSettings
from lemma.source_checkouts import source_checkout_path
from lemma.source_sorries import build_patch_task_from_sorrydb_record
from lemma.submissions import build_patch_submission
from lemma.tasks import TaskRegistry, load_task_registry
from lemma.validator import validate_once

SOURCE_ROOT = Path("tests/fixtures/lean_real_source_project")


def _sorrydb_row() -> dict[str, object]:
    return {
        "repo": {
            "remote": "https://github.com/SorryDB/SorryDB",
            "branch": "master",
            "commit": "0" * 40,
            "lean_version": "v4.30.0-rc2",
        },
        "location": {
            "path": "RealSource/Basic.lean",
            "start_line": 4,
            "start_column": 3,
            "end_line": 4,
            "end_column": 8,
        },
        "debug_info": {
            "goal": "n : Nat ⊢ n + 0 = n",
            "url": "https://github.com/SorryDB/SorryDB/blob/0000000000000000000000000000000000000000/RealSource/Basic.lean#L4",
        },
        "metadata": {
            "blame_email_hash": "private-hash-not-exported",
            "blame_date": "2026-01-01T00:00:00+00:00",
            "inclusion_date": "2026-01-02T00:00:00+00:00",
        },
        "id": "sorrydb-fixture-add-zero",
    }


def _task():
    return build_patch_task_from_sorrydb_record(
        _sorrydb_row(),
        source_root=SOURCE_ROOT,
        theorem_name="PublicSource.add_zero_real_source",
        type_expr="forall n : Nat, n + 0 = n",
        source_license="Apache-2.0",
        mathlib_rev="fixture-mathlib-rev",
        reproduction_command="lake build RealSource",
    )


def _patch() -> str:
    return (SOURCE_ROOT / "patches/add_zero_real_source.patch").read_text(encoding="utf-8")


def _completed(command: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode=returncode, stdout="ok", stderr="")


def _settings(tmp_path: Path) -> LemmaSettings:
    return LemmaSettings(
        _env_file=None,
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
        lean_use_docker=False,
    )


def _checkout_cache(tmp_path: Path, task) -> Path:
    root = tmp_path / "checkouts"
    checkout = source_checkout_path(root, task.source_ref)
    assert checkout is not None
    shutil.copytree(SOURCE_ROOT, checkout)
    return root


def test_sorrydb_row_builds_source_pinned_patch_task() -> None:
    task = _task()

    assert task.id == "lemma.sorrydb.sorrydb-fixture-add-zero"
    assert task.task_format == "patch"
    assert task.task_class == "source_sorry"
    assert task.source_stream == "sorrydb"
    assert task.source_ref.kind == "sorrydb"
    assert task.source_ref.name == "SorryDB/SorryDB"
    assert task.source_ref.commit == "0" * 40
    assert task.source_ref.path == "RealSource/Basic.lean"
    assert task.allowed_files == ("RealSource/Basic.lean",)
    assert task.reproduction_command == "lake build RealSource"
    assert task.metadata["source_start_line"] == 4
    assert task.metadata["source_file_sha256"] == hashlib.sha256(
        (SOURCE_ROOT / "RealSource/Basic.lean").read_bytes()
    ).hexdigest()
    assert "blame_email_hash" not in task.metadata
    assert len(task.target_sha256) == 64
    assert len(task.target_type_sha256) == 64
    assert len(task.environment_sha256 or "") == 64


def test_source_checkout_path_uses_public_source_identity() -> None:
    task = _task()

    assert source_checkout_path(Path("checkouts"), task.source_ref) == (
        Path("checkouts") / "sorrydb" / "SorryDB__SorryDB" / ("0" * 40)
    )


def test_source_sorry_patch_task_validates_and_publishes_patch_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task().model_copy(update={"metadata": {**_task().metadata, "source_root": str(SOURCE_ROOT)}})

    def fake_reproduction(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        assert command == ["lake", "build", "RealSource"]
        assert timeout_s > 0
        assert "exact Nat.add_zero n" in (cwd / "RealSource/Basic.lean").read_text(encoding="utf-8")
        return _completed(command)

    monkeypatch.setattr("lemma.lean.patch_task._run_reproduction_command", fake_reproduction)

    result = validate_once(
        _settings(tmp_path),
        [build_patch_submission(task, solver_hotkey="hk-source", patch_text=_patch())],
        registry=TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64),
        tempo=1,
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-source": 1}
    assert result.corpus_rows[0].artifact_kind == "patch"
    assert result.corpus_rows[0].patch_text == _patch()
    assert "source_root" not in result.corpus_rows[0].metadata


def test_validator_resolves_source_sorry_from_checkout_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task()
    checkout_root = _checkout_cache(tmp_path, task)

    def fake_reproduction(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        assert "exact Nat.add_zero n" in (cwd / "RealSource/Basic.lean").read_text(encoding="utf-8")
        return _completed(command)

    monkeypatch.setattr("lemma.lean.patch_task._run_reproduction_command", fake_reproduction)

    settings = _settings(tmp_path).model_copy(update={"source_checkout_root": checkout_root})
    result = validate_once(
        settings,
        [build_patch_submission(task, solver_hotkey="hk-source", patch_text=_patch())],
        registry=TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64),
        tempo=1,
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-source": 1}
    assert result.corpus_rows[0].artifact_kind == "patch"


def test_tasks_import_sorrydb_writes_registry(tmp_path: Path) -> None:
    row_path = tmp_path / "sorry.json"
    registry_path = tmp_path / "registry.json"
    row_path.write_text(json.dumps(_sorrydb_row()), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "import-sorrydb",
            "--sorry-json",
            str(row_path),
            "--source-root",
            str(SOURCE_ROOT),
            "--theorem-name",
            "PublicSource.add_zero_real_source",
            "--type-expr",
            "forall n : Nat, n + 0 = n",
            "--source-license",
            "Apache-2.0",
            "--mathlib-rev",
            "fixture-mathlib-rev",
            "--reproduction-command",
            "lake build RealSource",
            "--output",
            str(registry_path),
        ],
    )

    assert result.exit_code == 0, result.output
    registry = load_task_registry(registry_path.read_bytes())
    assert registry.tasks[0].id == "lemma.sorrydb.sorrydb-fixture-add-zero"
    assert json.loads(result.output)["registry_sha256"] == hashlib.sha256(registry_path.read_bytes()).hexdigest()

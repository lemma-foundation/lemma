"""Source checkout materialization."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from click.testing import CliRunner
from lemma.cli.main import main
from lemma.source_checkouts import materialize_source_checkout, source_checkout_path
from lemma.task_supply import write_registry
from lemma.tasks import LemmaTask, SourceRef, target_type_sha256

PATCH_FIXTURE_ROOT = Path("tests/fixtures/lean_patch_project")


def _run(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _upstream_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "upstream"
    repo.mkdir()
    for name in ("lakefile.lean", "lean-toolchain", "PatchFixture.lean"):
        shutil.copy2(PATCH_FIXTURE_ROOT / name, repo / name)
    _run(["git", "init"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(
        ["git", "-c", "user.name=Lemma", "-c", "user.email=lemma@example.invalid", "commit", "-m", "fixture"],
        cwd=repo,
    )
    return repo, _run(["git", "rev-parse", "HEAD"], cwd=repo)


def _source_ref(repo: Path, commit: str) -> SourceRef:
    return SourceRef(
        kind="lean_project",
        name="example/project",
        url=str(repo),
        commit=commit,
        path="PatchFixture.lean",
    )


def _task(repo: Path, commit: str) -> LemmaTask:
    source = (PATCH_FIXTURE_ROOT / "PatchFixture.lean").read_text(encoding="utf-8")
    return LemmaTask(
        id="lemma.test.materialize_patch",
        task_version=1,
        title="Materialize patch task",
        task_format="patch",
        task_class="source_sorry",
        source_stream="lean_project",
        source_ref=_source_ref(repo, commit),
        source_license="Apache-2.0",
        imports=(),
        allowed_files=("PatchFixture.lean",),
        theorem_name="PatchFixture.add_zero_fixture",
        type_expr="forall n : Nat, n + 0 = n",
        statement=source,
        submission_stub=source,
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="fixture-mathlib-rev",
        target_type_sha256=target_type_sha256("forall n : Nat, n + 0 = n"),
        reproduction_command="lake build PatchFixture",
    )


def test_materialize_source_checkout_clones_and_reuses_pinned_commit(tmp_path: Path) -> None:
    repo, commit = _upstream_repo(tmp_path)
    root = tmp_path / "checkouts"
    source_ref = _source_ref(repo, commit)
    expected_path = source_checkout_path(root, source_ref)

    first = materialize_source_checkout(root, source_ref, timeout_s=30)
    second = materialize_source_checkout(root, source_ref, timeout_s=30)

    assert expected_path is not None
    assert first.path == expected_path
    assert first.action == "cloned"
    assert second.action == "ready"
    assert _run(["git", "-C", str(expected_path), "rev-parse", "HEAD"]) == commit


def test_tasks_materialize_checkout_command_writes_expected_cache(tmp_path: Path) -> None:
    repo, commit = _upstream_repo(tmp_path)
    registry_path = tmp_path / "registry.json"
    write_registry([_task(repo, commit)], registry_path)
    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    root = tmp_path / "source-checkouts"

    result = CliRunner().invoke(
        main,
        ["tasks", "materialize-checkout", "lemma.test.materialize_patch", "--root", str(root), "--timeout", "30"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["action"] == "cloned"
    assert payload["commit"] == commit
    assert _run(["git", "-C", payload["path"], "rev-parse", "HEAD"]) == commit

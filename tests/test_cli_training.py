"""CLI smoke tests for registry tasks, submissions, and operator reports."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.lean.sandbox import VerifyResult
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport
from lemma.submissions import build_submission
from lemma.task_supply import make_task, write_registry
from lemma.tasks import LemmaTask, target_type_sha256

PATCH_FIXTURE_ROOT = Path("tests/fixtures/lean_patch_project")


def _proof(theorem_name: str = "true_intro_sample") -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def _write_registry(tmp_path: Path, *, task_count: int = 1) -> tuple[Path, str]:
    tasks = [
        make_task(
            task_id=f"lemma.test.cli_{idx}",
            title=f"CLI {idx}",
            theorem_name=f"cli_true_{idx}",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=0,
        )
        for idx in range(task_count)
    ]
    path = tmp_path / "registry.json"
    write_registry(tasks, path)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _write_patch_registry(tmp_path: Path) -> tuple[Path, str]:
    source = (PATCH_FIXTURE_ROOT / "PatchFixture.lean").read_text(encoding="utf-8")
    task = LemmaTask(
        id="lemma.test.patch_cli",
        task_version=1,
        title="CLI patch task",
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
    path = tmp_path / "patch-registry.json"
    path.write_text(
        json.dumps({"schema_version": 1, "tasks": [task.model_dump(mode="json")]}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_tasks_list_uses_default_registry() -> None:
    result = CliRunner().invoke(main, ["tasks", "list"])

    assert result.exit_code == 0
    assert "lemma.sample.true_intro" in result.output


def test_task_show_aliases_match_goal_language() -> None:
    for args in (["tasks", "show", "lemma.sample.true_intro"], ["task", "show", "lemma.sample.true_intro"]):
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 0
        assert "Submission stub" in result.output


def test_verify_loads_registry_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry_path, registry_sha = _write_registry(tmp_path)
    proof_path = tmp_path / "Proof.lean"
    proof_path.write_text(_proof("cli_true_0"), encoding="utf-8")
    calls: dict[str, object] = {}

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        calls["task_id"] = problem.id
        calls["proof_script"] = proof_script
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_verify)

    result = CliRunner().invoke(
        main,
        ["verify", "lemma.test.cli_0", "--submission", str(proof_path)],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
        },
    )

    assert result.exit_code == 0, result.output
    assert calls == {"task_id": "lemma.test.cli_0", "proof_script": _proof("cli_true_0")}


def test_verify_patch_uses_shared_patch_validator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry_path, registry_sha = _write_patch_registry(tmp_path)
    calls: list[list[str]] = []

    def fake_reproduction(command: list[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("lemma.lean.patch_task._run_reproduction_command", fake_reproduction)

    result = CliRunner().invoke(
        main,
        [
            "verify-patch",
            "lemma.test.patch_cli",
            "--source-root",
            str(PATCH_FIXTURE_ROOT),
            "--patch",
            str(PATCH_FIXTURE_ROOT / "patches/add_zero_fixture.patch"),
            "--timeout",
            "5",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["accepted"] is True
    assert payload["changed_files"] == ["PatchFixture.lean"]
    assert calls == [["lake", "build", "PatchFixture"]]


def test_verify_patch_rejects_disallowed_file(tmp_path: Path) -> None:
    registry_path, registry_sha = _write_patch_registry(tmp_path)
    patch_path = tmp_path / "bad.patch"
    patch_path.write_text(
        (PATCH_FIXTURE_ROOT / "patches/add_zero_fixture.patch").read_text(encoding="utf-8").replace(
            "PatchFixture.lean",
            "Other.lean",
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "verify-patch",
            "lemma.test.patch_cli",
            "--source-root",
            str(PATCH_FIXTURE_ROOT),
            "--patch",
            str(patch_path),
            "--no-run-reproduction",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
        },
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["accepted"] is False
    assert payload["reason"] == "disallowed_file"


def test_submit_writes_task_bound_package(tmp_path: Path) -> None:
    registry_path, registry_sha = _write_registry(tmp_path)
    proof_path = tmp_path / "Proof.lean"
    proof_path.write_text(_proof("cli_true_0"), encoding="utf-8")
    output_path = tmp_path / "submission.json"

    result = CliRunner().invoke(
        main,
        [
            "submit",
            "lemma.test.cli_0",
            "--submission",
            str(proof_path),
            "--solver-hotkey",
            "hk-cli",
            "--output",
            str(output_path),
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["task_id"] == "lemma.test.cli_0"
    assert payload["solver_hotkey"] == "hk-cli"


def test_setup_writes_registry_settings(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    result = CliRunner().invoke(
        main,
        [
            "setup",
            "--env-file",
            str(env_path),
            "--task-registry-url",
            "tasks/registry.json",
            "--task-registry-sha256",
            "0" * 64,
            "--active-k",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    text = env_path.read_text(encoding="utf-8")
    assert 'LEMMA_TASK_REGISTRY_URL="tasks/registry.json"' in text
    assert f'LEMMA_TASK_REGISTRY_SHA256_EXPECTED="{"0" * 64}"' in text
    assert 'LEMMA_ACTIVE_K="3"' in text


def test_validate_once_no_set_weights(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry_path, registry_sha = _write_registry(tmp_path)
    task = make_task(
        task_id="lemma.test.cli_0",
        title="CLI 0",
        theorem_name="cli_true_0",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(
        build_submission(task, solver_hotkey="hk-cli", proof_script=_proof("cli_true_0")).model_dump_json() + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lemma.verifiers.lean.run_lean_verify",
        lambda *args, **kwargs: VerifyResult(passed=True, reason="ok", proof_term_hash="term-cli"),
    )

    result = CliRunner().invoke(
        main,
        ["validate", "--once", "--submissions-jsonl", str(submission_path), "--no-set-weights"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
            "LEMMA_ACTIVE_K": "1",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
            "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["accepted_unique"] == 1
    assert payload["weights_set"] is False


def test_operator_preflight_and_diagnostics_use_registry(tmp_path: Path) -> None:
    registry_path, registry_sha = _write_registry(tmp_path, task_count=2)
    operator_dir = tmp_path / "operator"
    corpus_dir = tmp_path / "corpus"
    operator_dir.mkdir()
    corpus_dir.mkdir()
    (operator_dir / "validator-runs.jsonl").write_text("{}\n", encoding="utf-8")
    output_path = tmp_path / "diagnostics.json"
    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
        "LEMMA_ACTIVE_K": "2",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_CORPUS_OUTPUT_DIR": str(corpus_dir),
        "LEMMA_OPERATOR_DATA_DIR": str(operator_dir),
    }

    preflight = CliRunner().invoke(main, ["operator", "preflight"], env=env)
    assert preflight.exit_code == 0, preflight.output
    assert OperatorPreflightReport.model_validate_json(preflight.output).ok is True

    diagnostics = CliRunner().invoke(main, ["operator", "diagnostics", "--output", str(output_path)], env=env)
    assert diagnostics.exit_code == 0, diagnostics.output
    report = OperatorDiagnosticsReport.model_validate_json(output_path.read_text(encoding="utf-8"))
    assert report.registry_inspect is not None
    assert report.registry_inspect.total_task_count == 2

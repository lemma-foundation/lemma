"""CLI smoke tests for task and submission commands."""

from __future__ import annotations

import hashlib
import json
import sys

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.corpus import build_corpus_row, write_jsonl
from lemma.lean.sandbox import VerifyResult
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport, OperatorRegistryInspectReport
from lemma.submissions import build_submission
from lemma.task_supply import make_task, write_registry


def _true_intro_proof() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem true_intro_sample : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def test_tasks_list_uses_default_registry() -> None:
    result = CliRunner().invoke(main, ["tasks", "list"])

    assert result.exit_code == 0
    assert "lemma.sample.true_intro" in result.output


def test_task_show_aliases_match_goal_language() -> None:
    for args in (["tasks", "show", "lemma.sample.true_intro"], ["task", "show", "lemma.sample.true_intro"]):
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 0
        assert "Submission stub" in result.output


def test_root_help_prioritizes_normal_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    positions = [result.output.index(name) for name in ["setup", "status", "mine", "validate"]]
    assert positions == sorted(positions)
    assert "Examples:" in result.output


def test_submit_writes_task_bound_package(tmp_path) -> None:
    proof = tmp_path / "Submission.lean"
    proof.write_text(_true_intro_proof(), encoding="utf-8")
    output = tmp_path / "submission.json"

    result = CliRunner().invoke(
        main,
        [
            "submit",
            "lemma.sample.true_intro",
            "--submission",
            str(proof),
            "--solver-hotkey",
            "hk1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert '"task_id": "lemma.sample.true_intro"' in text
    assert '"target_sha256":' in text


def test_mine_once_with_fake_prover(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    prover = tmp_path / "prover.py"
    prover.write_text(
        "import json, sys\n"
        "task = json.load(sys.stdin)\n"
        "print(json.dumps({'task_id': task['task_id'], 'proof_script': " + repr(_true_intro_proof()) + "}))\n",
        encoding="utf-8",
    )
    output = tmp_path / "submission.json"
    monkeypatch.setenv("LEMMA_OPERATOR_DATA_DIR", str(tmp_path / "operator"))

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.miner.run_lean_verify", fake_verify)

    result = CliRunner().invoke(
        main,
        [
            "mine",
            "--once",
            "--task-id",
            "lemma.sample.true_intro",
            "--prover-command",
            f"{sys.executable} {prover}",
            "--solver-hotkey",
            "hk1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"task_id": "lemma.sample.true_intro"' in output.read_text(encoding="utf-8")


def test_validate_once_no_set_weights() -> None:
    result = CliRunner().invoke(main, ["validate", "--once", "--no-set-weights"])

    assert result.exit_code == 0
    assert '"scores": {}' in result.output
    assert '"weights_set": false' in result.output


def _write_preflight_registry(tmp_path, *, task_count: int = 2) -> tuple[str, str]:
    tasks = [
        make_task(
            task_id=f"lemma.test.preflight_{idx}",
            title=f"Preflight {idx}",
            theorem_name=f"preflight_true_{idx}",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=0,
        )
        for idx in range(task_count)
    ]
    path = tmp_path / "registry.json"
    write_registry(tasks, path)
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def test_operator_preflight_passes_with_pinned_registry(tmp_path) -> None:
    registry_url, registry_sha256 = _write_preflight_registry(tmp_path)

    result = CliRunner().invoke(
        main,
        ["operator", "preflight"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": registry_url,
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
            "LEMMA_ACTIVE_K": "2",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_ACTIVE_QUEUE_SEED": "pytest-preflight",
            "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
            "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        },
    )

    assert result.exit_code == 0, result.output
    payload = OperatorPreflightReport.model_validate_json(result.output)
    checks = {check.name: check for check in payload.checks}
    assert payload.ok is True
    assert payload.registry_sha256 == registry_sha256
    assert checks["registry_hash_pin"].ok is True
    assert checks["active_window"].detail.startswith("2 active / K=2")
    assert (tmp_path / "corpus").is_dir()
    assert (tmp_path / "operator").is_dir()


def test_operator_preflight_fails_without_registry_pin(tmp_path) -> None:
    registry_url, _ = _write_preflight_registry(tmp_path, task_count=1)

    result = CliRunner().invoke(
        main,
        ["operator", "preflight"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": registry_url,
            "LEMMA_ACTIVE_K": "1",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
            "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        },
    )

    assert result.exit_code == 1, result.output
    payload = OperatorPreflightReport.model_validate_json(result.output)
    checks = {check.name: check for check in payload.checks}
    assert payload.ok is False
    assert checks["registry_hash_pin"].ok is False


def test_operator_diagnostics_writes_public_safe_report(tmp_path) -> None:
    registry_url, registry_sha256 = _write_preflight_registry(tmp_path)
    output_path = tmp_path / "diagnostics" / "operator.json"

    result = CliRunner().invoke(
        main,
        ["operator", "diagnostics", "--output", str(output_path)],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": registry_url,
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
            "LEMMA_ACTIVE_K": "2",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_ACTIVE_QUEUE_SEED": "pytest-preflight",
            "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
            "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        },
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    payload_text = output_path.read_text(encoding="utf-8")
    payload = OperatorDiagnosticsReport.model_validate_json(payload_text)
    checks = {check.name: check for check in payload.preflight.checks}
    assert summary["active_task_count"] == 2
    assert payload.preflight.ok is True
    assert payload.registry_sha256 == registry_sha256
    assert set(payload.active_task_ids) == {"lemma.test.preflight_0", "lemma.test.preflight_1"}
    assert checks["corpus_output_dir"].detail == "ready"
    assert checks["operator_data_dir"].detail == "ready"
    assert str(tmp_path) not in payload_text
    assert "LEMMA_TASK_REGISTRY_URL" not in payload_text


def test_operator_registry_inspect_counts_active_waiting_and_parked(tmp_path) -> None:
    tasks = [
        make_task(
            task_id=f"lemma.test.inspect_{idx}",
            title=f"Inspect {idx}",
            theorem_name=f"inspect_true_{idx}",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=queue_depth,
        )
        for idx, queue_depth in enumerate((0, 0, 0, 2))
    ]
    registry_path = tmp_path / "registry.json"
    write_registry(tasks, registry_path)
    registry_sha256 = hashlib.sha256(registry_path.read_bytes()).hexdigest()

    result = CliRunner().invoke(
        main,
        ["operator", "registry-inspect"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
            "LEMMA_ACTIVE_K": "2",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_ACTIVE_QUEUE_SEED": "pytest-inspect",
        },
    )

    assert result.exit_code == 0, result.output
    payload = OperatorRegistryInspectReport.model_validate_json(result.output)
    assert payload.registry_sha256 == registry_sha256
    assert payload.total_task_count == 4
    assert payload.active_task_count == 2
    assert payload.eligible_task_count == 3
    assert payload.waiting_task_count == 1
    assert payload.parked_task_count == 1
    assert payload.max_queue_depth == 2
    assert payload.queue_depth_counts == {"0": 3, "2": 1}


def test_tasks_build_mathlib_snapshot_writes_pinned_registry(tmp_path) -> None:
    manifest = tmp_path / "snapshot.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "theorem_name": "True.intro",
                "type_expr": "True",
                "mathlib_rev": "abc123",
                "source_path": "Mathlib/Init.lean",
                "source_license": "Apache-2.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "registry.json"

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-mathlib-snapshot",
            "--input",
            str(manifest),
            "--output",
            str(output),
            "--frontier-depth",
            "4",
            "--signed-by",
            "fixture-signer",
            "--signature",
            "fixture-signature",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["registry_sha256"]
    assert payload["tasks"] == 1
    from lemma.tasks import load_task_registry

    loaded = load_task_registry(output.read_bytes(), payload["registry_sha256"])
    assert loaded.sha256 == payload["registry_sha256"]
    registry = json.loads(output.read_text(encoding="utf-8"))
    task = registry["tasks"][0]
    assert registry["signed_by"] == "fixture-signer"
    assert registry["signature"] == "fixture-signature"
    assert task["source_stream"] == "mathlib_snapshot"
    assert task["queue_position"] == 0
    assert task["frontier_depth"] == 4


def test_corpus_benchmark_export_cli_writes_jsonl_and_index(tmp_path) -> None:
    task = make_task(
        task_id="lemma.test.cli_benchmark",
        title="CLI benchmark",
        theorem_name="cli_benchmark_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    proof = "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem cli_benchmark_true : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )
    row = build_corpus_row(
        task,
        build_submission(task, solver_hotkey="hk1", proof_script=proof, created_at="2026-01-01T00:00:00Z"),
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk1",
        rewarded=True,
        accepted_at="2026-01-01T00:00:01Z",
    )
    corpus_dir = tmp_path / "corpus"
    output = tmp_path / "export" / "proofs.jsonl"
    index = tmp_path / "export" / "index.json"
    write_jsonl([row], corpus_dir / "epoch-1.jsonl")

    result = CliRunner().invoke(
        main,
        ["corpus", "benchmark-export", "--input", str(corpus_dir), "--output", str(output), "--index", str(index)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["row_count"] == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["task"]["id"] == "lemma.test.cli_benchmark"
    assert json.loads(index.read_text(encoding="utf-8"))["export"]["path"] == "proofs.jsonl"

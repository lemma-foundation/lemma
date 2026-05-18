"""Executable smoke for the documented operator registry flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.lean.sandbox import VerifyResult
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport, OperatorRegistryInspectReport
from lemma.submissions import build_submission
from lemma.tasks import load_task_registry


def _proof_for(theorem_name: str) -> str:
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


def test_operator_registry_flow_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    fixture_dir = Path("examples/operator-smoke")
    snapshot = fixture_dir / "snapshot.jsonl"
    registry_path = tmp_path / "tasks" / "mathlib-snapshot.registry.json"

    build = runner.invoke(
        main,
        [
            "tasks",
            "build-mathlib-snapshot",
            "--input",
            str(snapshot),
            "--output",
            str(registry_path),
            "--seed",
            "operator-smoke",
            "--frontier-depth",
            "0",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )

    assert build.exit_code == 0, build.output
    registry_sha256 = json.loads(build.output)["registry_sha256"]
    registry = load_task_registry(registry_path.read_bytes(), registry_sha256)
    active_task = registry.get("lemma.mathlib_snapshot.operator_smoke_true_0")
    inactive_task = registry.get("lemma.mathlib_snapshot.operator_smoke_deep_true")

    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
        "LEMMA_ACTIVE_K": "10",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_ACTIVE_QUEUE_SEED": "operator-smoke",
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_USE_DOCKER": "0",
        "LEMMA_ALLOW_HOST_LEAN": "1",
        "BT_WALLET_HOT": "validator-smoke",
    }
    proof_path = fixture_dir / "Submission.lean"
    package_path = tmp_path / "submission.json"

    inspect = runner.invoke(main, ["operator", "registry-inspect"], env=env)

    assert inspect.exit_code == 0, inspect.output
    inspect_payload = OperatorRegistryInspectReport.model_validate_json(inspect.output)
    assert inspect_payload.registry_sha256 == registry_sha256
    assert inspect_payload.total_task_count == 11
    assert inspect_payload.active_task_count == 10
    assert inspect_payload.eligible_task_count == 10
    assert inspect_payload.waiting_task_count == 0
    assert inspect_payload.parked_task_count == 1
    assert inspect_payload.queue_depth_counts == {"0": 10, "2": 1}

    preflight = runner.invoke(main, ["operator", "preflight"], env=env)

    assert preflight.exit_code == 0, preflight.output
    preflight_payload = OperatorPreflightReport.model_validate_json(preflight.output)
    preflight_checks = {check.name: check for check in preflight_payload.checks}
    assert preflight_payload.ok is True
    assert preflight_payload.registry_sha256 == registry_sha256
    assert preflight_payload.active_K == 10
    assert preflight_checks["registry_hash_pin"].ok is True
    assert preflight_checks["active_window"].detail.startswith("10 active / K=10")
    assert preflight_checks["corpus_output_dir"].ok is True
    assert preflight_checks["operator_data_dir"].ok is True
    assert preflight_checks["lean_verifier"].detail == "host Lean enabled"

    diagnostics_path = tmp_path / "operator-diagnostics.json"
    diagnostics = runner.invoke(
        main,
        ["operator", "diagnostics", "--output", str(diagnostics_path)],
        env=env,
    )

    assert diagnostics.exit_code == 0, diagnostics.output
    diagnostics_summary = json.loads(diagnostics.output)
    diagnostics_text = diagnostics_path.read_text(encoding="utf-8")
    diagnostics_payload = OperatorDiagnosticsReport.model_validate_json(diagnostics_text)
    assert diagnostics_summary["active_task_count"] == 10
    assert diagnostics_payload.preflight.ok is True
    assert diagnostics_payload.registry_sha256 == registry_sha256
    assert active_task.id in diagnostics_payload.active_task_ids
    assert inactive_task.id not in diagnostics_payload.active_task_ids
    assert str(tmp_path) not in diagnostics_text
    assert "LEMMA_TASK_REGISTRY_URL" not in diagnostics_text

    submit = runner.invoke(
        main,
        [
            "submit",
            active_task.id,
            "--submission",
            str(proof_path),
            "--solver-hotkey",
            "miner-active",
            "--output",
            str(package_path),
        ],
        env=env,
    )

    assert submit.exit_code == 0, submit.output
    active_submission = json.loads(package_path.read_text(encoding="utf-8"))
    inactive_submission = build_submission(
        inactive_task,
        solver_hotkey="miner-inactive",
        proof_script=_proof_for(inactive_task.theorem_name),
    ).model_dump(mode="json", exclude_none=True)
    submissions_jsonl = tmp_path / "submissions.jsonl"
    submissions_jsonl.write_text(
        json.dumps(active_submission, sort_keys=True) + "\n"
        + json.dumps(inactive_submission, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.validator.run_lean_verify", fake_verify)

    validate = runner.invoke(
        main,
        [
            "validate",
            "--once",
            "--submissions-jsonl",
            str(submissions_jsonl),
            "--validator-hotkey",
            "validator-smoke",
            "--no-set-weights",
        ],
        env=env,
    )

    assert validate.exit_code == 0, validate.output
    validation = json.loads(validate.output)
    assert validation["accepted_unique"] == 1
    assert validation["corpus_rows"] == 1
    assert validation["scores"] == {"miner-active": 0.1}
    assert validation["weights"] == {"burn_uid:0": 0.9, "miner-active": 0.1}
    assert validation["weights_set"] is False
    assert "inactive_task" in (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")

    corpus_jsonl = tmp_path / "corpus" / "epoch-local.jsonl"
    corpus_validate = runner.invoke(main, ["corpus", "validate", str(corpus_jsonl)], env=env)

    assert corpus_validate.exit_code == 0, corpus_validate.output
    corpus_row = json.loads(corpus_jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert corpus_row["active_K"] == 10
    assert corpus_row["queue_depth"] == 0
    assert corpus_row["source_stream"] == "mathlib_snapshot"
    assert corpus_row["rewarded"] is True

    corpus_index = tmp_path / "exports" / "corpus-index.json"
    corpus_export = runner.invoke(
        main,
        ["corpus", "export", "--input", str(tmp_path / "corpus"), "--output", str(corpus_index)],
        env=env,
    )

    assert corpus_export.exit_code == 0, corpus_export.output
    assert json.loads(corpus_index.read_text(encoding="utf-8"))["row_count"] == 1

    benchmark_jsonl = tmp_path / "exports" / "lemma-proofs.jsonl"
    benchmark_index = tmp_path / "exports" / "benchmark-index.json"
    benchmark_export = runner.invoke(
        main,
        [
            "corpus",
            "benchmark-export",
            "--input",
            str(tmp_path / "corpus"),
            "--output",
            str(benchmark_jsonl),
            "--index",
            str(benchmark_index),
        ],
        env=env,
    )

    assert benchmark_export.exit_code == 0, benchmark_export.output
    benchmark_summary = json.loads(benchmark_export.output)
    assert benchmark_summary["row_count"] == 1
    assert benchmark_summary["source_streams"] == {"mathlib_snapshot": 1}
    benchmark_record = json.loads(benchmark_jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert benchmark_record["reward"]["active_K"] == 10
    assert benchmark_record["provenance"]["validator_hotkey"] == "validator-smoke"

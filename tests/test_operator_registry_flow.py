"""Executable smoke for the documented operator registry flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.lean.sandbox import VerifyResult
from lemma.submissions import build_submission
from lemma.tasks import load_task_registry


def _snapshot_row(theorem_name: str, *, queue_depth: int) -> dict[str, object]:
    return {
        "theorem_name": theorem_name,
        "type_expr": "True",
        "mathlib_rev": "operator-smoke-rev",
        "source_path": "Mathlib/OperatorSmoke.lean",
        "source_license": "Apache-2.0",
        "queue_depth": queue_depth,
    }


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
    snapshot = tmp_path / "snapshot.jsonl"
    registry_path = tmp_path / "tasks" / "mathlib-snapshot.registry.json"
    active_names = [f"operator_smoke_true_{index}" for index in range(10)]
    rows = [_snapshot_row(name, queue_depth=0) for name in active_names]
    rows.append(_snapshot_row("operator_smoke_deep_true", queue_depth=2))
    snapshot.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

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
    active_task = next(task for task in registry.tasks if task.theorem_name in active_names)
    inactive_task = next(task for task in registry.tasks if task.theorem_name == "operator_smoke_deep_true")

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
        "BT_WALLET_HOT": "validator-smoke",
    }
    proof_path = tmp_path / "Submission.lean"
    proof_path.write_text(_proof_for(active_task.theorem_name), encoding="utf-8")
    package_path = tmp_path / "submission.json"

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

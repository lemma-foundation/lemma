"""CLI smoke tests for task and submission commands."""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.lean.sandbox import VerifyResult


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


def test_root_help_prioritizes_normal_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    positions = [result.output.index(name) for name in ["setup", "status", "mine", "validate"]]
    assert positions == sorted(positions)


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
    assert '"weights_set": false' in result.output

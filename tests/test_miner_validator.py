"""Miner and validator one-shot workflow tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.miner import ProverError, mine_once, run_prover_command
from lemma.protocol import ProofResponse, TaskRequest
from lemma.submissions import build_submission
from lemma.task_supply import make_task
from lemma.tasks import TaskRegistry
from lemma.validator import validate_once


def _task():
    return make_task(
        task_id="lemma.test.true",
        title="True task",
        theorem_name="test_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )


def _registry() -> TaskRegistry:
    return TaskRegistry(schema_version=1, tasks=(_task(),), sha256="0" * 64)


def _proof(body: str = "  trivial") -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem test_true : True := by",
            body,
            "",
            "end Submission",
            "",
        ]
    )


def _settings(tmp_path: Path) -> LemmaSettings:
    return LemmaSettings(
        _env_file=None,
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
        lean_use_docker=False,
    )


def test_local_prover_adapter_rejects_invalid_json(tmp_path: Path) -> None:
    script = tmp_path / "bad.py"
    script.write_text("print('not json')\n", encoding="utf-8")

    with pytest.raises(ProverError, match="invalid JSON"):
        run_prover_command(f"{sys.executable} {script}", _task(), 5)


def test_local_prover_adapter_times_out(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")

    with pytest.raises(ProverError, match="timed out"):
        run_prover_command(f"{sys.executable} {script}", _task(), 0.01)


def test_mine_once_rejects_local_verify_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = tmp_path / "prover.py"
    script.write_text(
        "import json, sys\n"
        "task = json.load(sys.stdin)\n"
        "print(json.dumps({'task_id': task['task_id'], 'proof_script': " + repr(_proof()) + "}))\n",
        encoding="utf-8",
    )

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=False, reason="compile_error")

    monkeypatch.setattr("lemma.miner.run_lean_verify", fake_verify)

    with pytest.raises(ProverError, match="local verification failed"):
        mine_once(_settings(tmp_path), prover_command=f"{sys.executable} {script}", registry=_registry())


def test_validator_scores_and_writes_alternate_corpus_rows(tmp_path: Path) -> None:
    task = _task()
    submissions = [
        build_submission(task, solver_hotkey="hk-a", proof_script=_proof("  trivial")),
        build_submission(task, solver_hotkey="hk-b", proof_script=_proof("  exact True.intro")),
        build_submission(task, solver_hotkey="hk-c", proof_script=_proof("  exact True.intro")),
    ]

    result = validate_once(
        _settings(tmp_path),
        submissions,
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        epoch=7,
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-a": 1}
    assert result.score.scores == {"hk-a": 1.0}
    assert [(row.solver_hotkey, row.rewarded) for row in result.corpus_rows] == [("hk-a", True), ("hk-b", False)]
    assert (tmp_path / "corpus" / "epoch-7.jsonl").exists()
    assert (tmp_path / "corpus" / "corpus-index.json").exists()
    score_events = (tmp_path / "operator" / "score-events.jsonl").read_text(encoding="utf-8")
    assert '"score":1.0' in score_events
    assert '"rewarded":false' in score_events


def test_validator_rejects_bad_target_hash_and_unsigned_live_submission(tmp_path: Path) -> None:
    task = _task()
    bad_target = build_submission(task, solver_hotkey="hk-a", proof_script=_proof()).model_copy(
        update={"target_sha256": "0" * 64}
    )
    unsigned = build_submission(task, solver_hotkey="hk-b", proof_script=_proof())

    result = validate_once(
        _settings(tmp_path),
        [bad_target, unsigned],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        require_signatures=True,
        no_set_weights=True,
    )

    assert result.verification_records == ()
    receipts = (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    assert "target_sha256 mismatch" in receipts
    assert "unsigned" in receipts


def test_validator_zero_credit_epoch_leaves_weights_unset(tmp_path: Path) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof())

    result = validate_once(
        _settings(tmp_path),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=False, reason="compile_error"),
        no_set_weights=False,
    )

    assert result.score.weights == {}
    assert result.weights_set is False
    assert result.corpus_rows == ()


def test_protocol_signing_payloads_are_stable() -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof(), created_at="2026-01-01T00:00:00Z")
    request = TaskRequest(validator_hotkey="vhk", epoch=1, tasks=(task,))
    response = ProofResponse(miner_hotkey="hk-a", submissions=(submission,))

    assert request.signing_payload() == request.model_copy().signing_payload()
    assert response.signing_payload() == response.model_copy().signing_payload()
    assert submission.signature_payload_sha256 in response.signing_payload()

"""Miner and validator one-shot workflow tests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.miner import ProverError, mine_once, run_prover_command
from lemma.protocol import ProofResponse, TaskRequest
from lemma.submissions import build_submission
from lemma.supply.mathlib_snapshot import candidates_from_jsonl
from lemma.supply.types import registry_tasks_from_candidates
from lemma.task_supply import make_task, write_registry
from lemma.tasks import TaskRegistry
from lemma.validator import ValidatorRunSummary, validate_once


def _task(task_id: str = "lemma.test.true", queue_depth: int = 0):
    return make_task(
        task_id=task_id,
        title="True task",
        theorem_name="test_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
        queue_depth=queue_depth,
    )


def _registry() -> TaskRegistry:
    return TaskRegistry(schema_version=1, tasks=(_task(),), sha256="0" * 64)


def _two_task_registry() -> TaskRegistry:
    return TaskRegistry(
        schema_version=1,
        tasks=(
            _task("lemma.test.active", queue_depth=0),
            _task("lemma.test.deep", queue_depth=2),
        ),
        sha256="0" * 64,
    )


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


def _proof_for(theorem_name: str, body: str = "  trivial") -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : True := by",
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

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

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
    run_summary = ValidatorRunSummary.model_validate_json(
        (tmp_path / "operator" / "validator-runs.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert result.summary == run_summary
    assert run_summary.registry_sha256 == "0" * 64
    assert run_summary.active_K == 1
    assert run_summary.frontier_depth == 0
    assert run_summary.verified_count == 3
    assert run_summary.accepted_unique_count == 2
    assert run_summary.rewarded_count == 1
    assert run_summary.score_event_count == 2
    assert run_summary.corpus_row_count == 2
    assert run_summary.unearned_share == 0.0
    assert run_summary.unearned_policy == "burn"
    assert run_summary.weights_set is False


def test_validator_no_epoch_uses_next_numbered_local_file(tmp_path: Path) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof())
    settings = _settings(tmp_path)

    validate_once(
        settings,
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        no_set_weights=True,
    )
    validate_once(
        settings,
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        no_set_weights=True,
    )

    assert (tmp_path / "corpus" / "epoch-000001.jsonl").exists()
    assert (tmp_path / "corpus" / "epoch-000002.jsonl").exists()
    assert not (tmp_path / "corpus" / "epoch-local.jsonl").exists()


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


def test_validator_zero_credit_epoch_routes_unearned_share(tmp_path: Path) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof())

    result = validate_once(
        _settings(tmp_path),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=False, reason="compile_error"),
        no_set_weights=False,
    )

    assert result.score.miner_weights == {}
    assert result.score.weights == {"burn_uid:0": 1.0}
    assert result.score.unearned_share == 1.0
    assert result.weights_set is False
    assert result.corpus_rows == ()
    run_summary = ValidatorRunSummary.model_validate_json(
        (tmp_path / "operator" / "validator-runs.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert run_summary.verified_count == 1
    assert run_summary.accepted_unique_count == 0
    assert run_summary.unearned_share == 1.0
    assert run_summary.weights_set is False


def test_validator_submits_weights_only_when_enabled(tmp_path: Path) -> None:
    submission = build_submission(_task(), solver_hotkey="hk-a", proof_script=_proof())
    calls: list[dict[str, float]] = []

    def fake_submit_weights(settings: LemmaSettings, weights: dict[str, float]) -> bool:  # noqa: ARG001
        calls.append(weights)
        return True

    result = validate_once(
        _settings(tmp_path).model_copy(update={"enable_set_weights": True}),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        no_set_weights=False,
        submit_weights=fake_submit_weights,
    )

    assert result.weights_set is True
    assert calls == [{"hk-a": 1.0}]


def test_validator_uses_deterministic_active_window_not_full_registry(tmp_path: Path) -> None:
    registry = _two_task_registry()
    active, deep = registry.tasks
    submissions = [
        build_submission(active, solver_hotkey="hk-a", proof_script=_proof("  trivial")),
        build_submission(deep, solver_hotkey="hk-b", proof_script=_proof("  exact True.intro")),
    ]

    result = validate_once(
        _settings(tmp_path),
        submissions,
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-a": 1}
    assert result.score.score_events[0].active_K == 1
    assert result.corpus_rows[0].active_K == 1
    assert result.corpus_rows[0].queue_depth == 0
    receipts = (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    assert "inactive_task" in receipts


def test_mathlib_snapshot_registry_loads_through_validator_path(tmp_path: Path) -> None:
    manifest = tmp_path / "mathlib-snapshot.jsonl"
    rows = [
        {
            "theorem_name": "registry_smoke_active_true",
            "type_expr": "True",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Smoke.lean",
            "source_license": "Apache-2.0",
            "source_line": 10,
            "queue_depth": 0,
        },
        {
            "theorem_name": "registry_smoke_deep_true",
            "type_expr": "True",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Smoke.lean",
            "source_license": "Apache-2.0",
            "source_line": 20,
            "queue_depth": 2,
        },
    ]
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    tasks = registry_tasks_from_candidates(candidates_from_jsonl(manifest), seed="validator-smoke", frontier_depth=0)
    registry_path = tmp_path / "registry.json"
    write_registry(tasks, registry_path)
    registry_sha256 = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    active_task = next(task for task in tasks if task.queue_depth == 0)
    deep_task = next(task for task in tasks if task.queue_depth == 2)
    submissions = [
        build_submission(active_task, solver_hotkey="hk-a", proof_script=_proof_for(active_task.theorem_name)),
        build_submission(deep_task, solver_hotkey="hk-b", proof_script=_proof_for(deep_task.theorem_name)),
    ]
    settings = _settings(tmp_path).model_copy(
        update={
            "task_registry_url": str(registry_path),
            "task_registry_sha256_expected": registry_sha256,
            "active_task_count": 2,
            "frontier_depth": 0,
            "active_queue_seed": "validator-smoke",
        }
    )

    result = validate_once(
        settings,
        submissions,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        epoch=9,
        tempo=3,
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-a": 1}
    assert result.score.miner_weights == {"hk-a": 1.0}
    assert result.score.unearned_share == 0.0
    assert result.score.score_events[0].active_K == 1
    assert result.corpus_rows[0].task_id == active_task.id
    assert result.corpus_rows[0].source_stream == "mathlib_snapshot"
    assert result.corpus_rows[0].source_ref.path == "Mathlib/Smoke.lean"
    assert result.corpus_rows[0].queue_position == 0
    assert result.corpus_rows[0].frontier_depth == 0
    assert result.corpus_rows[0].active_K == 1
    receipts = (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    assert "inactive_task" in receipts
    assert (tmp_path / "corpus" / "epoch-9.jsonl").exists()
    assert (tmp_path / "corpus" / "corpus-index.json").exists()


def test_protocol_signing_payloads_are_stable() -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof(), created_at="2026-01-01T00:00:00Z")
    request = TaskRequest(validator_hotkey="vhk", epoch=1, tasks=(task,))
    response = ProofResponse(miner_hotkey="hk-a", submissions=(submission,))

    assert request.signing_payload() == request.model_copy().signing_payload()
    assert response.signing_payload() == response.model_copy().signing_payload()
    assert submission.signature_payload_sha256 in response.signing_payload()

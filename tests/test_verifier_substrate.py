"""Verifier-substrate architecture tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.corpus.affine_export import affine_training_row
from lemma.corpus.dedup import DuplicateTracker
from lemma.corpus.export import export_rows
from lemma.corpus.rows import build_corpus_row_v2, normalized_artifact_hash
from lemma.lean.sandbox import VerifyResult
from lemma.scoring.registry import compute_domain_score
from lemma.submissions import build_submission, submission_v2_from_lean_submission
from lemma.task_supply import make_task
from lemma.tasks import upgrade_task_v1_to_v2
from lemma.verifiers.base import VerificationResult
from lemma.verifiers.lean import LeanVerifierAdapter
from lemma.verifiers.registry import get_verifier


def _task():
    return make_task(
        task_id="lemma.test.true",
        title="True task",
        theorem_name="test_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )


def _proof() -> str:
    return "import Mathlib\n\nnamespace Submission\n\ntheorem test_true : True := by\n  trivial\n\nend Submission\n"


def test_registry_returns_lean_and_fails_closed_for_unknown_domain() -> None:
    settings = LemmaSettings(_env_file=None)

    assert isinstance(get_verifier("lean", settings=settings), LeanVerifierAdapter)
    with pytest.raises(ValueError, match="Unknown domain_id"):
        get_verifier("bogus", settings=settings)


def test_verus_is_disabled_by_default() -> None:
    with pytest.raises(ValueError, match="disabled by default"):
        get_verifier("verus", settings=LemmaSettings(_env_file=None))


def test_lean_adapter_bridges_current_verify_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk", proof_script=_proof())

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok", proof_term_hash="term-hash")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    result = LeanVerifierAdapter(LemmaSettings(_env_file=None)).verify(task, submission)

    assert result.accepted is True
    assert result.domain_id == "lean"
    assert result.verifier_id == "lake-build"
    assert result.metrics["proof_term_hash"] == "term-hash"


def test_task_submission_and_corpus_row_v2_shapes(tmp_path: Path) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk", proof_script=_proof())
    task_v2 = upgrade_task_v1_to_v2(task.model_dump())
    submission_v2 = submission_v2_from_lean_submission(submission, task)
    row = build_corpus_row_v2(
        task,
        submission,
        VerifyResult(passed=True, reason="ok", proof_term_hash="term-hash"),
        validator_hotkey="vhk",
        block=7,
        timestamp="2026-01-01T00:00:00Z",
    )

    assert task_v2["domain_id"] == "lean"
    assert task_v2["verifier_id"] == "lake-build"
    assert task_v2["prompt"]["theorem_name"] == "test_true"
    assert submission_v2["artifact"]["proof"] == _proof()
    assert row.domain_id == "lean"
    assert row.verification["accepted"] is True
    assert row.metadata["normalized_artifact_hash"] == normalized_artifact_hash(row.accepted_artifact)

    metadata = export_rows([row], output=tmp_path / "lean_corpus.jsonl", fmt="jsonl")

    exported = (tmp_path / "lean_corpus.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(exported)["schema_version"] == 2
    assert metadata["num_rows"] == 1
    assert (tmp_path / "lean_corpus.jsonl.metadata.json").exists()
    assert affine_training_row(row)["target"] == _proof()


def test_duplicate_tracker_rejects_exact_artifact_duplicate() -> None:
    tracker = DuplicateTracker()
    artifact = {"proof": "by\n  trivial"}

    assert tracker.add("task-a", artifact) is True
    assert tracker.add("task-a", artifact) is False
    assert tracker.add("task-b", artifact) is True


def test_domain_score_registry_fails_closed() -> None:
    accepted = VerificationResult(accepted=True, verifier_id="lake-build", verifier_version="v1", domain_id="lean")

    assert compute_domain_score("lean", {}, {}, accepted) == 1
    with pytest.raises(ValueError, match="Unknown domain_id"):
        compute_domain_score("bogus", {}, {}, accepted)

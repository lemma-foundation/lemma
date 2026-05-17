"""Corpus row validation and replay."""

from __future__ import annotations

from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.corpus import build_corpus_row, replay_jsonl, validate_jsonl, write_jsonl
from lemma.lean.sandbox import VerifyResult
from lemma.submissions import build_submission
from lemma.tasks import LemmaTask


def _submission_stub() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem test_true : True := by",
            "  sorry",
            "",
            "end Submission",
            "",
        ]
    )


def _task() -> LemmaTask:
    return LemmaTask(
        id="lemma.test.true",
        title="True task",
        source_stream="human_curated",
        imports=("Mathlib",),
        theorem_name="test_true",
        type_expr="True",
        statement="theorem test_true : True := by\n  sorry",
        submission_stub=_submission_stub(),
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        policy="restricted_helpers",
    )


def _proof() -> str:
    return "import Mathlib\n\nnamespace Submission\n\ntheorem test_true : True := by\n  trivial\n\nend Submission\n"


def test_corpus_row_jsonl_validates(tmp_path: Path) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk1", proof_script=_proof(), created_at="2026-01-01T00:00:00Z")
    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok"),
        verified_at="2026-01-01T00:00:01Z",
    )
    path = tmp_path / "corpus.jsonl"

    write_jsonl([row], path)

    assert validate_jsonl(path) == 1


def test_corpus_replay_calls_verifier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    task = _task()
    proof = _proof()
    submission = build_submission(task, solver_hotkey="hk1", proof_script=proof, created_at="2026-01-01T00:00:00Z")
    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok"),
        verified_at="2026-01-01T00:00:01Z",
    )
    path = tmp_path / "corpus.jsonl"
    write_jsonl([row], path)
    calls: list[tuple[str, str]] = []

    def fake_verify(settings: LemmaSettings, **kwargs: object) -> VerifyResult:  # noqa: ARG001
        problem = kwargs["problem"]
        calls.append((problem.id, str(kwargs["proof_script"])))
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.corpus.run_lean_verify", fake_verify)

    results = replay_jsonl(LemmaSettings(_env_file=None), path)

    assert results[0].passed is True
    assert calls == [("lemma.test.true", proof)]

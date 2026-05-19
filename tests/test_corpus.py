"""Corpus row validation and replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.corpus import (
    build_corpus_index,
    build_corpus_row,
    replay_jsonl,
    validate_jsonl,
    write_benchmark_export,
    write_jsonl,
)
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
        task_version=1,
        title="True task",
        source_stream="human_curated",
        source_ref={"kind": "unit_test", "name": "pytest"},
        source_license="CC-BY-4.0",
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
        validator_hotkey="vhk1",
        rewarded=True,
        accepted_at="2026-01-01T00:00:01Z",
    )
    path = tmp_path / "corpus.jsonl"

    write_jsonl([row], path)

    assert validate_jsonl(path) == 1
    assert row.row_id
    assert row.task_version == 1
    assert row.validator_hotkey == "vhk1"
    assert row.rewarded is True


def test_corpus_replay_calls_verifier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    task = _task()
    proof = _proof()
    submission = build_submission(task, solver_hotkey="hk1", proof_script=proof, created_at="2026-01-01T00:00:00Z")
    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk1",
        rewarded=True,
        accepted_at="2026-01-01T00:00:01Z",
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


def test_corpus_index_and_metadata_sanitize_private_paths(tmp_path: Path) -> None:
    private_path = "/" + "Users/example/private"
    private_login = "ro" + "ot@example.test"
    private_ip = ".".join(["203", "0", "113", "10"])
    task = _task().model_copy(
        update={
            "metadata": {
                "local_path": private_path,
                "difficulty": "unit",
                "nested": {
                    "safe": "kept",
                    "endpoint": private_ip,
                    "items": ["public", private_path, {"note": "ok", "login": private_login}, "agent_state"],
                },
            }
        }
    )
    proof = _proof()
    submission = build_submission(task, solver_hotkey="hk1", proof_script=proof, created_at="2026-01-01T00:00:00Z")
    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk1",
        rewarded=False,
        accepted_at="2026-01-01T00:00:01Z",
    )
    path = tmp_path / "epoch-1.jsonl"

    write_jsonl([row], path)
    index = build_corpus_index(tmp_path)

    assert row.metadata == {
        "title": "True task",
        "difficulty": "unit",
        "nested": {"safe": "kept", "items": ["public", {"note": "ok"}]},
    }
    assert index["row_count"] == 1
    assert index["files"][0]["path"] == "epoch-1.jsonl"

    export_path = tmp_path / "exports" / "lemma-proofs.jsonl"
    write_benchmark_export(tmp_path, export_path)
    export_text = export_path.read_text(encoding="utf-8")
    assert private_path not in export_text
    assert private_login not in export_text
    assert private_ip not in export_text


def test_benchmark_export_writes_compact_records_and_index(tmp_path: Path) -> None:
    task = _task().model_copy(update={"queue_position": 3, "queue_depth": 1, "frontier_depth": 2})
    proof = _proof()
    first = build_corpus_row(
        task,
        build_submission(task, solver_hotkey="hk1", proof_script=proof, created_at="2026-01-01T00:00:00Z"),
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk1",
        rewarded=True,
        epoch=4,
        active_K=10,
        accepted_at="2026-01-01T00:00:01Z",
    )
    alternate = build_corpus_row(
        task,
        build_submission(task, solver_hotkey="hk2", proof_script=proof, created_at="2026-01-01T00:00:02Z"),
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk1",
        rewarded=False,
        epoch=4,
        active_K=10,
        accepted_at="2026-01-01T00:00:03Z",
    )
    write_jsonl([first, alternate], tmp_path / "corpus" / "epoch-4.jsonl")

    index = write_benchmark_export(
        tmp_path / "corpus",
        tmp_path / "export" / "proofs.jsonl",
        index_path=tmp_path / "export" / "index.json",
        rewarded_only=True,
    )

    records = [
        json.loads(line) for line in (tmp_path / "export" / "proofs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    saved_index = json.loads((tmp_path / "export" / "index.json").read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["task"]["id"] == "lemma.test.true"
    assert records[0]["task"]["queue_depth"] == 1
    assert records[0]["proof"]["script"] == proof
    assert records[0]["source"]["stream"] == "human_curated"
    assert records[0]["reward"]["active_K"] == 10
    assert index["format"] == "lemma-benchmark-export-v1"
    assert saved_index["row_count"] == 1
    assert saved_index["source_streams"] == {"human_curated": 1}
    assert saved_index["export"]["path"] == "proofs.jsonl"

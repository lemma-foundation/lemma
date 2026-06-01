from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lemma.corpus import build_corpus_row, write_jsonl
from lemma.lean.sandbox import VerifyResult
from lemma.submissions import build_submission
from lemma.task_supply import make_task

ROOT = Path(__file__).resolve().parents[1]


def _proof() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem smoke_true : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def test_prepare_proof_atlas_publish_regenerates_exports_and_docs(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    proof_dir = repo / "proofs" / "sn467" / "accepted"
    proof_dir.mkdir(parents=True)
    (repo / "README.md").write_text("- accepted proof rows: `0`\n", encoding="utf-8")
    (repo / "ATLAS_CARD.md").write_text(
        "The checked-in artifact set contains 0 accepted Lean proof rows,\n"
        "The validator accepted all 0 proofs with the pinned Lean verifier.\n",
        encoding="utf-8",
    )

    task = make_task(
        task_id="lemma.sn467.true_test",
        title="Smoke true",
        theorem_name="smoke_true",
        type_expr="True",
        source_stream="generated",
        source_name="pytest",
    )
    submission = build_submission(task, solver_hotkey="miner-test", proof_script=_proof())
    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="validator-test",
        rewarded=True,
    )
    write_jsonl([row], proof_dir / "epoch-000001.jsonl")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_proof_atlas_publish.py",
            "--repo",
            str(repo),
            "--netuid",
            "sn467",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads(result.stdout)
    proof_index = json.loads((repo / "proofs" / "sn467" / "index.json").read_text(encoding="utf-8"))
    benchmark_index = json.loads((repo / "exports" / "sn467" / "benchmark-index.json").read_text(encoding="utf-8"))
    assert summary["proof_rows"] == 1
    assert proof_index["row_count"] == 1
    assert benchmark_index["row_count"] == 1
    assert "- accepted proof rows: `1`" in (repo / "README.md").read_text(encoding="utf-8")
    assert "contains 1 accepted Lean proof rows" in (repo / "ATLAS_CARD.md").read_text(encoding="utf-8")

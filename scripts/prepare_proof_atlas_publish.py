#!/usr/bin/env python3
"""Regenerate Proof Atlas indexes and row-count docs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from lemma.corpus import validate_jsonl, write_benchmark_export, write_corpus_index


def _replace(path: Path, replacements: tuple[tuple[str, str], ...]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    updated = text
    for pattern, value in replacements:
        updated = re.sub(pattern, value, updated)
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def prepare(repo: Path, netuid: str) -> dict[str, object]:
    proof_dir = repo / "proofs" / netuid / "accepted"
    if not proof_dir.is_dir():
        raise SystemExit(f"missing accepted proof directory: {proof_dir}")
    files = sorted(proof_dir.glob("epoch-*.jsonl"))
    if not files:
        raise SystemExit(f"no epoch JSONL files under {proof_dir}")

    row_count = sum(validate_jsonl(path) for path in files)
    index_path = repo / "proofs" / netuid / "index.json"
    export_path = repo / "exports" / netuid / "lemma-proofs.jsonl"
    benchmark_index_path = repo / "exports" / netuid / "benchmark-index.json"

    write_corpus_index(proof_dir, index_path)
    benchmark_index = write_benchmark_export(proof_dir, export_path, index_path=benchmark_index_path)

    _replace(repo / "README.md", ((r"- accepted proof rows: `\d+`", f"- accepted proof rows: `{row_count}`"),))
    _replace(
        repo / "ATLAS_CARD.md",
        (
            (
                r"The checked-in artifact set contains .* accepted Lean proof rows,",
                f"The checked-in artifact set contains {row_count} accepted Lean proof rows,",
            ),
            (
                r"The validator accepted all .* proofs with",
                f"The validator accepted all {row_count} proofs with",
            ),
        ),
    )
    return {
        "benchmark_rows": benchmark_index["row_count"],
        "proof_rows": row_count,
        "export_sha256": benchmark_index["export"]["sha256"],
        "files": len(files),
        "netuid": netuid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Proof Atlas checkout to prepare")
    parser.add_argument("--netuid", default="sn467", help="Proof Atlas namespace, for example sn467")
    args = parser.parse_args()

    summary = prepare(args.repo.resolve(), args.netuid)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

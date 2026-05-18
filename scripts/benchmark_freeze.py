"""Freeze a replayable corpus slice into a benchmark JSONL.

This script is intentionally small: benchmark construction is downstream of
the corpus, and the launch repo should not imply a held-out benchmark product
before policy and contamination rules are settled.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lemma.corpus import read_jsonl


def freeze(input_path: Path, output_path: Path, *, limit: int | None = None) -> int:
    rows = read_jsonl(input_path)
    selected = rows[:limit] if limit is not None else rows
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(row.model_dump_json(exclude_none=True) + "\n" for row in selected),
        encoding="utf-8",
    )
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    count = freeze(args.input, args.output, limit=args.limit)
    print(f"wrote {count} rows to {args.output}")


if __name__ == "__main__":
    main()

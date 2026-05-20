#!/usr/bin/env python3
"""Export the public active-problem snapshot for lemmasub.net."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lemma.common.config import LemmaSettings  # noqa: E402
from lemma.current_problems import build_current_problems_snapshot, write_current_problems_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path; stdout when omitted")
    args = parser.parse_args()

    snapshot = build_current_problems_snapshot(LemmaSettings())
    if args.output is None:
        print(json.dumps(snapshot.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True))
    else:
        write_current_problems_snapshot(args.output, snapshot)
        print(json.dumps({"output": str(args.output), "task_count": snapshot.task_count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

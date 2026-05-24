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
from lemma.tasks import load_task_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path; stdout when omitted")
    parser.add_argument("--tempo", type=int, default=None, help="Override the active-window tempo")
    parser.add_argument("--registry-json", type=Path, default=None, help="Use a prebuilt task registry JSON file")
    parser.add_argument(
        "--registry-is-active",
        action="store_true",
        help="Treat --registry-json as the already-selected active task set",
    )
    parser.add_argument(
        "--skip-randomness-hashes",
        action="store_true",
        help="Do not resolve optional epoch-randomness hashes while exporting",
    )
    args = parser.parse_args()

    registry = load_task_registry(args.registry_json.read_bytes()) if args.registry_json is not None else None
    snapshot = build_current_problems_snapshot(
        LemmaSettings(),
        registry=registry,
        registry_is_active=args.registry_is_active,
        tempo=args.tempo,
        include_randomness_hashes=not args.skip_randomness_hashes,
    )
    if args.output is None:
        print(json.dumps(snapshot.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True))
    else:
        write_current_problems_snapshot(args.output, snapshot)
        print(json.dumps({"output": str(args.output), "task_count": snapshot.task_count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

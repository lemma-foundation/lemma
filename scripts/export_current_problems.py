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
from lemma.current_problems import (  # noqa: E402
    build_current_problems_snapshot,
    build_empty_current_problems_snapshot,
    write_current_problems_snapshot,
)
from lemma.tasks import load_task_registry  # noqa: E402
from lemma.validator import (  # noqa: E402
    active_registry_cache_path,
    current_active_tempo,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path; stdout when omitted")
    parser.add_argument("--tempo", type=int, default=None, help="Override the active-window tempo")
    parser.add_argument("--registry-json", type=Path, default=None, help="Use a prebuilt task registry JSON file")
    parser.add_argument(
        "--current-cache-dir",
        type=Path,
        default=None,
        help="Use only the registry cache file for the current active tempo",
    )
    parser.add_argument(
        "--empty-when-current-cache-missing",
        action="store_true",
        help="Write an empty current-tempo snapshot if --current-cache-dir has no current cache yet",
    )
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

    settings = LemmaSettings()
    tempo = args.tempo
    registry_json = args.registry_json
    registry_is_active = args.registry_is_active
    if args.current_cache_dir is not None:
        tempo = current_active_tempo(settings) if tempo is None else tempo
        cache_settings = settings.model_copy(update={"active_registry_cache_dir": args.current_cache_dir})
        registry_json = active_registry_cache_path(cache_settings, tempo=tempo)
        registry_is_active = True
        if registry_json is not None and not registry_json.is_file():
            if not args.empty_when_current_cache_missing:
                raise FileNotFoundError(registry_json)
            snapshot = build_empty_current_problems_snapshot(settings, tempo=tempo)
            if args.output is None:
                print(json.dumps(snapshot.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True))
            else:
                write_current_problems_snapshot(args.output, snapshot)
                print(json.dumps({"output": str(args.output), "task_count": snapshot.task_count}, sort_keys=True))
            return 0
    registry = load_task_registry(registry_json.read_bytes()) if registry_json is not None else None
    snapshot = build_current_problems_snapshot(
        settings,
        registry=registry,
        registry_is_active=registry_is_active,
        tempo=tempo,
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

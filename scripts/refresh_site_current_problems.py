#!/usr/bin/env python3
"""Refresh the lemmasub.net current-problems JSON from validator settings."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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
from lemma.validator import active_registry_cache_path, current_active_tempo  # noqa: E402

LEAK_PATTERN = re.compile(
    "AGENT" + r"[_ ]STATE|Agent " + "State|\\." + "env|" + "/" + "Users/|root" + "@|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|BEGIN (?:RSA|OPENSSH|PRIVATE)|"
    "api[_-]?key|to" + "ken|mne" + "monic|sec" + "ret|s" + "sh",
    re.IGNORECASE,
)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        capture_output=True,
    )


def _relative_to_repo(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def _staged_paths(repo: Path) -> list[str]:
    result = _git(repo, "diff", "--cached", "--name-only")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _tracked_changes(repo: Path) -> list[str]:
    result = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _sync_site_repo(repo: Path) -> None:
    changed = _tracked_changes(repo)
    if changed:
        raise SystemExit(f"site repo has local tracked changes: {', '.join(changed)}")
    _git(repo, "pull", "--ff-only")


def _assert_public_staged_diff(repo: Path) -> None:
    diff = _git(repo, "diff", "--cached").stdout
    if match := LEAK_PATTERN.search(diff):
        raise SystemExit(f"staged site diff matched leak pattern: {match.group(0)}")


def _commit_and_push(repo: Path, relative_output: str, *, message: str, push: bool) -> bool:
    existing_staged = _staged_paths(repo)
    if existing_staged:
        raise SystemExit(f"site repo already has staged changes: {', '.join(existing_staged)}")

    _git(repo, "add", relative_output)
    staged = _git(repo, "diff", "--cached", "--quiet", "--", relative_output, check=False)
    if staged.returncode == 0:
        return False
    _assert_public_staged_diff(repo)
    _git(repo, "commit", "-m", message, "--", relative_output)
    if push:
        _git(repo, "push")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-repo", type=Path, default=ROOT.parent / "lemmasub.net")
    parser.add_argument("--output", type=Path, default=Path("data/current-problems.json"))
    parser.add_argument("--commit", action="store_true", help="Commit the refreshed JSON in the site repo")
    parser.add_argument("--push", action="store_true", help="Push the site repo after committing")
    parser.add_argument("--message", default="Update current problems snapshot")
    parser.add_argument("--tempo", type=int, default=None, help="Override the active-window tempo")
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
        "--keep-output-when-current-cache-missing",
        action="store_true",
        help="Leave an existing output snapshot unchanged if --current-cache-dir has no current cache yet",
    )
    parser.add_argument(
        "--skip-randomness-hashes",
        action="store_true",
        help="Do not resolve optional epoch-randomness hashes while refreshing",
    )
    args = parser.parse_args()

    site_repo = args.site_repo.resolve()
    output = args.output if args.output.is_absolute() else site_repo / args.output
    if args.commit or args.push:
        _sync_site_repo(site_repo)
    settings = LemmaSettings()
    tempo = args.tempo
    if args.current_cache_dir is not None:
        tempo = current_active_tempo(settings) if tempo is None else tempo
        cache_settings = settings.model_copy(update={"active_registry_cache_dir": args.current_cache_dir})
        registry_json = active_registry_cache_path(cache_settings, tempo=tempo)
        if registry_json is not None and not registry_json.is_file():
            if args.keep_output_when_current_cache_missing and output.is_file():
                print(
                    json.dumps(
                        {
                            "committed": False,
                            "kept": True,
                            "output": str(output),
                            "pushed": False,
                            "reason": "current cache missing",
                            "task_count": None,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            if not args.empty_when_current_cache_missing:
                raise FileNotFoundError(registry_json)
            snapshot = build_empty_current_problems_snapshot(settings, tempo=tempo)
        else:
            snapshot = build_current_problems_snapshot(
                settings,
                registry=load_task_registry(registry_json.read_bytes()) if registry_json is not None else None,
                registry_is_active=True,
                tempo=tempo,
                include_randomness_hashes=not args.skip_randomness_hashes,
            )
    else:
        snapshot = build_current_problems_snapshot(
            settings,
            tempo=tempo,
            include_randomness_hashes=not args.skip_randomness_hashes,
        )
    write_current_problems_snapshot(output, snapshot)

    committed = False
    if args.commit or args.push:
        committed = _commit_and_push(
            site_repo,
            _relative_to_repo(site_repo, output),
            message=args.message,
            push=args.push,
        )

    print(
        json.dumps(
            {
                "committed": committed,
                "output": str(output),
                "pushed": bool(args.push and committed),
                "task_count": snapshot.task_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from lemma.current_problems import build_current_problems_snapshot, write_current_problems_snapshot  # noqa: E402

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
    args = parser.parse_args()

    site_repo = args.site_repo.resolve()
    output = args.output if args.output.is_absolute() else site_repo / args.output
    snapshot = build_current_problems_snapshot(LemmaSettings())
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

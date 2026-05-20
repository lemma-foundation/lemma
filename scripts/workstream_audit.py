#!/usr/bin/env python3
"""Run the fast Lemma workstream audit across code, site, and privacy checks."""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE_REPO = ROOT.parent / "lemmasub.net"


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    cwd: Path = ROOT


@dataclass(frozen=True)
class StepResult:
    name: str
    returncode: int
    elapsed_s: float

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def _quote(command: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _repo_argument(repo: Path) -> str:
    resolved = repo.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        pass
    try:
        return "../" + resolved.relative_to(ROOT.parent).as_posix()
    except ValueError:
        return str(repo)


def _pytest_command(profile: str) -> tuple[str, ...]:
    if profile == "full":
        return ("uv", "run", "pytest", "tests", "-q", "--ignore=tests/test_docker_golden.py")
    return (
        "uv",
        "run",
        "pytest",
        "tests/test_cli_training.py",
        "tests/test_miner_validator.py",
        "tests/test_leak_check.py",
        "tests/test_current_problems.py",
        "tests/test_operator_registry_flow.py",
        "tests/test_workstream_audit.py",
        "-q",
    )


def build_steps(profile: str, site_repo: Path = DEFAULT_SITE_REPO, *, skip_site: bool = False) -> list[Step]:
    steps = [
        Step("lemma git status", ("git", "status", "--short", "--branch")),
        Step("lemma diff whitespace", ("git", "diff", "--check")),
        Step("ruff", ("uv", "run", "ruff", "check", ".")),
        Step("mypy", ("uv", "run", "mypy", "lemma")),
    ]

    if profile == "full":
        steps.extend(
            [
                Step("bandit", ("uv", "run", "bandit", "-q", "-r", "lemma", "scripts", "-ll")),
                Step(
                    "pip-audit",
                    (
                        "uv",
                        "run",
                        "pip-audit",
                        "--ignore-vuln",
                        "PYSEC-2025-49",
                        "--ignore-vuln",
                        "PYSEC-2022-42969",
                    ),
                ),
            ]
        )

    leak_command = ["uv", "run", "python", "scripts/leak_check.py", "--repo", "."]
    if not skip_site and site_repo.exists():
        leak_command.extend(["--repo", _repo_argument(site_repo)])
    steps.append(Step("privacy leak check", tuple(leak_command)))
    steps.append(Step("pytest", _pytest_command(profile)))

    if not skip_site and site_repo.exists():
        steps.extend(
            [
                Step("site git status", ("git", "status", "--short", "--branch"), site_repo),
                Step(
                    "site current-problems json",
                    ("python3", "-m", "json.tool", "data/current-problems.json", "/dev/null"),
                    site_repo,
                ),
            ]
        )
        if shutil.which("node"):
            steps.append(Step("site javascript syntax", ("node", "--check", "assets/site.js"), site_repo))
    return steps


def run_step(step: Step) -> StepResult:
    started = time.monotonic()
    print(f"\n== {step.name}")
    print(f"$ {_quote(step.command)}")
    completed = subprocess.run(step.command, cwd=step.cwd, text=True, capture_output=True, check=False)  # noqa: S603
    elapsed = time.monotonic() - started
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    print(f"-> {'PASS' if completed.returncode == 0 else 'FAIL'} ({elapsed:.1f}s)")
    return StepResult(step.name, completed.returncode, elapsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("quick", "full"), default="quick")
    parser.add_argument("--site-repo", type=Path, default=DEFAULT_SITE_REPO)
    parser.add_argument("--skip-site", action="store_true")
    parser.add_argument("--keep-going", action="store_true", help="Run every step even after a failure.")
    args = parser.parse_args(argv)

    results: list[StepResult] = []
    for step in build_steps(args.profile, args.site_repo, skip_site=args.skip_site):
        result = run_step(step)
        results.append(result)
        if not result.passed and not args.keep_going:
            break

    failed = [result for result in results if not result.passed]
    print("\n== summary")
    print(f"passed={len(results) - len(failed)} failed={len(failed)} profile={args.profile}")
    for result in failed:
        print(f"failed: {result.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

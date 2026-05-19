#!/usr/bin/env python3
"""Fail if tracked public files contain private operator data."""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_CONTENT = {".gitignore", ".env.example", "scripts/leak_check.py"}
PRIVATE_PATH_PARTS = ("wallets/", ".bittensor/", ".ssh/")
SKIP_LOCAL_USERNAMES = {"root", "runner"}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)  # noqa: S603, S607


def _tracked_files() -> list[str]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)  # noqa: S603, S607
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def _private_path_label(path: str) -> str | None:
    name = Path(path).name
    lowered = path.lower()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "env-path"
    if name == ".envrc" or (name.startswith(".envrc.") and name != ".envrc.example"):
        return "env-path"
    if re.search(r"agent[-_ ]?state", lowered):
        return "agent-state-path"
    if any(part in lowered for part in PRIVATE_PATH_PARTS):
        return "private-operator-path"
    return None


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    patterns = [
        ("agent-state", re.compile("AGENT" + r"[_ ]STATE|Agent State|" + "agent" + r"_state")),
        ("local-user-path", re.compile(re.escape("/" + "Users/"))),
        ("root-ssh", re.compile(r"root" + re.escape("@"))),
        ("ip-address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
        ("private-key", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
        ("wallet-mnemonic", re.compile(r"\bmnemonic\b", re.IGNORECASE)),
        ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ]
    user = getpass.getuser()
    if user and user not in SKIP_LOCAL_USERNAMES and not os.environ.get("GITHUB_ACTIONS"):
        patterns.append(("local-username", re.compile(rf"\b{re.escape(user)}\b")))
    credential_names = "|".join(("api[_-]?" + "key", "to" + "ken", "sec" + "ret"))
    patterns.extend(
        [
            ("github-credential", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
            ("slack-credential", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b")),
            ("aws-access-credential", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
            ("google-credential", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
            (
                "credential-assignment",
                re.compile(
                    rf"\b(?:{credential_names})\b\s*[:=]\s*['\"]?[A-Za-z0-9][A-Za-z0-9_./+=:-]{{23,}}",
                    re.IGNORECASE,
                ),
            ),
        ]
    )
    return patterns


def main() -> int:
    findings: list[str] = []
    for path in _tracked_files():
        if label := _private_path_label(path):
            findings.append(f"{label}:{path}")
            continue
        if path in SKIP_CONTENT:
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in _patterns():
            if pattern.search(text):
                findings.append(f"{label}:{path}")

    staged = _git("diff", "--cached", "--name-only")
    for path in staged.splitlines():
        if label := _private_path_label(path):
            findings.append(f"staged-{label}:{path}")

    if findings:
        print("Leak check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("Leak check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail if tracked public files contain private operator data."""

from __future__ import annotations

import getpass
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_CONTENT = {".gitignore", ".env.example", "scripts/leak_check.py"}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)  # noqa: S603, S607


def _tracked_files() -> list[str]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)  # noqa: S603, S607
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    user = re.escape(getpass.getuser())
    return [
        ("agent-state", re.compile(r"AGENT[_ ]STATE|Agent State")),
        ("local-user-path", re.compile(re.escape("/" + "Users/"))),
        ("root-ssh", re.compile(r"root" + re.escape("@"))),
        ("ip-address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
        ("private-key", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
        ("wallet-mnemonic", re.compile(r"\bmnemonic\b", re.IGNORECASE)),
        ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
        ("local-username", re.compile(rf"\b{user}\b") if user else re.compile(r"a^")),
    ]


def main() -> int:
    findings: list[str] = []
    for path in _tracked_files():
        name = Path(path).name
        if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
            findings.append(f"path:{path}")
            continue
        if "AGENT" + "_STATE" in path:
            findings.append(f"path:{path}")
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
        name = Path(path).name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example") or "AGENT" + "_STATE" in path:
            findings.append(f"staged-path:{path}")

    if findings:
        print("Leak check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("Leak check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

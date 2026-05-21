#!/usr/bin/env python3
"""Serve the public active-problem snapshot as JSON."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lemma.common.config import LemmaSettings  # noqa: E402
from lemma.current_problem_server import CurrentProblemService, run_server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--tempo", type=int, default=None, help="Override the active-window tempo")
    args = parser.parse_args()

    run_server(args.host, args.port, CurrentProblemService(LemmaSettings(), tempo=args.tempo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Append-only local JSONL stores for operator artifacts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def append_jsonl(path: Path, rows: Sequence[BaseModel | dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            if isinstance(row, BaseModel):
                f.write(row.model_dump_json(exclude_none=True))
            else:
                f.write(json.dumps(row, sort_keys=True))
            f.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows

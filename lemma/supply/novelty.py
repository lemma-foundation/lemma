"""Public novelty cache for procedural task generation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NOVELTY_CACHE_VERSION = "lemma-novelty-cache-v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class NoveltyCache:
    statement_hashes: frozenset[str]

    def contains(self, statement_hash: str) -> bool:
        return statement_hash.lower().removeprefix("sha256:") in self.statement_hashes

    def metadata(self) -> dict[str, object]:
        return {
            "novelty_cache_version": NOVELTY_CACHE_VERSION,
            "novelty_cache_entries": len(self.statement_hashes),
            "novelty_cache_sha256": self.sha256,
        }

    @property
    def sha256(self) -> str:
        return _hash_json({"version": NOVELTY_CACHE_VERSION, "statement_hashes": sorted(self.statement_hashes)})


def novelty_cache_from_hashes(statement_hashes: tuple[str, ...]) -> NoveltyCache:
    normalized = tuple(value for item in statement_hashes if (value := _normalize_hash(item)) is not None)
    return NoveltyCache(frozenset(normalized))


def empty_novelty_cache() -> NoveltyCache:
    return NoveltyCache(frozenset())


def read_novelty_cache(path: Path) -> NoveltyCache:
    hashes: set[str] = set()
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{no}: invalid novelty cache row: {e}") from e
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{no}: novelty cache row must be an object")
        digest = _row_statement_hash(row)
        if digest is None:
            raise ValueError(f"{path}:{no}: novelty cache row lacks statement hash or type_expr")
        hashes.add(digest)
    return NoveltyCache(frozenset(sorted(hashes)))


def statement_hash(type_expr: str) -> str:
    return hashlib.sha256(_normalize_statement(type_expr).encode("utf-8")).hexdigest()


def _row_statement_hash(row: dict[str, Any]) -> str | None:
    for key in ("statement_hash", "canonical_statement_hash"):
        value = _normalize_hash(row.get(key))
        if value:
            return value
    type_expr = row.get("type_expr")
    if isinstance(type_expr, str) and type_expr.strip():
        return statement_hash(type_expr)
    value = _normalize_hash(row.get("target_sha256"))
    if value:
        return value
    return None


def _normalize_hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower().removeprefix("sha256:")
    return raw if _HEX64.fullmatch(raw) else None


def _normalize_statement(type_expr: str) -> str:
    return " ".join(type_expr.strip().split())


def _hash_json(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()

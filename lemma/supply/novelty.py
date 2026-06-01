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
    novelty_family_hashes: frozenset[str] = frozenset()

    def contains(self, statement_hash: str) -> bool:
        return statement_hash.lower().removeprefix("sha256:") in self.statement_hashes

    def contains_family(self, novelty_family_hash: str) -> bool:
        return novelty_family_hash.lower().removeprefix("sha256:") in self.novelty_family_hashes

    def metadata(self) -> dict[str, object]:
        return {
            "novelty_cache_version": NOVELTY_CACHE_VERSION,
            "novelty_cache_entries": len(self.statement_hashes),
            "novelty_family_cache_entries": len(self.novelty_family_hashes),
            "novelty_cache_sha256": self.sha256,
        }

    @property
    def sha256(self) -> str:
        return _hash_json(
            {
                "version": NOVELTY_CACHE_VERSION,
                "novelty_family_hashes": sorted(self.novelty_family_hashes),
                "statement_hashes": sorted(self.statement_hashes),
            }
        )


def novelty_cache_from_hashes(
    statement_hashes: tuple[str, ...],
    *,
    novelty_family_hashes: tuple[str, ...] = (),
) -> NoveltyCache:
    normalized = tuple(value for item in statement_hashes if (value := _normalize_hash(item)) is not None)
    normalized_families = tuple(
        value for item in novelty_family_hashes if (value := _normalize_hash(item)) is not None
    )
    return NoveltyCache(frozenset(normalized), frozenset(normalized_families))


def empty_novelty_cache() -> NoveltyCache:
    return NoveltyCache(frozenset())


def read_novelty_cache(path: Path, *, strict_statement_hash_rows: bool = False) -> NoveltyCache:
    hashes: set[str] = set()
    family_hashes: set[str] = set()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path}: novelty cache path invalid")
    raw = path.read_bytes()
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as e:
        raise ValueError(f"{path}: invalid novelty cache UTF-8") from e
    rows: list[dict[str, str]] = []
    for no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{no}: invalid novelty cache row: {e}") from e
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{no}: novelty cache row must be an object")
        if strict_statement_hash_rows:
            if set(row) not in ({"statement_hash"}, {"novelty_family_hash"}):
                raise ValueError(f"{path}:{no}: novelty cache row schema invalid")
            key = "statement_hash" if "statement_hash" in row else "novelty_family_hash"
            if not isinstance(row.get(key), str):
                raise ValueError(f"{path}:{no}: novelty cache row schema invalid")
            if not _HEX64.fullmatch(row[key]):
                raise ValueError(f"{path}:{no}: novelty cache {key} invalid")
            canonical_row = {key: row[key]}
            if line.encode("utf-8") != _canonical_json(canonical_row):
                raise ValueError(f"{path}:{no}: novelty cache row noncanonical")
            rows.append(canonical_row)
            if key == "novelty_family_hash":
                if row[key] in family_hashes:
                    raise ValueError(f"{path}:{no}: novelty cache novelty_family_hash duplicated")
                family_hashes.add(row[key])
                continue
        digest = _row_statement_hash(row)
        if digest is None:
            family_digest = _row_family_hash(row)
            if family_digest is None:
                raise ValueError(
                    f"{path}:{no}: novelty cache row lacks statement hash, type_expr, or novelty family hash"
                )
            if family_digest in family_hashes:
                raise ValueError(f"{path}:{no}: novelty cache novelty_family_hash duplicated")
            family_hashes.add(family_digest)
            continue
        if strict_statement_hash_rows and digest in hashes:
            raise ValueError(f"{path}:{no}: novelty cache statement_hash duplicated")
        hashes.add(digest)
    if strict_statement_hash_rows:
        canonical = b"".join(
            _canonical_json(row) + b"\n"
            for row in sorted(
                rows,
                key=lambda item: (
                    "novelty_family_hash" in item,
                    item.get("statement_hash", item.get("novelty_family_hash", "")),
                ),
            )
        )
        if raw != canonical:
            raise ValueError(f"{path}: novelty cache JSONL noncanonical")
    return NoveltyCache(frozenset(sorted(hashes)), frozenset(sorted(family_hashes)))


def statement_hash(type_expr: str) -> str:
    return hashlib.sha256(_normalize_statement(type_expr).encode("utf-8")).hexdigest()


def _row_statement_hash(row: dict[str, Any]) -> str | None:
    for key in ("statement_hash", "canonical_statement_hash", "canonical_hash", "kernel_canonical_hash"):
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


def _row_family_hash(row: dict[str, Any]) -> str | None:
    return _normalize_hash(row.get("novelty_family_hash"))


def _normalize_hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower().removeprefix("sha256:")
    return raw if _HEX64.fullmatch(raw) else None


def _normalize_statement(type_expr: str) -> str:
    return " ".join(type_expr.strip().split())


def _hash_json(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

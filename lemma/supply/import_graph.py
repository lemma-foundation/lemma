"""Public import graph used by procedural slot-weight receipts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

IMPORT_GRAPH_VERSION = "lemma-import-graph-v1"
_LEAN_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


class ImportGraphRow(BaseModel):
    """One Lean module and its direct imports."""

    model_config = ConfigDict(extra="forbid")

    module: str
    imports: tuple[str, ...] = ()

    @field_validator("module")
    @classmethod
    def _validate_module(cls, value: str) -> str:
        return _clean_module(value, "module")

    @field_validator("imports")
    @classmethod
    def _validate_imports(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_clean_module(item, "imports") for item in value))


@dataclass(frozen=True)
class ImportGraphResolution:
    roots: tuple[str, ...]
    direct_imports: tuple[str, ...]
    transitive_imports: tuple[str, ...]
    missing_roots: tuple[str, ...]
    max_depth: int
    transitive_hash: str


@dataclass(frozen=True)
class ImportGraph:
    edges: dict[str, tuple[str, ...]]

    @property
    def entry_count(self) -> int:
        return len(self.edges)

    @property
    def sha256(self) -> str:
        rows = [{"module": module, "imports": imports} for module, imports in sorted(self.edges.items())]
        return _hash_json({"version": IMPORT_GRAPH_VERSION, "rows": rows})

    def metadata(self) -> dict[str, object]:
        return {
            "import_graph_version": IMPORT_GRAPH_VERSION,
            "import_graph_entries": self.entry_count,
            "import_graph_sha256": self.sha256,
        }

    def resolve(self, imports: Iterable[str]) -> ImportGraphResolution:
        roots = tuple(dict.fromkeys(_clean_module(item, "imports") for item in imports if str(item).strip()))
        direct = tuple(sorted({dep for root in roots for dep in self.edges.get(root, ())}))
        missing = tuple(root for root in roots if root not in self.edges)
        transitive = tuple(sorted(self._reachable(roots)))
        depth = max((self._depth(root, seen=frozenset()) for root in roots), default=0)
        transitive_hash = _hash_json(
            {
                "version": IMPORT_GRAPH_VERSION,
                "roots": roots,
                "transitive_imports": transitive,
            }
        )
        return ImportGraphResolution(
            roots=roots,
            direct_imports=direct,
            transitive_imports=transitive,
            missing_roots=missing,
            max_depth=depth,
            transitive_hash=transitive_hash,
        )

    def _reachable(self, roots: tuple[str, ...]) -> set[str]:
        out: set[str] = set()
        stack = list(roots)
        root_set = set(roots)
        while stack:
            module = stack.pop()
            for dep in self.edges.get(module, ()):
                if dep in out or dep in root_set:
                    continue
                out.add(dep)
                stack.append(dep)
        return out

    def _depth(self, module: str, *, seen: frozenset[str]) -> int:
        if module in seen:
            return 0
        deps = self.edges.get(module, ())
        if not deps:
            return 0
        next_seen = seen | {module}
        return 1 + max((self._depth(dep, seen=next_seen) for dep in deps), default=0)


def empty_import_graph() -> ImportGraph:
    return ImportGraph({})


def import_graph_from_rows(rows: Iterable[ImportGraphRow]) -> ImportGraph:
    edges = {row.module: row.imports for row in rows}
    return ImportGraph(dict(sorted(edges.items())))


def read_import_graph(path: Path) -> ImportGraph:
    rows: list[ImportGraphRow] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(ImportGraphRow.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{path}:{no}: invalid import graph row: {e}") from e
    return import_graph_from_rows(rows)


def write_import_graph_jsonl(rows: Iterable[ImportGraphRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


def extract_import_graph_rows(
    mathlib_root: Path,
    includes: tuple[str, ...] = ("Mathlib/**/*.lean",),
) -> tuple[ImportGraphRow, ...]:
    root = mathlib_root.resolve()
    rows: list[ImportGraphRow] = []
    for path in sorted({item for pattern in includes for item in root.glob(pattern) if item.is_file()}):
        if path.suffix != ".lean":
            continue
        module = path.relative_to(root).as_posix().removesuffix(".lean").replace("/", ".")
        rows.append(ImportGraphRow(module=module, imports=_imports_from_text(path.read_text(encoding="utf-8"))))
    return tuple(sorted(rows, key=lambda row: row.module))


def _imports_from_text(text: str) -> tuple[str, ...]:
    imports: list[str] = []
    in_block_comment = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        code, in_block_comment = _strip_comments(raw, in_block_comment)
        stripped = code.strip()
        if not stripped.startswith("import "):
            continue
        imports.extend(item for item in stripped.removeprefix("import ").split() if _LEAN_MODULE.fullmatch(item))
    return tuple(dict.fromkeys(imports))


def _strip_comments(line: str, in_block_comment: bool) -> tuple[str, bool]:
    out = ""
    i = 0
    while i < len(line):
        if in_block_comment:
            end = line.find("-/", i)
            if end == -1:
                return out, True
            i = end + 2
            in_block_comment = False
            continue
        if line.startswith("/-", i):
            in_block_comment = True
            i += 2
            continue
        if line.startswith("--", i):
            break
        out += line[i]
        i += 1
    return out, in_block_comment


def _clean_module(value: str, field: str) -> str:
    text = str(value).strip()
    if not text or not _LEAN_MODULE.fullmatch(text):
        raise ValueError(f"{field} must contain Lean module names")
    return text


def _hash_json(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()

"""Off-chain Mathlib snapshot extraction.

The validator consumes pinned JSON artifacts. This module is deliberately a
small exporter for trusted operator use, not part of the live scoring path.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lemma.supply.mathlib_snapshot import MathlibSnapshotRow

_DECL_RE = re.compile(
    r"^(?:protected\s+)?(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)(?=\s|:|\(|\{|\[)(.*)$"
)
_BOUNDARY_RE = re.compile(r"^(?:protected\s+)?(?:theorem|lemma|def|abbrev|instance)\b")
_LEAN_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_ATTR_RE = re.compile(r"^(?:@\[[^\]]+\]\s*)+")
_UNIVERSE_APP_RE = re.compile(r"\.\{[^{}]*\}")
_SORT_LEVEL_RE = re.compile(r"\b(Type|Sort)\s+(?:\([^()]*\)|[A-Za-z_][A-Za-z0-9_]*(?:\s*\+\s*\d+)?)")
_HARD_TOPICS = {"Algebra", "Analysis", "CategoryTheory", "Geometry", "MeasureTheory", "Topology"}


@dataclass(frozen=True)
class ExtractConfig:
    mathlib_root: Path
    includes: tuple[str, ...] = ("Mathlib/**/*.lean",)
    limit: int | None = None
    depth0_limit: int | None = None
    depth1_limit: int | None = None
    depth2_limit: int | None = None
    mathlib_rev: str | None = None
    source_license: str = "Apache-2.0"
    elaborate_types: bool = False
    lake_root: Path | None = None


@dataclass(frozen=True)
class _Line:
    no: int
    code: str
    namespace: tuple[str, ...]


def extract_snapshot_rows(config: ExtractConfig) -> tuple[MathlibSnapshotRow, ...]:
    root = config.mathlib_root.resolve()
    rev = config.mathlib_rev or mathlib_revision(root)
    rows: list[MathlibSnapshotRow] = []
    for path in _source_files(root, config.includes):
        rows.extend(_rows_from_file(root, path, rev, config.source_license))
    rows = sorted(
        rows,
        key=lambda row: (row.queue_depth, row.topic or "", row.source_path, row.source_line or 0, row.theorem_name),
    )
    rows = _apply_depth_limits(rows, config.depth0_limit, config.depth1_limit, config.depth2_limit)
    if config.limit is not None:
        rows = rows[: config.limit]
    if config.elaborate_types:
        rows = _elaborate_types(rows, config.lake_root or _default_lake_root(root))
    return tuple(rows)


def write_snapshot_jsonl(rows: Iterable[MathlibSnapshotRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(row.model_dump_json(exclude_none=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def mathlib_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise ValueError(f"could not resolve Mathlib git revision from {root}") from e
    rev = result.stdout.strip()
    if not rev:
        raise ValueError(f"could not resolve Mathlib git revision from {root}")
    return rev


def _source_files(root: Path, includes: tuple[str, ...]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for pattern in includes or ("Mathlib/**/*.lean",):
        files.update(path for path in root.glob(pattern) if path.is_file() and path.suffix == ".lean")
    return tuple(sorted(files))


def _rows_from_file(root: Path, path: Path, rev: str, source_license: str) -> list[MathlibSnapshotRow]:
    rel = path.relative_to(root).as_posix()
    topic, subtopic = _topic_from_path(rel)
    module = rel.removesuffix(".lean").replace("/", ".")
    lines = _code_lines(path.read_text(encoding="utf-8"))
    boundaries = [i for i, line in enumerate(lines) if _boundary_match(line.code)]
    rows: list[MathlibSnapshotRow] = []
    for offset, start in enumerate(boundaries):
        line = lines[start]
        if _decl_match(line.code) is None:
            continue
        end = boundaries[offset + 1] if offset + 1 < len(boundaries) else len(lines)
        block = "\n".join(item.code for item in lines[start:end])
        parsed = _parse_decl(line, block)
        if parsed is None:
            continue
        name, type_expr, proof = parsed
        theorem_name = name if "." in name else ".".join((*line.namespace, name))
        if theorem_name.startswith("_root_.") or not _LEAN_NAME_RE.fullmatch(theorem_name):
            continue
        difficulty_score = _difficulty_score(type_expr, block, topic)
        rows.append(
            MathlibSnapshotRow(
                theorem_name=theorem_name,
                type_expr=type_expr,
                imports=(module,),
                mathlib_rev=rev,
                source_path=rel,
                source_line=line.no,
                source_license=source_license,
                proof_sha256=hashlib.sha256(proof.encode("utf-8")).hexdigest() if proof.strip() else None,
                queue_depth=_queue_depth(difficulty_score),
                topic=topic,
                subtopic=subtopic,
                difficulty_score=difficulty_score,
            )
        )
    return rows


def _code_lines(text: str) -> list[_Line]:
    lines: list[_Line] = []
    namespace: list[str] = []
    in_block_comment = False
    for no, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        code, in_block_comment = _strip_comments(raw, in_block_comment)
        stripped = code.strip()
        if not stripped:
            continue
        stripped = _ATTR_RE.sub("", stripped).strip()
        lines.append(_Line(no=no, code=stripped, namespace=tuple(namespace)))
        if stripped.startswith("namespace "):
            namespace.extend(
                part for part in stripped.removeprefix("namespace ").split() if _LEAN_NAME_RE.fullmatch(part)
            )
        elif stripped.startswith("end "):
            name = stripped.removeprefix("end ").split()[0]
            if namespace and namespace[-1] == name:
                namespace.pop()
    return lines


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


def _decl_match(code: str) -> re.Match[str] | None:
    if code.startswith("private "):
        return None
    return _DECL_RE.match(_ATTR_RE.sub("", code).strip())


def _boundary_match(code: str) -> re.Match[str] | None:
    if code.startswith("private "):
        return None
    return _BOUNDARY_RE.match(_ATTR_RE.sub("", code).strip())


def _parse_decl(line: _Line, block: str) -> tuple[str, str, str] | None:
    match = _decl_match(line.code)
    if match is None:
        return None
    proof_at = _find_def_eq(block)
    if proof_at is None:
        return None
    header = " ".join(block[:proof_at].split())
    proof = block[proof_at + 2 :]
    match = _DECL_RE.match(_ATTR_RE.sub("", header).strip())
    if match is None:
        return None
    name = match.group(1)
    rest = match.group(2).strip()
    colon = _find_top_level_colon(rest)
    if colon is None:
        return None
    binders = rest[:colon].strip()
    target = rest[colon + 1 :].strip()
    if not target or " where " in target:
        return None
    type_expr = f"∀ {binders}, {target}" if binders else target
    return name, type_expr, proof


def _default_lake_root(mathlib_root: Path) -> Path:
    packages = mathlib_root.parent
    if packages.name == "packages" and packages.parent.name == ".lake":
        return packages.parent.parent
    return mathlib_root


def _elaborate_types(rows: list[MathlibSnapshotRow], lake_root: Path) -> list[MathlibSnapshotRow]:
    checks = "\n".join(f"#check {row.theorem_name}" for row in rows)
    with tempfile.NamedTemporaryFile("w", suffix=".lean", encoding="utf-8", delete=False) as handle:
        handle.write("import Mathlib\n")
        handle.write(checks)
        handle.write("\n")
        check_path = Path(handle.name)
    try:
        result = subprocess.run(
            ["lake", "env", "lean", str(check_path)],
            cwd=lake_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        detail = str(e)
        raise ValueError(f"could not elaborate Mathlib theorem types: {detail}") from e
    finally:
        check_path.unlink(missing_ok=True)
    types = _parse_check_output(result.stdout, [row.theorem_name for row in rows])
    if result.returncode != 0 and not types:
        lines = "\n".join(part for part in (result.stderr, result.stdout) if part).strip().splitlines()
        detail = " | ".join(lines[-6:]) if lines else f"lean exited {result.returncode}"
        raise ValueError(f"could not elaborate Mathlib theorem types: {detail}")
    return [row.model_copy(update={"type_expr": types[row.theorem_name]}) for row in rows if row.theorem_name in types]


def _parse_check_output(output: str, names: list[str]) -> dict[str, str]:
    wanted = set(names)
    types: dict[str, str] = {}
    lines = output.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        name = next((candidate for candidate in wanted - types.keys() if line.startswith(candidate)), None)
        if name is None:
            index += 1
            continue
        block = [line.strip()]
        index += 1
        while index < len(lines) and not any(lines[index].startswith(candidate) for candidate in wanted - types.keys()):
            if not lines[index].startswith((" ", "\t")) or "error(" in lines[index]:
                break
            block.append(lines[index].strip())
            index += 1
        parsed = _type_from_check_line(name, " ".join(block))
        if parsed:
            types[name] = parsed
    return types


def _type_from_check_line(name: str, line: str) -> str | None:
    tail = line.removeprefix(name).strip()
    if tail.startswith(".{"):
        end = tail.find("}")
        if end == -1:
            return None
        tail = tail[end + 1 :].strip()
    colon = _find_top_level_colon(tail)
    if colon is None:
        return None
    binders = tail[:colon].strip()
    target = tail[colon + 1 :].strip()
    if not target:
        return None
    type_expr = f"∀ {binders}, {target}" if binders else target
    return _erase_universe_levels(type_expr)


def _erase_universe_levels(type_expr: str) -> str:
    without_apps = _UNIVERSE_APP_RE.sub("", type_expr)
    return _SORT_LEVEL_RE.sub(lambda match: f"{match.group(1)} _", without_apps)


def _find_def_eq(text: str) -> int | None:
    depth = 0
    for i, ch in enumerate(text[:-1]):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and text[i + 1] == "=" and depth == 0:
            return i
    return None


def _find_top_level_colon(text: str) -> int | None:
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            return i
    return None


def _topic_from_path(rel: str) -> tuple[str, str | None]:
    parts = rel.removesuffix(".lean").split("/")
    if len(parts) < 2 or parts[0] != "Mathlib":
        return "Unknown", None
    return parts[1], parts[2] if len(parts) > 2 else None


def _difficulty_score(type_expr: str, block: str, topic: str) -> int:
    score = 0
    score += min(3, type_expr.count("∀") + type_expr.count("->") + type_expr.count("→"))
    score += min(2, type_expr.count("["))
    score += 1 if len(type_expr) > 120 else 0
    score += 1 if len(type_expr) > 240 else 0
    score += 1 if topic in _HARD_TOPICS else 0
    lines = max(1, block.count("\n") + 1)
    score += 1 if lines > 8 else 0
    score += 1 if lines > 25 else 0
    return score


def _queue_depth(score: int) -> int:
    if score <= 2:
        return 0
    if score <= 5:
        return 1
    return 2


def _apply_depth_limits(
    rows: list[MathlibSnapshotRow],
    depth0_limit: int | None,
    depth1_limit: int | None,
    depth2_limit: int | None,
) -> list[MathlibSnapshotRow]:
    limits = {0: depth0_limit, 1: depth1_limit, 2: depth2_limit}
    seen = {0: 0, 1: 0, 2: 0}
    out: list[MathlibSnapshotRow] = []
    for row in rows:
        depth = min(2, row.queue_depth)
        limit = limits[depth]
        if limit is not None and seen[depth] >= limit:
            continue
        seen[depth] += 1
        out.append(row)
    return out

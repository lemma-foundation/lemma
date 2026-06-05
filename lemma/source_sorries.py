"""Build patch tasks from public source-sorry records."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lemma.tasks import LemmaTask, SourceRef, target_type_sha256

_HOLE_RE = re.compile(r"\b(sorry|admit)\b")


def build_patch_task_from_sorrydb_record(
    record: Mapping[str, Any],
    *,
    source_root: Path,
    theorem_name: str,
    type_expr: str,
    source_license: str,
    mathlib_rev: str,
    task_id: str | None = None,
    title: str | None = None,
    lean_toolchain: str | None = None,
    reproduction_command: str = "lake build",
    allowed_imports: Sequence[str] = (),
    queue_depth: int = 0,
    source_value: str = "medium",
) -> LemmaTask:
    """Turn one SorryDB row plus a local pinned checkout into a patch task."""
    repo = _mapping(record.get("repo"), "repo")
    location = _mapping(record.get("location"), "location")
    metadata = record.get("metadata")

    rel_path = _safe_relative_path(_required_str(location, "path"))
    source_path = source_root / rel_path
    if not source_path.is_file():
        raise ValueError(f"source file does not exist in checkout: {rel_path}")
    source = source_path.read_text(encoding="utf-8")
    if _HOLE_RE.search(_code_text(source)) is None:
        raise ValueError(f"source file has no sorry/admit hole: {rel_path}")
    decl_header = _target_decl_header(source, theorem_name)
    if decl_header is None:
        raise ValueError(f"target declaration not found: {theorem_name}")

    remote = _required_str(repo, "remote")
    commit = _required_str(repo, "commit")
    row_id = str(record.get("id") or hashlib.sha256(f"{remote}:{commit}:{rel_path}".encode()).hexdigest()[:12])
    lean_version = str(repo.get("lean_version") or "").strip()
    resolved_toolchain = lean_toolchain or _lean_toolchain_from_version(lean_version)
    source_ref = SourceRef(
        kind="sorrydb",
        name=_source_name(remote),
        url=remote,
        commit=commit,
        path=rel_path,
    )
    return LemmaTask(
        id=task_id or f"lemma.sorrydb.{_safe_id(row_id)}",
        task_version=1,
        title=title or f"SorryDB {theorem_name}",
        task_format="patch",
        task_class="source_sorry",
        source_value=source_value,  # type: ignore[arg-type]
        source_stream="sorrydb",
        source_ref=source_ref,
        source_license=source_license,
        imports=(),
        allowed_files=(rel_path,),
        allowed_imports=tuple(allowed_imports),
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=source,
        submission_stub=source,
        lean_toolchain=resolved_toolchain,
        mathlib_rev=mathlib_rev,
        policy="restricted_helpers",
        target_type_sha256=target_type_sha256(type_expr),
        environment_sha256=source_environment_sha256(source_root),
        reproduction_command=reproduction_command,
        queue_depth=queue_depth,
        difficulty_band="medium",
        metadata={
            "sorrydb_id": row_id,
            "source_file_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "source_start_line": _int_or_none(location.get("start_line")),
            "source_start_column": _int_or_none(location.get("start_column")),
            "source_decl_header": decl_header,
            "repo_branch": str(repo.get("branch") or ""),
            "repo_lean_version": lean_version,
            "source_debug_url": _debug_url(record),
            **_public_record_metadata(metadata),
        },
    )


def source_environment_sha256(source_root: Path) -> str | None:
    """Hash the public Lean project files that pin a source checkout environment."""
    candidates = ("lean-toolchain", "lakefile.lean", "lakefile.toml", "lake-manifest.json")
    parts: list[bytes] = []
    for rel in candidates:
        path = source_root / rel
        if path.is_file():
            raw = path.read_bytes()
            parts.extend([rel.encode("utf-8"), b"\0", hashlib.sha256(raw).hexdigest().encode("ascii"), b"\n"])
    if not parts:
        return None
    return hashlib.sha256(b"".join(parts)).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"SorryDB record missing object field: {field}")
    return value


def _required_str(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"SorryDB record missing string field: {field}")
    return value.strip()


def _safe_relative_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    parts = cleaned.split("/")
    if not cleaned or cleaned.startswith("/") or ".." in parts or "" in parts:
        raise ValueError(f"unsafe source path: {path}")
    return cleaned


def _code_text(source: str) -> str:
    lines = []
    for raw in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        code = raw.split("--", 1)[0].strip()
        if code:
            lines.append(code)
    return "\n".join(lines)


def _target_decl_header(source: str, theorem_name: str) -> str | None:
    short_name = theorem_name.rsplit(".", 1)[-1]
    prefixes = (f"theorem {short_name} ", f"lemma {short_name} ")
    for line in _code_text(source).splitlines():
        if line.startswith(prefixes) and ":=" in line:
            return " ".join(line.split(":=", 1)[0].split())
    return None


def _lean_toolchain_from_version(lean_version: str) -> str:
    if lean_version.startswith("leanprover/lean4:"):
        return lean_version
    if lean_version:
        return f"leanprover/lean4:{lean_version}"
    return "leanprover/lean4:v4.30.0-rc2"


def _debug_url(record: Mapping[str, Any]) -> str:
    debug_info = record.get("debug_info")
    if isinstance(debug_info, Mapping) and isinstance(debug_info.get("url"), str):
        return str(debug_info["url"])
    return ""


def _source_name(remote: str) -> str:
    trimmed = remote.removesuffix(".git").rstrip("/")
    return trimmed.rsplit("/", 2)[-2] + "/" + trimmed.rsplit("/", 1)[-1] if "/" in trimmed else trimmed


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or hashlib.sha256(value.encode()).hexdigest()[:12]


def _int_or_none(value: Any) -> int | None:
    return value if type(value) is int else None


def _public_record_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = ("blame_date", "inclusion_date")
    return {key: value[key] for key in allowed if isinstance(value.get(key), str)}

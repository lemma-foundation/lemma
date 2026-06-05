"""Minimal validator for source-pinned Lean patch tasks."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lemma.lean.rejection import RejectionClass, hole_class
from lemma.tasks import LemmaTask

#: Patch validation reports the canonical miner-facing rejection vocabulary.
PatchValidationReason = RejectionClass

_FORBIDDEN_PREFIXES = (
    "axiom ",
    "constant ",
    "unsafe ",
    "extern ",
    "opaque ",
    "set_option debug.skipKernelTC",
    "set_option pp.all",
    "run_cmd ",
    "initialize ",
    "builtin_initialize ",
)


@dataclass(frozen=True)
class PatchValidationResult:
    accepted: bool
    reason: PatchValidationReason
    detail: str = ""
    changed_files: tuple[str, ...] = ()
    stdout_tail: str = ""
    stderr_tail: str = ""


def validate_patch_task(
    task: LemmaTask,
    *,
    source_root: Path,
    patch_text: str,
    run_reproduction: bool = True,
    timeout_s: int = 120,
) -> PatchValidationResult:
    """Apply and statically validate a patch task in a clean source copy."""
    if task.task_format != "patch":
        return PatchValidationResult(False, "validator_internal_error", detail="unsupported_task_format")
    if not source_root.is_dir():
        return PatchValidationResult(
            False, "validator_internal_error", detail="invalid_source_root", stderr_tail=str(source_root)
        )

    changed_files = _changed_files(patch_text)
    if not changed_files:
        return PatchValidationResult(False, "patch_apply_failed", detail="empty_patch")
    unsafe = [path for path in changed_files if not _safe_manifest_path(path)]
    if unsafe:
        return PatchValidationResult(
            False, "patch_apply_failed", detail="unsafe_patch_path", changed_files=changed_files, stderr_tail=unsafe[0]
        )
    allowed = set(task.allowed_files)
    disallowed = [path for path in changed_files if path not in allowed]
    if disallowed:
        return PatchValidationResult(
            False,
            "patch_apply_failed",
            detail="disallowed_file",
            changed_files=changed_files,
            stderr_tail=disallowed[0],
        )

    with tempfile.TemporaryDirectory(prefix="lemma-patch-") as tmp:
        work = Path(tmp) / "source"
        shutil.copytree(source_root, work, ignore=shutil.ignore_patterns(".git", ".lake"))
        patch_path = Path(tmp) / "submission.patch"
        patch_path.write_text(patch_text, encoding="utf-8")

        applied = _apply_patch(work, patch_path, timeout_s)
        if applied.returncode != 0:
            return PatchValidationResult(
                False,
                "patch_apply_failed",
                changed_files=changed_files,
                stdout_tail=_tail(applied.stdout),
                stderr_tail=_tail(applied.stderr),
            )

        static = _validate_static(task, source_root=source_root, work=work, changed_files=changed_files)
        if not static.accepted:
            return static

        if run_reproduction:
            command = shlex.split(task.reproduction_command)
            if not command:
                return PatchValidationResult(
                    False,
                    "validator_internal_error",
                    detail="missing_reproduction_command",
                    changed_files=changed_files,
                )
            try:
                reproduced = _run_reproduction_command(command, cwd=work, timeout_s=timeout_s)
            except subprocess.TimeoutExpired:
                return PatchValidationResult(False, "timeout", changed_files=changed_files)
            except OSError as e:
                return PatchValidationResult(
                    False,
                    "validator_internal_error",
                    detail="reproduction_command_unavailable",
                    changed_files=changed_files,
                    stderr_tail=str(e),
                )
            if reproduced.returncode != 0:
                return PatchValidationResult(
                    False,
                    "lean_compile_error",
                    detail="reproduction_failed",
                    changed_files=changed_files,
                    stdout_tail=_tail(reproduced.stdout),
                    stderr_tail=_tail(reproduced.stderr),
                )

    return PatchValidationResult(True, "ok", changed_files=changed_files)


def _changed_files(patch_text: str) -> tuple[str, ...]:
    files: list[str] = []
    for raw in patch_text.splitlines():
        path = ""
        if raw.startswith("diff --git "):
            parts = raw.split()
            if len(parts) >= 4:
                path = _strip_patch_prefix(parts[3])
        elif raw.startswith("+++ "):
            path = _strip_patch_prefix(raw.split(maxsplit=1)[1])
        if path and path != "/dev/null" and path not in files:
            files.append(path)
    return tuple(files)


def _strip_patch_prefix(path: str) -> str:
    path = path.strip().strip('"')
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _safe_manifest_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return bool(path) and not path.startswith("/") and ".." not in parts and "" not in parts


def _apply_patch(work: Path, patch_path: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "apply", "--unidiff-zero", str(patch_path)],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _validate_static(
    task: LemmaTask,
    *,
    source_root: Path,
    work: Path,
    changed_files: tuple[str, ...],
) -> PatchValidationResult:
    old_decl = _target_decl(source_root, changed_files, task.theorem_name)
    new_decl = _target_decl(work, changed_files, task.theorem_name)
    if old_decl is None or new_decl is None:
        return PatchValidationResult(
            False, "target_type_changed", detail="target_statement_missing", changed_files=changed_files
        )
    if old_decl != new_decl:
        return PatchValidationResult(
            False, "target_type_changed", detail="target_statement_changed", changed_files=changed_files
        )

    for rel in changed_files:
        original_imports = _imports(source_root / rel)
        original_forbidden = _forbidden_lines(source_root / rel)
        allowed_imports = set(task.allowed_imports) | original_imports
        source = (work / rel).read_text(encoding="utf-8")
        code_lines = _code_lines(source)
        for line in code_lines:
            if line.startswith("import "):
                imported = line.removeprefix("import ").strip()
                if imported not in allowed_imports:
                    return PatchValidationResult(
                        False, "forbidden_import", detail=imported, changed_files=changed_files
                    )
            if line.startswith(_FORBIDDEN_PREFIXES) and line not in original_forbidden:
                return PatchValidationResult(
                    False, "new_axiom_detected", detail=line.split()[0], changed_files=changed_files
                )
        hole = hole_class("\n".join(code_lines))
        if hole is not None:
            return PatchValidationResult(False, hole, changed_files=changed_files)
    return PatchValidationResult(True, "ok", changed_files=changed_files)


def _target_decl(root: Path, files: tuple[str, ...], theorem_name: str) -> str | None:
    short_name = theorem_name.rsplit(".", 1)[-1]
    for rel in files:
        path = root / rel
        if not path.is_file():
            continue
        parts: list[str] = []
        for line in _code_lines(path.read_text(encoding="utf-8")):
            if not parts:
                if not _starts_decl(line, short_name):
                    continue
                parts.append(line)
            else:
                parts.append(line)
            joined = " ".join(" ".join(parts).split())
            if ":=" in joined:
                return joined.split(":=", 1)[0].strip()
    return None


def _starts_decl(line: str, short_name: str) -> bool:
    prefixes = (f"theorem {short_name}", f"lemma {short_name}")
    return any(line == prefix or line.startswith(prefix + " ") for prefix in prefixes)


def _imports(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        line.removeprefix("import ").strip()
        for line in _code_lines(path.read_text(encoding="utf-8"))
        if line.startswith("import ")
    }


def _forbidden_lines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line for line in _code_lines(path.read_text(encoding="utf-8")) if line.startswith(_FORBIDDEN_PREFIXES)}


def _code_lines(source: str) -> list[str]:
    lines = []
    for raw in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        code = raw.split("--", 1)[0].strip()
        if code:
            lines.append(code)
    return lines


def _run_reproduction_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_s: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)


def _tail(value: str | None, limit: int = 4000) -> str:
    return (value or "")[-limit:]

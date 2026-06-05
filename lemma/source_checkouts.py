"""Local source-checkout paths for public source-pinned tasks."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lemma.tasks import LemmaTask, SourceRef

CheckoutAction = Literal["ready", "cloned", "updated"]


@dataclass(frozen=True)
class SourceCheckoutResult:
    path: Path
    commit: str
    action: CheckoutAction


def source_checkout_path(root: Path, source_ref: SourceRef) -> Path | None:
    """Return the expected local checkout path for a public source ref."""
    commit = (source_ref.commit or "").strip()
    if not commit:
        return None
    return root / _segment(source_ref.kind) / _segment(source_ref.name) / _segment(commit)


def source_checkout_status(root: Path | None, tasks: tuple[LemmaTask, ...]) -> tuple[bool, str]:
    """Summarize checkout readiness for active patch tasks."""
    patch_tasks = tuple(task for task in tasks if task.task_format == "patch")
    if not patch_tasks:
        return True, "no active patch tasks"
    if root is None:
        return False, f"LEMMA_SOURCE_CHECKOUT_ROOT missing for {len(patch_tasks)} active patch tasks"

    missing: list[str] = []
    unresolved: list[str] = []
    for task in patch_tasks:
        path = source_checkout_path(root, task.source_ref)
        if path is None:
            unresolved.append(task.id)
            continue
        if not path.is_dir():
            missing.append(f"{task.id}:{path}")
    if unresolved:
        return False, "missing source_ref.commit for " + ", ".join(unresolved[:3])
    if missing:
        return False, "missing checkout " + "; ".join(missing[:3])
    return True, f"{len(patch_tasks)} active patch checkouts ready"


def materialize_source_checkout(
    root: Path,
    source_ref: SourceRef,
    *,
    timeout_s: int = 300,
) -> SourceCheckoutResult:
    """Clone/fetch one public source checkout and verify the pinned commit."""
    path = source_checkout_path(root, source_ref)
    if path is None:
        raise ValueError("source_ref.commit is required")
    remote = (source_ref.url or "").strip()
    if not remote:
        raise ValueError("source_ref.url is required")
    commit = source_ref.commit or ""

    if path.exists():
        if not path.is_dir() or path.is_symlink():
            raise ValueError(f"source checkout path is not a directory: {path}")
        if _current_commit(path, timeout_s=timeout_s) == commit:
            return SourceCheckoutResult(path=path, commit=commit, action="ready")
        _run_git(["git", "-C", str(path), "fetch", "--depth", "1", "origin", commit], timeout_s=timeout_s)
        action: CheckoutAction = "updated"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["git", "clone", "--no-checkout", remote, str(path)], timeout_s=timeout_s)
        _run_git(["git", "-C", str(path), "fetch", "--depth", "1", "origin", commit], timeout_s=timeout_s)
        action = "cloned"

    _run_git(["git", "-C", str(path), "checkout", "--detach", commit], timeout_s=timeout_s)
    current = _current_commit(path, timeout_s=timeout_s)
    if current != commit:
        raise RuntimeError(f"source checkout commit mismatch: got {current or '<none>'}, expected {commit}")
    return SourceCheckoutResult(path=path, commit=commit, action=action)


def materialize_source_checkouts(
    root: Path,
    tasks: tuple[LemmaTask, ...],
    *,
    timeout_s: int = 300,
) -> tuple[tuple[LemmaTask, SourceCheckoutResult], ...]:
    """Clone/fetch source checkouts for every patch task in a registry."""
    return tuple(
        (task, materialize_source_checkout(root, task.source_ref, timeout_s=timeout_s))
        for task in tasks
        if task.task_format == "patch"
    )


def _segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "__", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError("source checkout path segment is empty")
    return cleaned[:160]


def _current_commit(path: Path, *, timeout_s: int) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_git(command: list[str], *, timeout_s: int) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{' '.join(command)} failed" + (f": {detail[-4000:]}" if detail else ""))

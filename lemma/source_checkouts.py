"""Local source-checkout paths for public source-pinned tasks."""

from __future__ import annotations

import re
from pathlib import Path

from lemma.tasks import LemmaTask, SourceRef


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


def _segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "__", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError("source checkout path segment is empty")
    return cleaned[:160]

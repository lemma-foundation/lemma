"""Local source-checkout paths for public source-pinned tasks."""

from __future__ import annotations

import re
from pathlib import Path

from lemma.tasks import SourceRef


def source_checkout_path(root: Path, source_ref: SourceRef) -> Path | None:
    """Return the expected local checkout path for a public source ref."""
    commit = (source_ref.commit or "").strip()
    if not commit:
        return None
    return root / _segment(source_ref.kind) / _segment(source_ref.name) / _segment(commit)


def _segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "__", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError("source checkout path segment is empty")
    return cleaned[:160]

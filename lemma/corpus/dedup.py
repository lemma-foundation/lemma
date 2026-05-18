"""Duplicate detection for verifier-grounded artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field

from lemma.corpus.rows import normalized_artifact_hash, task_hash


@dataclass
class DuplicateTracker:
    """Track exact duplicate artifacts by task."""

    seen_by_task: dict[str, set[str]] = field(default_factory=dict)

    def add(self, task_id: str, artifact: dict[str, object]) -> bool:
        digest = normalized_artifact_hash(artifact)
        seen = self.seen_by_task.setdefault(task_id, set())
        if digest in seen:
            return False
        seen.add(digest)
        return True


def duplicate_metadata(
    prompt: dict[str, object],
    artifact: dict[str, object],
    *,
    near_duplicate: bool = False,
) -> dict[str, object]:
    return {
        "task_hash": task_hash(prompt),
        "normalized_artifact_hash": normalized_artifact_hash(artifact),
        "near_duplicate": near_duplicate,
    }

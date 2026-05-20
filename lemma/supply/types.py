"""Typed task-candidate surfaces for off-chain supply generators."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lemma.task_supply import DEFAULT_MATHLIB_REV, DEFAULT_TOOLCHAIN, deterministic_queue
from lemma.tasks import LemmaTask, SourceRef, SourceStream


class TaskCandidate(BaseModel):
    """A generated Lean task before paid activation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    source_stream: SourceStream
    source_ref: SourceRef
    source_license: str = "CC-BY-4.0"
    imports: tuple[str, ...] = ("Mathlib",)
    theorem_name: str
    type_expr: str
    statement: str
    submission_stub: str
    lean_toolchain: str = DEFAULT_TOOLCHAIN
    mathlib_rev: str = DEFAULT_MATHLIB_REV
    policy: str = "restricted_helpers"
    queue_depth: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_task(self, *, queue_position: int | None = None, frontier_depth: int | None = None) -> LemmaTask:
        return LemmaTask(
            id=self.id,
            title=self.title,
            source_stream=self.source_stream,
            source_ref=self.source_ref,
            source_license=self.source_license,
            imports=self.imports,
            theorem_name=self.theorem_name,
            type_expr=self.type_expr,
            statement=self.statement,
            submission_stub=self.submission_stub,
            lean_toolchain=self.lean_toolchain,
            mathlib_rev=self.mathlib_rev,
            policy=self.policy,
            queue_position=queue_position,
            queue_depth=self.queue_depth,
            frontier_depth=frontier_depth,
            metadata=self.metadata,
        )


def lean_stub(theorem_name: str, type_expr: str, imports: tuple[str, ...] = ("Mathlib",)) -> str:
    import_lines = [f"import {module}" for module in imports]
    return "\n".join(
        [
            *import_lines,
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : {type_expr} := by",
            "  sorry",
            "",
            "end Submission",
            "",
        ]
    )


def fixture_candidate(
    *,
    slug: str,
    source_stream: SourceStream,
    source_name: str,
    theorem_name: str,
    type_expr: str,
    queue_depth: int,
    metadata: dict[str, Any] | None = None,
) -> TaskCandidate:
    return TaskCandidate(
        id=f"lemma.{source_stream}.{slug}",
        title=slug.replace("_", " ").title(),
        source_stream=source_stream,
        source_ref=SourceRef(kind="fixture", name=source_name),
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=f"theorem {theorem_name} : {type_expr} := by\n  sorry",
        submission_stub=lean_stub(theorem_name, type_expr),
        queue_depth=queue_depth,
        metadata=metadata or {},
    )


def registry_tasks_from_candidates(
    candidates: tuple[TaskCandidate, ...],
    *,
    seed: str,
    frontier_depth: int | None = None,
) -> tuple[LemmaTask, ...]:
    queued = deterministic_queue(
        (candidate.to_task(frontier_depth=frontier_depth) for candidate in candidates),
        seed=seed,
    )
    return tuple(task.model_copy(update={"queue_position": index}) for index, task in enumerate(queued))

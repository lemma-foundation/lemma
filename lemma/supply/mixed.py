"""Launch-gated mixed task supply builder."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lemma.license import license_state_for, paid_license_allowed
from lemma.supply.triviality_gate import label_from_baseline
from lemma.supply.types import TaskCandidate
from lemma.task_supply import deterministic_queue
from lemma.tasks import LemmaTask, SourceStream

PAID_MIXED_STREAMS: frozenset[SourceStream] = frozenset(
    {
        "mathlib_snapshot",
        "mathlib_perturbation",
        "state_graph",
        "auto_formalized",
        "conjecture_generated",
        "hard_target_variant",
    }
)


@dataclass(frozen=True)
class RejectedCandidate:
    id: str
    reason: str


@dataclass(frozen=True)
class MixedRegistryBuild:
    tasks: tuple[LemmaTask, ...]
    rejected: tuple[RejectedCandidate, ...]


def candidates_from_jsonl(path: Path) -> tuple[TaskCandidate, ...]:
    out: list[TaskCandidate] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(TaskCandidate.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{path}:{no}: invalid task candidate: {e}") from e
    return tuple(out)


def build_mixed_registry_tasks(
    candidates: tuple[TaskCandidate, ...],
    *,
    seed: str,
    frontier_depth: int | None = None,
) -> MixedRegistryBuild:
    tasks: list[LemmaTask] = []
    rejected: list[RejectedCandidate] = []
    for candidate in candidates:
        reason = _candidate_rejection_reason(candidate)
        if reason:
            rejected.append(RejectedCandidate(candidate.id, reason))
            continue
        task = candidate.to_task(frontier_depth=frontier_depth)
        baseline_solved = bool(candidate.metadata.get("baseline_solved"))
        triviality = label_from_baseline(solved_by_baseline=baseline_solved, queue_depth=candidate.queue_depth)
        difficulty = cast(Any, _difficulty_band(candidate.queue_depth))
        tasks.append(
            task.model_copy(
                update={
                    "activation_status": "paid",
                    "triviality_status": triviality,
                    "difficulty_band": difficulty,
                    "metadata": {
                        **task.metadata,
                        "activation_status": "paid",
                        "license_state": license_state_for(
                            task.source_license,
                            str(task.metadata.get("license_state") or ""),
                        ),
                    },
                }
            )
        )
    queued = tuple(
        task.model_copy(update={"queue_position": index})
        for index, task in enumerate(deterministic_queue(tasks, seed=seed, max_frontier_depth=frontier_depth))
    )
    return MixedRegistryBuild(tasks=queued, rejected=tuple(rejected))


def _candidate_rejection_reason(candidate: TaskCandidate) -> str:
    if candidate.source_stream not in PAID_MIXED_STREAMS:
        return f"unsupported_source_stream:{candidate.source_stream}"
    metadata = candidate.metadata
    status = str(metadata.get("activation_status") or "paid")
    if status != "paid":
        return f"activation_status:{status}"
    if bool(metadata.get("held_out_benchmark")):
        return "held_out_benchmark"
    license_state = license_state_for(candidate.source_license, str(metadata.get("license_state") or ""))
    if not paid_license_allowed(license_state):
        return f"license_state:{license_state}"
    if not bool(metadata.get("typechecked")):
        return "typecheck_not_confirmed"
    if not bool(metadata.get("triviality_checked")):
        return "triviality_not_checked"
    if bool(metadata.get("baseline_solved")):
        return "baseline_solved"
    if "near_duplicate_score" not in metadata:
        return "near_duplicate_score_missing"
    near_duplicate_score = _float_metadata(metadata, "near_duplicate_score")
    if near_duplicate_score >= 0.9:
        return "near_duplicate"
    if not candidate.mathlib_rev.strip() or candidate.mathlib_rev == "unknown":
        return "missing_mathlib_rev"
    if not candidate.lean_toolchain.strip():
        return "missing_lean_toolchain"
    return ""


def _float_metadata(metadata: dict[str, Any], key: str) -> float:
    try:
        return float(metadata.get(key) or 0.0)
    except (TypeError, ValueError):
        return 1.0


def _difficulty_band(queue_depth: int) -> str:
    if queue_depth <= 1:
        return "easy"
    if queue_depth <= 3:
        return "medium"
    if queue_depth <= 6:
        return "hard"
    return "frontier"

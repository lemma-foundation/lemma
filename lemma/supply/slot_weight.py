"""Deterministic paid-slot weight receipts for procedural tasks."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lemma.supply.types import TaskCandidate
from lemma.tasks import LemmaTask

SLOT_WEIGHT_VERSION = "lemma-slot-weight-v1"


@dataclass(frozen=True)
class SlotWeightReceipt:
    weight: float
    basis_points: int
    inputs: dict[str, object]

    def metadata(self) -> dict[str, object]:
        return {
            "slot_weight": self.weight,
            "slot_weight_version": SLOT_WEIGHT_VERSION,
            "slot_weight_basis_points": self.basis_points,
            "slot_weight_inputs": self.inputs,
        }


def slot_weight_receipt_for_candidate(candidate: TaskCandidate) -> SlotWeightReceipt:
    return _slot_weight_receipt(
        source_stream=candidate.source_stream,
        imports=candidate.imports,
        queue_depth=candidate.queue_depth,
        metadata=candidate.metadata,
    )


def slot_weight_receipt_for_task(task: LemmaTask) -> SlotWeightReceipt:
    return _slot_weight_receipt(
        source_stream=task.source_stream,
        imports=task.imports,
        queue_depth=task.queue_depth,
        metadata=task.metadata,
    )


def _slot_weight_receipt(
    *,
    source_stream: str,
    imports: Sequence[str],
    queue_depth: int,
    metadata: Mapping[str, Any],
) -> SlotWeightReceipt:
    unique_imports = tuple(dict.fromkeys(str(item).strip() for item in imports if str(item).strip()))
    import_breadth = len(unique_imports)
    import_depth = max((_module_depth(module) for module in unique_imports), default=0)
    direct_count = _nonnegative_int(metadata.get("direct_dependency_count"), default=import_breadth)
    dependency_depth = max(
        queue_depth,
        _nonnegative_int(metadata.get("dependency_depth"), default=queue_depth),
    )
    mutation_depth = _nonnegative_int(metadata.get("mutation_depth"), default=0)
    lemma_rows_used = _lemma_rows_used(source_stream, metadata)
    citation_weight = _bounded_float(metadata.get("citation_weight"), default=1.0, cap=100.0)
    transitive_hash = str(metadata.get("transitive_dependency_hash") or "")

    basis_points = (
        1000
        + 200 * min(mutation_depth, 10)
        + 150 * min(dependency_depth, 100)
        + 50 * min(direct_count, 200)
        + 25 * min(import_breadth, 200)
        + 25 * min(import_depth, 50)
        + 100 * min(len(lemma_rows_used), 50)
        + min(1000, int(round(citation_weight * 10)))
    )
    inputs: dict[str, object] = {
        "import_breadth": import_breadth,
        "import_depth": import_depth,
        "direct_dependency_count": direct_count,
        "dependency_depth": dependency_depth,
        "mutation_depth": mutation_depth,
        "lemma_rows_used_count": len(lemma_rows_used),
        "citation_weight": citation_weight,
        "transitive_dependency_hash": transitive_hash,
    }
    return SlotWeightReceipt(weight=round(basis_points / 1000.0, 6), basis_points=basis_points, inputs=inputs)


def _module_depth(module: str) -> int:
    return len([part for part in module.split(".") if part])


def _nonnegative_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _bounded_float(value: object, *, default: float, cap: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        try:
            number = float(value)
        except ValueError:
            return default
    else:
        return default
    if not math.isfinite(number) or number < 0:
        return default
    return min(cap, number)


def _lemma_rows_used(source_stream: str, metadata: Mapping[str, Any]) -> tuple[str, ...]:
    rows = metadata.get("lemma_rows_used")
    if isinstance(rows, (list, tuple)):
        out = tuple(str(item) for item in rows if str(item).strip())
    else:
        out = ()
    substrate_row = str(metadata.get("substrate_row_id") or "").strip()
    if source_stream == "lemma_substrate" and substrate_row and substrate_row not in out:
        out = (*out, substrate_row)
    return out

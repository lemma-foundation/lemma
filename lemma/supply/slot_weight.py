"""Deterministic paid-slot weight receipts for procedural tasks."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lemma.supply.import_graph import IMPORT_GRAPH_VERSION, ImportGraph
from lemma.supply.types import TaskCandidate
from lemma.tasks import LemmaTask

SLOT_WEIGHT_VERSION = "lemma-slot-weight-v2"


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


def slot_weight_receipt_for_candidate(
    candidate: TaskCandidate,
    *,
    import_graph: ImportGraph | None = None,
) -> SlotWeightReceipt:
    return _slot_weight_receipt(
        source_stream=candidate.source_stream,
        imports=candidate.imports,
        queue_depth=candidate.queue_depth,
        metadata=candidate.metadata,
        import_graph=import_graph,
    )


def slot_weight_receipt_for_task(task: LemmaTask, *, import_graph: ImportGraph | None = None) -> SlotWeightReceipt:
    return _slot_weight_receipt(
        source_stream=task.source_stream,
        imports=task.imports,
        queue_depth=task.queue_depth,
        metadata=task.metadata,
        import_graph=import_graph,
    )


def _slot_weight_receipt(
    *,
    source_stream: str,
    imports: Sequence[str],
    queue_depth: int,
    metadata: Mapping[str, Any],
    import_graph: ImportGraph | None,
) -> SlotWeightReceipt:
    stored = _stored_graph_inputs(metadata)
    if import_graph is None and stored is not None:
        return _receipt_from_inputs(stored)

    unique_imports = tuple(dict.fromkeys(str(item).strip() for item in imports if str(item).strip()))
    if import_graph is not None and import_graph.entry_count > 0:
        resolved = import_graph.resolve(unique_imports)
        import_breadth = len(resolved.roots)
        import_depth = resolved.max_depth
        direct_count = len(resolved.direct_imports)
        transitive_count = len(resolved.transitive_imports)
        dependency_depth = max(queue_depth, resolved.max_depth)
        transitive_hash = resolved.transitive_hash
        graph_inputs: dict[str, object] = {
            "import_graph_resolved": True,
            "import_graph_version": IMPORT_GRAPH_VERSION,
            "import_graph_sha256": import_graph.sha256,
            "import_graph_entries": import_graph.entry_count,
            "missing_import_count": len(resolved.missing_roots),
            "transitive_dependency_count": transitive_count,
        }
    else:
        import_breadth = len(unique_imports)
        import_depth = max((_module_depth(module) for module in unique_imports), default=0)
        direct_count = _nonnegative_int(metadata.get("direct_dependency_count"), default=import_breadth)
        dependency_depth = max(
            queue_depth,
            _nonnegative_int(metadata.get("dependency_depth"), default=queue_depth),
        )
        transitive_count = 0
        transitive_hash = str(metadata.get("transitive_dependency_hash") or "")
        graph_inputs = {"import_graph_resolved": False}
    mutation_depth = _nonnegative_int(metadata.get("mutation_depth"), default=0)
    lemma_rows_used = _lemma_rows_used(source_stream, metadata)
    citation_weight = _bounded_float(metadata.get("citation_weight"), default=1.0, cap=100.0)

    inputs: dict[str, object] = {
        **graph_inputs,
        "import_breadth": import_breadth,
        "import_depth": import_depth,
        "direct_dependency_count": direct_count,
        "transitive_dependency_count": transitive_count,
        "dependency_depth": dependency_depth,
        "mutation_depth": mutation_depth,
        "lemma_rows_used_count": len(lemma_rows_used),
        "citation_weight": citation_weight,
        "transitive_dependency_hash": transitive_hash,
    }
    return _receipt_from_inputs(inputs)


def _receipt_from_inputs(inputs: Mapping[str, Any]) -> SlotWeightReceipt:
    import_breadth = _nonnegative_int(inputs.get("import_breadth"), default=0)
    import_depth = _nonnegative_int(inputs.get("import_depth"), default=0)
    direct_count = _nonnegative_int(inputs.get("direct_dependency_count"), default=0)
    dependency_depth = _nonnegative_int(inputs.get("dependency_depth"), default=0)
    mutation_depth = _nonnegative_int(inputs.get("mutation_depth"), default=0)
    lemma_rows_used_count = _nonnegative_int(inputs.get("lemma_rows_used_count"), default=0)
    citation_weight = _bounded_float(inputs.get("citation_weight"), default=1.0, cap=100.0)
    transitive_count = _nonnegative_int(inputs.get("transitive_dependency_count"), default=0)
    basis_points = (
        1000
        + 200 * min(mutation_depth, 10)
        + 150 * min(dependency_depth, 100)
        + 50 * min(direct_count, 200)
        + 25 * min(import_breadth, 200)
        + 25 * min(import_depth, 50)
        + 10 * min(transitive_count, 500)
        + 100 * min(lemma_rows_used_count, 50)
        + min(1000, int(round(citation_weight * 10)))
    )
    return SlotWeightReceipt(
        weight=round(basis_points / 1000.0, 6),
        basis_points=basis_points,
        inputs=_canonical_inputs(inputs),
    )


def _canonical_inputs(inputs: Mapping[str, Any]) -> dict[str, object]:
    out: dict[str, object] = {
        "import_graph_resolved": inputs.get("import_graph_resolved") is True,
        "import_breadth": _nonnegative_int(inputs.get("import_breadth"), default=0),
        "import_depth": _nonnegative_int(inputs.get("import_depth"), default=0),
        "direct_dependency_count": _nonnegative_int(inputs.get("direct_dependency_count"), default=0),
        "transitive_dependency_count": _nonnegative_int(inputs.get("transitive_dependency_count"), default=0),
        "dependency_depth": _nonnegative_int(inputs.get("dependency_depth"), default=0),
        "mutation_depth": _nonnegative_int(inputs.get("mutation_depth"), default=0),
        "lemma_rows_used_count": _nonnegative_int(inputs.get("lemma_rows_used_count"), default=0),
        "citation_weight": _bounded_float(inputs.get("citation_weight"), default=1.0, cap=100.0),
        "transitive_dependency_hash": str(inputs.get("transitive_dependency_hash") or ""),
    }
    if out["import_graph_resolved"]:
        out.update(
            {
                "import_graph_version": str(inputs.get("import_graph_version") or ""),
                "import_graph_sha256": str(inputs.get("import_graph_sha256") or ""),
                "import_graph_entries": _nonnegative_int(inputs.get("import_graph_entries"), default=0),
                "missing_import_count": _nonnegative_int(inputs.get("missing_import_count"), default=0),
            }
        )
    return out


def _stored_graph_inputs(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    inputs = metadata.get("slot_weight_inputs")
    if not isinstance(inputs, Mapping) or inputs.get("import_graph_resolved") is not True:
        return None
    return inputs


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

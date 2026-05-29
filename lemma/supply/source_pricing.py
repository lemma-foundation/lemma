"""Source-derived task quality classification."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum

SOURCE_PRICING_VERSION = 1

_LEAN_DECL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")
_SOURCE_DERIVED_STREAMS = frozenset({"procedural", "mathlib_snapshot", "lemma_substrate"})


class SourceReuseClass(StrEnum):
    DIRECT_SOURCE_WRAPPER = "direct_source_wrapper"
    SOURCE_ORACLE_SOLVED = "source_oracle_solved"
    SOURCE_BASELINE_SOLVED = "source_baseline_solved"
    SOURCE_DERIVED_SURVIVED = "source_derived_survived"
    NON_SOURCE = "non_source"
    UNKNOWN = "unknown"


class TaskPool(StrEnum):
    CALIBRATION = "calibration"
    BOOTSTRAP = "bootstrap"
    SERIOUS_PAID = "serious_paid"
    FRONTIER = "frontier"
    UNKNOWN = "unknown"


_TASK_POOL_DEPTH_CAPS = {
    TaskPool.CALIBRATION: 1.00,
    TaskPool.BOOTSTRAP: 1.25,
    TaskPool.SERIOUS_PAID: 2.50,
    TaskPool.FRONTIER: 3.50,
    TaskPool.UNKNOWN: 1.00,
}


def is_lean_decl_name(value: object) -> bool:
    return isinstance(value, str) and bool(_LEAN_DECL_RE.fullmatch(value.strip()))


def reusable_source_theorem_name(metadata: Mapping[str, object]) -> str | None:
    name = metadata.get("source_theorem_name")
    if not is_lean_decl_name(name) or not source_theorem_is_direct_proof(metadata):
        return None
    return str(name).strip()


def source_theorem_is_direct_proof(metadata: Mapping[str, object]) -> bool:
    for step in _mutation_chain(metadata):
        params = step.get("params")
        if not isinstance(params, Mapping):
            return False
        if step.get("operator") == "substitute-type" and params.get("fallback") != "no_supported_type_occurrence":
            return False
        if params.get("target") == "fresh_prop_hypothesis" and params.get("binder_type") == "Prop":
            continue
        if step.get("operator") == "specialize" and source_specialize_value(params) is not None:
            continue
        if params.get("fallback") in {"true_premise", "no_supported_type_occurrence"}:
            continue
        if params.get("rule") == "reverse_relation" and params.get("relation") in {"=", "↔"}:
            continue
        if params.get("mode") == "peer_premise":
            continue
        if params.get("rule") in {"conjoin_peer_conclusion", "conjoin_self", "false_disjunct"}:
            continue
        return False
    return True


def source_specialize_value(params: Mapping[str, object]) -> str | None:
    value = params.get("value")
    binder_type = params.get("binder_type")
    if not isinstance(value, str) or not value.strip() or not isinstance(binder_type, str) or not binder_type.strip():
        return None
    if binder_type.strip() == "Prop":
        return value.strip()
    return f"({value.strip()} : {binder_type.strip()})"


def source_pricing_metadata(source_stream: str, metadata: Mapping[str, object]) -> dict[str, object]:
    reuse_class = source_reuse_class(source_stream, metadata)
    task_pool = task_pool_for_source_reuse(reuse_class, metadata)
    return {
        "source_pricing_version": SOURCE_PRICING_VERSION,
        "source_reuse_class": reuse_class.value,
        "task_pool": task_pool.value,
    }


def source_reuse_class(source_stream: str, metadata: Mapping[str, object]) -> SourceReuseClass:
    if reusable_source_theorem_name(metadata) is not None:
        return SourceReuseClass.DIRECT_SOURCE_WRAPPER
    if metadata.get("source_oracle_solved") is True and _is_source_derived(source_stream, metadata):
        return SourceReuseClass.SOURCE_ORACLE_SOLVED
    if metadata.get("baseline_solved") is True and _is_source_derived(source_stream, metadata):
        return SourceReuseClass.SOURCE_BASELINE_SOLVED
    explicit = parse_source_reuse_class(metadata.get("source_reuse_class") or metadata.get("source_pricing_class"))
    if explicit is not SourceReuseClass.UNKNOWN:
        return explicit
    if _is_source_derived(source_stream, metadata):
        return SourceReuseClass.SOURCE_DERIVED_SURVIVED
    return SourceReuseClass.NON_SOURCE


def task_pool_for_source_reuse(reuse_class: SourceReuseClass, metadata: Mapping[str, object]) -> TaskPool:
    if reuse_class is SourceReuseClass.DIRECT_SOURCE_WRAPPER:
        return TaskPool.CALIBRATION
    if reuse_class in {SourceReuseClass.SOURCE_ORACLE_SOLVED, SourceReuseClass.SOURCE_BASELINE_SOLVED}:
        return TaskPool.BOOTSTRAP
    explicit = parse_task_pool(metadata.get("task_pool"))
    if explicit is not TaskPool.UNKNOWN:
        return explicit
    if reuse_class in {SourceReuseClass.SOURCE_DERIVED_SURVIVED, SourceReuseClass.NON_SOURCE}:
        return TaskPool.SERIOUS_PAID
    return TaskPool.BOOTSTRAP


def parse_source_reuse_class(value: object) -> SourceReuseClass:
    if isinstance(value, SourceReuseClass):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "source_derived":
            return SourceReuseClass.SOURCE_DERIVED_SURVIVED
        try:
            return SourceReuseClass(text)
        except ValueError:
            return SourceReuseClass.UNKNOWN
    return SourceReuseClass.UNKNOWN


def parse_task_pool(value: object) -> TaskPool:
    if isinstance(value, TaskPool):
        return value
    if isinstance(value, str):
        try:
            return TaskPool(value.strip())
        except ValueError:
            return TaskPool.UNKNOWN
    return TaskPool.UNKNOWN


def depth_multiplier_micros(queue_depth: int, task_pool: TaskPool) -> int:
    raw = math.sqrt(max(0, int(queue_depth)) + 1)
    cap = _TASK_POOL_DEPTH_CAPS.get(task_pool, _TASK_POOL_DEPTH_CAPS[TaskPool.UNKNOWN])
    return max(1_000_000, int(round(min(raw, cap) * 1_000_000)))


def is_source_derived(source_stream: str, metadata: Mapping[str, object]) -> bool:
    return _is_source_derived(source_stream, metadata)


def source_import_status(
    imports: Sequence[str],
    metadata: Mapping[str, object],
    *,
    source_path: str | None = None,
) -> str:
    if not is_lean_decl_name(metadata.get("source_theorem_name")):
        return "no_source_theorem"
    source_module = _source_module_from_path(source_path)
    if any(module.strip() == "Mathlib" for module in imports):
        return "source_theorem_available"
    if source_module is None:
        return "unknown"
    return "source_theorem_available" if source_module in imports else "source_theorem_unavailable"


def _mutation_chain(metadata: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    chain = metadata.get("mutation_chain")
    if not isinstance(chain, list):
        return ()
    return tuple(step for step in chain if isinstance(step, Mapping))


def _is_source_derived(source_stream: str, metadata: Mapping[str, object]) -> bool:
    return (
        str(source_stream).strip() in _SOURCE_DERIVED_STREAMS
        or is_lean_decl_name(metadata.get("source_theorem_name"))
        or "source_task_id" in metadata
        or "source_target_sha256" in metadata
    )


def _source_module_from_path(source_path: str | None) -> str | None:
    if source_path is None:
        return None
    text = source_path.strip()
    if not text.endswith(".lean") or text.startswith("/") or "\\" in text:
        return None
    parts = text.removesuffix(".lean").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return ".".join(parts)

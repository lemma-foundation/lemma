"""Small task-supply helpers for Lean registries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from lemma.tasks import LemmaTask, SourceRef, TaskRegistry, load_task_registry

BaselineGate = Callable[[LemmaTask], bool]

DEFAULT_TOOLCHAIN = "leanprover/lean4:v4.30.0-rc2"
DEFAULT_MATHLIB_REV = "5450b53e5ddc"


def _stub(theorem_name: str, type_expr: str) -> str:
    return "\n".join(
        [
            "import Mathlib",
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


def make_task(
    *,
    task_id: str,
    title: str,
    theorem_name: str,
    type_expr: str,
    source_stream: str,
    source_name: str,
    source_license: str = "CC-BY-4.0",
    metadata: dict[str, Any] | None = None,
    queue_position: int | None = None,
    queue_depth: int = 0,
    frontier_depth: int | None = None,
    triviality_status: str = "unknown",
) -> LemmaTask:
    """Build a registry task with required provenance and computed target hash."""
    return LemmaTask(
        id=task_id,
        task_version=1,
        title=title,
        source_stream=source_stream,  # type: ignore[arg-type]
        source_ref=SourceRef(kind="dev_seed", name=source_name, path="tasks/registry.json"),
        source_license=source_license,
        imports=("Mathlib",),
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=f"theorem {theorem_name} : {type_expr} := by\n  sorry",
        submission_stub=_stub(theorem_name, type_expr),
        lean_toolchain=DEFAULT_TOOLCHAIN,
        mathlib_rev=DEFAULT_MATHLIB_REV,
        policy="restricted_helpers",
        queue_position=queue_position,
        queue_depth=queue_depth,
        frontier_depth=frontier_depth,
        triviality_status=triviality_status,  # type: ignore[arg-type]
        metadata=metadata or {},
    )


def generated_tasks(count: int) -> list[LemmaTask]:
    """Deterministic dev-seed generated tasks."""
    templates = [
        ("lemma.generated.true_intro", "Generated True", "generated_true_intro", "True", "trivial"),
        ("lemma.generated.and_intro", "Generated conjunction", "generated_and_intro", "True ∧ True", "constructor"),
        (
            "lemma.generated.nat_zero_add",
            "Generated Nat zero-add",
            "generated_nat_zero_add",
            "∀ n : Nat, 0 + n = n",
            "simp",
        ),
        (
            "lemma.generated.nat_add_zero",
            "Generated Nat add-zero",
            "generated_nat_add_zero",
            "∀ n : Nat, n + 0 = n",
            "simp",
        ),
        (
            "lemma.generated.nat_self_eq",
            "Generated Nat reflexivity",
            "generated_nat_self_eq",
            "∀ n : Nat, n = n",
            "rfl",
        ),
        (
            "lemma.generated.prop_id",
            "Generated proposition identity",
            "generated_prop_id",
            "∀ p : Prop, p → p",
            "intro",
        ),
        (
            "lemma.generated.and_left",
            "Generated conjunction left",
            "generated_and_left",
            "∀ p q : Prop, p ∧ q → p",
            "intro",
        ),
        (
            "lemma.generated.and_right",
            "Generated conjunction right",
            "generated_and_right",
            "∀ p q : Prop, p ∧ q → q",
            "intro",
        ),
        (
            "lemma.generated.true_of_prop",
            "Generated proof to True",
            "generated_true_of_prop",
            "∀ p : Prop, p → True",
            "trivial",
        ),
        (
            "lemma.generated.pair_intro",
            "Generated conjunction builder",
            "generated_pair_intro",
            "∀ p q : Prop, p → q → p ∧ q",
            "constructor",
        ),
        (
            "lemma.generated.or_false",
            "Generated right disjunction",
            "generated_or_false",
            "∀ p : Prop, p → p ∨ False",
            "left",
        ),
        (
            "lemma.generated.false_or",
            "Generated left disjunction",
            "generated_false_or",
            "∀ p : Prop, p → False ∨ p",
            "right",
        ),
        (
            "lemma.generated.eq_symm_nat",
            "Generated Nat equality symmetry",
            "generated_eq_symm_nat",
            "∀ a b : Nat, a = b → b = a",
            "symm",
        ),
        (
            "lemma.generated.eq_trans_nat",
            "Generated Nat equality transitivity",
            "generated_eq_trans_nat",
            "∀ a b c : Nat, a = b → b = c → a = c",
            "trans",
        ),
        (
            "lemma.generated.list_nil_length",
            "Generated List nil length",
            "generated_list_nil_length",
            "([] : List Nat).length = 0",
            "rfl",
        ),
        (
            "lemma.generated.list_append_nil",
            "Generated List append nil",
            "generated_list_append_nil",
            "∀ xs : List Nat, xs ++ [] = xs",
            "simp",
        ),
        (
            "lemma.generated.list_nil_append",
            "Generated List nil append",
            "generated_list_nil_append",
            "∀ xs : List Nat, ([] : List Nat) ++ xs = xs",
            "rfl",
        ),
        (
            "lemma.generated.bool_not_false",
            "Generated Bool not false",
            "generated_bool_not_false",
            "Bool.not false = true",
            "rfl",
        ),
        (
            "lemma.generated.bool_not_true",
            "Generated Bool not true",
            "generated_bool_not_true",
            "Bool.not true = false",
            "rfl",
        ),
        (
            "lemma.generated.nat_succ_ne_zero",
            "Generated Nat succ nonzero",
            "generated_nat_succ_ne_zero",
            "∀ n : Nat, Nat.succ n ≠ 0",
            "simp",
        ),
        (
            "lemma.generated.nat_zero_ne_succ",
            "Generated Nat zero not succ",
            "generated_nat_zero_ne_succ",
            "∀ n : Nat, 0 ≠ Nat.succ n",
            "simp",
        ),
        ("lemma.generated.nat_le_refl", "Generated Nat le refl", "generated_nat_le_refl", "∀ n : Nat, n ≤ n", "rfl"),
        (
            "lemma.generated.nat_lt_succ_self",
            "Generated Nat lt successor",
            "generated_nat_lt_succ_self",
            "∀ n : Nat, n < Nat.succ n",
            "simp",
        ),
        (
            "lemma.generated.nat_one_add",
            "Generated Nat one add",
            "generated_nat_one_add",
            "∀ n : Nat, 1 + n = Nat.succ n",
            "simp",
        ),
        (
            "lemma.generated.nat_succ_eq_add_one",
            "Generated Nat succ add-one",
            "generated_nat_succ_eq_add_one",
            "∀ n : Nat, Nat.succ n = n + 1",
            "simp",
        ),
        ("lemma.generated.not_false", "Generated not false", "generated_not_false", "¬ False", "intro"),
        ("lemma.generated.true_or_false", "Generated true or false", "generated_true_or_false", "True ∨ False", "left"),
        (
            "lemma.generated.false_implies",
            "Generated false elimination",
            "generated_false_implies",
            "∀ p : Prop, False → p",
            "cases",
        ),
        (
            "lemma.generated.and_comm_from_hyp",
            "Generated conjunction commutes",
            "generated_and_comm_from_hyp",
            "∀ p q : Prop, p ∧ q → q ∧ p",
            "constructor",
        ),
        ("lemma.generated.exists_zero", "Generated exists zero", "generated_exists_zero", "∃ n : Nat, n = 0", "exists"),
        ("lemma.generated.zero_le", "Generated zero le Nat", "generated_zero_le", "∀ n : Nat, 0 ≤ n", "simp"),
    ]
    return [
        make_task(
            task_id=task_id,
            title=title,
            theorem_name=theorem,
            type_expr=type_expr,
            source_stream="generated",
            source_name="deterministic-dev-seed",
            queue_position=offset,
            queue_depth=0,
            metadata={"difficulty": "dev", "baseline_hint": hint},
        )
        for offset, (task_id, title, theorem, type_expr, hint) in enumerate(templates[:count])
    ]


def load_seed_registry(path: Path) -> TaskRegistry:
    return load_task_registry(path.read_bytes())


def default_baseline_gate(task: LemmaTask) -> bool:
    """Return False when metadata marks a task as baseline-solved."""
    return not bool(task.metadata.get("baseline_solved"))


def eligible_tasks(tasks: Iterable[LemmaTask], baseline_gate: BaselineGate = default_baseline_gate) -> list[LemmaTask]:
    """Apply launch activation gates without hiding the rules in validators."""
    out: list[LemmaTask] = []
    for task in tasks:
        if bool(task.metadata.get("held_out_benchmark")):
            continue
        if not baseline_gate(task):
            continue
        out.append(task)
    return out


def deterministic_queue(
    tasks: Iterable[LemmaTask],
    *,
    seed: str,
    max_frontier_depth: int | None = None,
) -> list[LemmaTask]:
    """Return a deterministic queue balanced across available levels and families."""
    eligible = [task for task in tasks if max_frontier_depth is None or task.queue_depth <= max_frontier_depth]
    seeded = sorted(
        eligible,
        key=lambda task: (
            hashlib.sha256(f"{seed}:{task.id}:{task.target_sha256}".encode()).hexdigest(),
        ),
    )
    return _level_family_balanced_queue(seeded)


def _level_family_balanced_queue(tasks: list[LemmaTask]) -> list[LemmaTask]:
    by_depth: dict[int, list[LemmaTask]] = {}
    for task in tasks:
        by_depth.setdefault(task.queue_depth, []).append(task)
    depth_order = depth_spread_order(tuple(by_depth))
    depth_queues = {depth: _family_balanced_queue(items) for depth, items in by_depth.items()}
    positions = dict.fromkeys(depth_queues, 0)
    out: list[LemmaTask] = []
    while len(out) < len(tasks):
        progressed = False
        for depth in depth_order:
            position = positions[depth]
            bucket = depth_queues[depth]
            if position >= len(bucket):
                continue
            out.append(bucket[position])
            positions[depth] = position + 1
            progressed = True
        if not progressed:
            break
    return out


def _family_balanced_queue(tasks: list[LemmaTask]) -> list[LemmaTask]:
    by_family: dict[str, list[LemmaTask]] = {}
    for task in tasks:
        by_family.setdefault(_task_family(task), []).append(task)
    positions = dict.fromkeys(by_family, 0)
    out: list[LemmaTask] = []
    while len(out) < len(tasks):
        progressed = False
        for family, bucket in by_family.items():
            position = positions[family]
            if position >= len(bucket):
                continue
            out.append(bucket[position])
            positions[family] = position + 1
            progressed = True
        if not progressed:
            break
    return out


def depth_spread_order(depths: tuple[int, ...]) -> tuple[int, ...]:
    ordered = sorted(depths)
    out: list[int] = []
    low = 0
    high = len(ordered) - 1
    while low <= high:
        out.append(ordered[high])
        if low != high:
            out.append(ordered[low])
        high -= 1
        low += 1
    return tuple(out)


def _task_family(task: LemmaTask) -> str:
    topic = str(task.metadata.get("topic") or "").strip()
    subtopic = str(task.metadata.get("subtopic") or "").strip()
    if topic and subtopic:
        return f"{topic}/{subtopic}"
    if topic:
        return topic
    path = str(task.source_ref.path or "").strip()
    if path:
        parts = path.removesuffix(".lean").split("/")
        if len(parts) >= 3 and parts[0] == "Mathlib":
            if parts[1] == "Data":
                return "/".join(parts[1:3])
            return parts[1]
        return path
    return str(task.source_ref.name or task.id)


def write_registry(
    tasks: Iterable[LemmaTask],
    path: Path,
    *,
    signed_by: str | None = None,
    signature: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "tasks": [task.model_dump(mode="json", exclude_none=True) for task in tasks],
    }
    if signed_by is not None:
        payload["signed_by"] = signed_by
    if signature is not None:
        payload["signature"] = signature
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

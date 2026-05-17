"""Small task-supply helpers for v1 registries."""

from __future__ import annotations

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
            "lemma.generated.list_nil_length",
            "Generated List nil length",
            "generated_list_nil_length",
            "([] : List Nat).length = 0",
            "rfl",
        ),
    ]
    return [
        make_task(
            task_id=task_id,
            title=title,
            theorem_name=theorem,
            type_expr=type_expr,
            source_stream="generated",
            source_name="deterministic-dev-seed",
            metadata={"difficulty": "dev", "baseline_hint": hint},
        )
        for task_id, title, theorem, type_expr, hint in templates[:count]
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


def write_registry(tasks: Iterable[LemmaTask], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "tasks": [task.model_dump(mode="json", exclude_none=True) for task in tasks],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

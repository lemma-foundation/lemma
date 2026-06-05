"""Evaluate the real-task launch criteria into a deterministic report.

The assessment takes a pinned task registry, runs several local epochs with a
mixed panel of honest, duplicate, invalid, and timeout miners, and checks each
launch criterion from the roadmap. Everything is derived from public inputs so
two operators get the same report.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lemma.atlas import build_solved_ledger, build_task_bundles
from lemma.atlas.realtask import TaskBundle
from lemma.lean.rejection import MINER_FACING_REJECTION_CLASSES, REJECTION_DESCRIPTIONS
from lemma.sim.epoch_sim import MinerPlan, SimResult, simulate_epochs
from lemma.supply.stratified import class_stratified_tasks
from lemma.tasks import TaskRegistry

CriterionStatus = Literal["pass", "fail", "not_measured"]

#: The default mixed miner panel: two honest miners (an early winner and a
#: late honest miner), plus a duplicate, an invalid, and a timeout miner.
DEFAULT_PANEL: tuple[MinerPlan, ...] = (
    MinerPlan(hotkey="miner-honest-early", behavior="honest", order=0),
    MinerPlan(hotkey="miner-honest-late", behavior="honest", order=5),
    MinerPlan(hotkey="miner-duplicate", behavior="duplicate", order=1),
    MinerPlan(hotkey="miner-invalid", behavior="invalid", order=2),
    MinerPlan(hotkey="miner-timeout", behavior="timeout", order=3),
)

_HONEST_HOTKEYS = frozenset(plan.hotkey for plan in DEFAULT_PANEL if plan.behavior == "honest")


class CriterionResult(BaseModel):
    """One launch criterion and its measured outcome."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    status: CriterionStatus
    detail: str
    metric: dict[str, Any] = Field(default_factory=dict)


class LaunchReport(BaseModel):
    """The full launch-readiness report.

    ``ready`` means every automatically-checkable criterion passed. Criteria
    marked ``not_measured`` (for example container replay that needs a real Lean
    toolchain) are reported but do not flip ``ready`` on their own; they list the
    manual verification still required before launch.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    ready: bool
    pass_count: int
    fail_count: int
    not_measured_count: int
    criteria: list[CriterionResult]


def _ok(id: str, description: str, detail: str, **metric: Any) -> CriterionResult:
    return CriterionResult(id=id, description=description, status="pass", detail=detail, metric=metric)


def _fail(id: str, description: str, detail: str, **metric: Any) -> CriterionResult:
    return CriterionResult(id=id, description=description, status="fail", detail=detail, metric=metric)


def _pf(condition: bool, id: str, description: str, ok_detail: str, fail_detail: str, **metric: Any) -> CriterionResult:
    return (
        _ok(id, description, ok_detail, **metric) if condition else _fail(id, description, fail_detail, **metric)
    )


def _packaged_count_criterion(bundles: Sequence[TaskBundle], min_tasks: int) -> CriterionResult:
    count = len(bundles)
    return _pf(
        count >= min_tasks,
        "packaged_task_volume",
        "At least the minimum number of packaged, validated real tasks are available.",
        f"{count} packaged tasks (>= {min_tasks}).",
        f"only {count} packaged tasks; need at least {min_tasks}.",
        count=count,
        minimum=min_tasks,
    )


def _field_present_criterion(
    bundles: Sequence[TaskBundle],
    *,
    id: str,
    description: str,
    label: str,
    predicate: Any,
) -> CriterionResult:
    missing = [bundle.task_id for bundle in bundles if not predicate(bundle)]
    return _pf(
        not missing,
        id,
        description,
        f"every task carries {label}.",
        f"{len(missing)} task(s) missing {label}: {', '.join(missing[:5])}",
        missing=missing,
    )


def _rejection_reasons_criterion() -> CriterionResult:
    undocumented = [cls for cls in MINER_FACING_REJECTION_CLASSES if not REJECTION_DESCRIPTIONS.get(cls)]
    return _pf(
        not undocumented,
        "rejection_reasons_understandable",
        "Every miner-facing rejection class has a plain-language explanation.",
        f"all {len(MINER_FACING_REJECTION_CLASSES)} reject classes are documented.",
        f"undocumented reject classes: {', '.join(undocumented)}",
        undocumented=undocumented,
    )


def _deterministic_selection_criterion(registry: TaskRegistry, *, active_K: int, seed: str) -> CriterionResult:
    first = class_stratified_tasks(registry.tasks, active_K=active_K, seed=seed)
    second = class_stratified_tasks(registry.tasks, active_K=active_K, seed=seed)
    first_ids = tuple(task.id for task in first)
    second_ids = tuple(task.id for task in second)
    return _pf(
        first_ids == second_ids,
        "deterministic_active_selection",
        "Two validators select the same active set from the same public inputs.",
        f"selection is deterministic across runs ({len(first_ids)} tasks).",
        "active selection differed across identical runs.",
        selected=list(first_ids),
    )


def _epochs_criterion(sim: SimResult, *, epochs: int) -> CriterionResult:
    completed = len(sim.epochs)
    any_winner = any(outcome.winners for outcome in sim.epochs)
    return _pf(
        completed == epochs and any_winner,
        "epochs_run_end_to_end",
        "Several local epochs run end to end and accept proofs.",
        f"{completed} epochs completed with verified winners.",
        f"only {completed}/{epochs} epochs completed or no winners were accepted.",
        epochs=completed,
        solved=len(sim.solved_task_ids),
    )


def _duplicate_criterion(sim: SimResult) -> CriterionResult:
    dup_credit = sum(outcome.credits.get("miner-duplicate", 0) for outcome in sim.epochs)
    honest_credit = sum(
        credit for outcome in sim.epochs for hk, credit in outcome.credits.items() if hk in _HONEST_HOTKEYS
    )
    return _pf(
        dup_credit == 0 and honest_credit > 0,
        "duplicate_scored_correctly",
        "Duplicate resubmissions of a winning proof earn no credit.",
        "duplicate miner earned no credit; honest winners were rewarded.",
        f"duplicate credit={dup_credit}, honest credit={honest_credit}.",
        duplicate_credit=dup_credit,
        honest_credit=honest_credit,
    )


def _cheats_criterion(sim: SimResult) -> CriterionResult:
    winners = {hotkey for outcome in sim.epochs for hotkey in outcome.winners.values()}
    cheaters = sorted(winners - _HONEST_HOTKEYS)
    return _pf(
        not cheaters,
        "cheats_never_rewarded",
        "Invalid, timeout, and duplicate submissions never win a task.",
        "only honest unique proofs won tasks.",
        f"non-honest winners detected: {', '.join(cheaters)}",
        non_honest_winners=cheaters,
    )


def _synthetic_accepted_rows(sim: SimResult, bundles: Sequence[TaskBundle]) -> list[dict[str, Any]]:
    formats = {bundle.task_id: bundle.task_format for bundle in bundles}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for outcome in sim.epochs:
        for task_id, hotkey in outcome.winners.items():
            if task_id in seen:
                continue
            seen.add(task_id)
            kind = "patch" if formats.get(task_id) == "patch" else "proof"
            identity = f"{task_id}:{hotkey}"
            rows.append(
                {
                    "task_id": task_id,
                    "solver_hotkey": hotkey,
                    "validator_hotkey": "sim-validator",
                    "artifact_kind": kind,
                    "proof_sha256": identity,
                    "patch_sha256": identity if kind == "patch" else None,
                    "proof_identity": identity,
                    "proof_identity_strength": "strong",
                    "rewarded": True,
                    "tempo": outcome.tempo,
                }
            )
    return rows


def _replay_metadata_criterion(solved: Sequence[Any]) -> CriterionResult:
    incomplete = [
        entry.task_id
        for entry in solved
        if not (entry.reproduction_command and entry.target_sha256 and entry.artifact_sha256)
    ]
    return _pf(
        bool(solved) and not incomplete,
        "solved_replay_metadata_present",
        "Every solved proof carries the metadata needed to replay it.",
        f"all {len(solved)} solved entries carry reproduction, target, and artifact hashes.",
        (
            "no solved entries to check."
            if not solved
            else f"{len(incomplete)} solved entries missing replay metadata: {', '.join(incomplete[:5])}"
        ),
        incomplete=incomplete,
    )


def _atlas_accuracy_criterion(sim: SimResult, solved: Sequence[Any]) -> CriterionResult:
    expected = len(sim.solved_task_ids)
    actual = len(solved)
    return _pf(
        expected == actual,
        "atlas_site_accurate",
        "The solved ledger (Proof Atlas and site) matches the solved tasks exactly.",
        f"solved ledger has {actual} entries, matching {expected} solved tasks.",
        f"solved ledger has {actual} entries but {expected} tasks were solved.",
        expected=expected,
        actual=actual,
    )


def _container_replay_criterion() -> CriterionResult:
    return CriterionResult(
        id="container_replay",
        description="Accepted proofs replay from clean Lean containers.",
        status="not_measured",
        detail=(
            "Requires a pinned Lean toolchain; run `lemma corpus replay` against accepted rows "
            "in a clean container before launch. Replay metadata presence is checked separately."
        ),
    )


def assess_launch_readiness(
    registry: TaskRegistry,
    *,
    min_tasks: int = 100,
    epochs: int = 3,
    active_K: int = 8,
    seed: str = "lemma-launch",
) -> LaunchReport:
    """Run the end-to-end launch-readiness assessment over a task registry."""
    bundles = build_task_bundles(registry)
    effective_k = max(1, min(active_K, len(registry.tasks))) if registry.tasks else 1
    sim = simulate_epochs(
        registry.tasks,
        miners=DEFAULT_PANEL,
        epochs=epochs,
        active_K=effective_k,
        seed=seed,
    )
    accepted_rows = _synthetic_accepted_rows(sim, bundles)
    solved = build_solved_ledger(accepted_rows, bundles)

    criteria = [
        _packaged_count_criterion(bundles, min_tasks),
        _field_present_criterion(
            bundles,
            id="reproduction_command_present",
            description="Every task has an exact reproduction command.",
            label="a reproduction command",
            predicate=lambda b: bool(b.reproduction_command),
        ),
        _field_present_criterion(
            bundles,
            id="environment_certified",
            description="Every task pins a certified validation environment.",
            label="an environment hash",
            predicate=lambda b: bool(b.environment_sha256),
        ),
        _field_present_criterion(
            bundles,
            id="target_hashes_present",
            description="Every task pins target and target-type hashes.",
            label="target and target-type hashes",
            predicate=lambda b: bool(b.target_sha256) and bool(b.target_type_sha256),
        ),
        _rejection_reasons_criterion(),
        _deterministic_selection_criterion(registry, active_K=effective_k, seed=f"{seed}:0"),
        _epochs_criterion(sim, epochs=epochs),
        _duplicate_criterion(sim),
        _cheats_criterion(sim),
        _replay_metadata_criterion(solved),
        _atlas_accuracy_criterion(sim, solved),
        _container_replay_criterion(),
    ]

    pass_count = sum(1 for c in criteria if c.status == "pass")
    fail_count = sum(1 for c in criteria if c.status == "fail")
    not_measured_count = sum(1 for c in criteria if c.status == "not_measured")
    return LaunchReport(
        ready=fail_count == 0,
        pass_count=pass_count,
        fail_count=fail_count,
        not_measured_count=not_measured_count,
        criteria=criteria,
    )

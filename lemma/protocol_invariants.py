"""Production-mode protocol invariants for real Lean tasks."""

from __future__ import annotations

from collections.abc import Iterable

from lemma.common.config import LemmaSettings
from lemma.task_activation import task_reward_eligibility
from lemma.tasks import LemmaTask, TaskRegistry

REAL_TASK_STREAMS = frozenset({"formal_conjectures", "sorrydb", "lean_project", "human_curated"})


def production_supply_rejection_reason(task: LemmaTask) -> str:
    """Return why a paid task is not eligible for real-task production supply."""

    reward = task_reward_eligibility(task)
    if not reward.eligible:
        return reward.reason
    if task.source_stream not in REAL_TASK_STREAMS:
        return f"source_stream:{task.source_stream}"
    if task.source_stream == "human_curated" and task.source_ref.kind == "fixed_fixture":
        return "source_ref:fixed_fixture"
    if not task.source_ref.kind.strip():
        return "source_ref.kind"
    if not task.source_ref.name.strip():
        return "source_ref.name"
    if task.source_ref.commit is not None and not task.source_ref.commit.strip():
        return "source_ref.commit"
    if task.source_ref.url is not None and not task.source_ref.url.strip():
        return "source_ref.url"
    if task.source_ref.path is not None and not task.source_ref.path.strip():
        return "source_ref.path"
    if task.source_stream in {"formal_conjectures", "sorrydb", "lean_project"}:
        if not task.source_ref.url:
            return "source_ref.url"
        if not task.source_ref.commit:
            return "source_ref.commit"
        if not task.source_ref.path:
            return "source_ref.path"
    if not task.imports:
        return "imports"
    if not task.theorem_name.strip():
        return "theorem_name"
    if not task.type_expr.strip():
        return "type_expr"
    if " sorry" not in f" {task.statement}":
        return "statement_target_hole"
    if " sorry" not in f" {task.submission_stub}":
        return "submission_stub_hole"
    return ""


def production_supply_rejections(
    registry_or_tasks: TaskRegistry | Iterable[LemmaTask],
    **_: object,
) -> tuple[tuple[str, str], ...]:
    """Return public-safe production supply rejection reasons."""

    tasks = registry_or_tasks.tasks if isinstance(registry_or_tasks, TaskRegistry) else tuple(registry_or_tasks)
    return tuple(
        (task.id, reason)
        for task in tasks
        if (reason := production_supply_rejection_reason(task))
    )


def enforce_production_invariants(settings: LemmaSettings, registry: TaskRegistry) -> None:
    """Fail closed unless production uses registry-backed real Lean tasks."""

    if settings.protocol_mode != "production":
        return
    if settings.task_supply_mode != "registry":
        raise RuntimeError("production mode requires LEMMA_TASK_SUPPLY_MODE=registry")
    if tuple(settings.enabled_domains) != ("lean",):
        raise RuntimeError("production mode requires LEMMA_ENABLED_DOMAINS=lean")
    if settings.active_seed_mode != "epoch_randomness":
        raise RuntimeError("production mode requires LEMMA_ACTIVE_SEED_MODE=epoch_randomness")
    if settings.active_epoch_randomness_source != "chain_drand":
        raise RuntimeError("production mode requires LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand")
    if not settings.require_submission_signatures:
        raise RuntimeError("production mode requires LEMMA_REQUIRE_SUBMISSION_SIGNATURES=1")
    if not settings.require_commit_reveal:
        raise RuntimeError("production mode requires LEMMA_REQUIRE_COMMIT_REVEAL=1")
    if not settings.require_strong_proof_identity:
        raise RuntimeError("production mode requires LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1")
    if settings.lean_sandbox_network.strip().lower() not in {"none", "no"}:
        raise RuntimeError("production mode requires Lean verifier networking disabled")
    if not registry.tasks:
        raise RuntimeError("production mode requires a nonempty task registry")
    rejections = production_supply_rejections(registry)
    if rejections:
        task_id, reason = rejections[0]
        raise RuntimeError(f"production mode requires real missing-proof tasks: {task_id}:{reason}")

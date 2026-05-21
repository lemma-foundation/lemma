"""Production-mode protocol invariants."""

from __future__ import annotations

import re
from typing import Any

from lemma.common.config import LemmaSettings
from lemma.task_activation import activation_status_for, task_reward_eligibility
from lemma.tasks import LemmaTask, TaskRegistry

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def production_supply_rejection_reason(task: LemmaTask) -> str:
    """Return why a paid task is not eligible for production mainnet supply."""

    if activation_status_for(task) != "paid":
        return ""
    reward = task_reward_eligibility(task)
    if not reward.eligible:
        return reward.reason
    metadata = task.metadata
    if task.source_stream != "procedural":
        return f"source_stream:{task.source_stream}"
    if task.source_ref.kind != "procedural":
        return f"source_ref:{task.source_ref.kind}"
    if metadata.get("supply_mode") != "procedural":
        return "supply_mode"
    if metadata.get("mutation_depth") != 2:
        return "mutation_depth"
    chain = metadata.get("mutation_chain")
    if not isinstance(chain, list) or len(chain) != 2:
        return "mutation_chain"
    for step in chain:
        if not isinstance(step, dict) or not str(step.get("operator") or "").strip():
            return "mutation_chain"
    if not _has_text(metadata, "generation_seed"):
        return "generation_seed"
    if not _has_int(metadata, "drand_round"):
        return "drand_round"
    if not _has_int(metadata, "anchor_block"):
        return "anchor_block"
    for key in ("source_pool_hash", "operator_bundle_hash", "canonical_hash"):
        if not _has_hex64(metadata, key):
            return key
    if metadata.get("typechecked") is not True:
        return "typecheck"
    if metadata.get("prop_gate_passed") is not True:
        return "prop_gate"
    if metadata.get("triviality_checked") is not True:
        return "triviality"
    if metadata.get("baseline_solved") is True:
        return "baseline_solved"
    if metadata.get("novelty_status") != "passed":
        return "novelty_status"
    if _positive_float(metadata.get("slot_weight")) is None:
        return "slot_weight"
    return ""


def production_supply_rejections(registry: TaskRegistry) -> tuple[str, ...]:
    out: list[str] = []
    for task in registry.tasks:
        reason = production_supply_rejection_reason(task)
        if reason:
            out.append(f"{task.id}:{reason}")
    return tuple(out)


def _has_text(metadata: dict[str, Any], key: str) -> bool:
    return isinstance(metadata.get(key), str) and bool(str(metadata[key]).strip())


def _has_int(metadata: dict[str, Any], key: str) -> bool:
    return isinstance(metadata.get(key), int) and metadata[key] >= 0


def _has_hex64(metadata: dict[str, Any], key: str) -> bool:
    return isinstance(metadata.get(key), str) and bool(_HEX64.fullmatch(str(metadata[key])))


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def enforce_production_invariants(settings: LemmaSettings, registry: TaskRegistry) -> None:
    """Fail closed when production mode lacks the Lean safety boundary."""

    if settings.protocol_mode != "production":
        return
    if tuple(settings.enabled_domains) != ("lean",):
        raise RuntimeError("production mode currently supports only lean: LEMMA_ENABLED_DOMAINS must be lean")
    if not settings.task_registry_sha256_expected:
        raise RuntimeError("production mode requires LEMMA_TASK_REGISTRY_SHA256_EXPECTED")
    if registry.signature_status != "verified":
        raise RuntimeError("production mode requires signature-verified registry bytes")
    if settings.lean_sandbox_network.strip().lower() not in {"none", "no"}:
        raise RuntimeError("production mode requires network-disabled verifier runs")
    if not settings.require_submission_signatures:
        raise RuntimeError("production mode requires live miner authentication")
    if not settings.require_commit_reveal:
        raise RuntimeError("production mode requires LEMMA_REQUIRE_COMMIT_REVEAL=1")
    if not settings.require_strong_proof_identity:
        raise RuntimeError("production mode requires LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1")
    rejections = production_supply_rejections(registry)
    if rejections:
        detail = ", ".join(rejections[:5])
        raise RuntimeError(f"production mode requires paid procedural depth-2 supply: {detail}")
    if settings.active_seed_mode != "epoch_randomness":
        raise RuntimeError("production mode requires LEMMA_ACTIVE_SEED_MODE=epoch_randomness")
    if settings.active_epoch_randomness_source != "chain_drand":
        raise RuntimeError("production mode requires LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand")

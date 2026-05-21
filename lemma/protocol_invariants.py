"""Production-mode protocol invariants."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from lemma.common.config import LemmaSettings
from lemma.supply.gates import GATE_VERSION
from lemma.supply.slot_weight import SLOT_WEIGHT_VERSION, slot_weight_receipt_for_task
from lemma.supply.triviality_budget import TRIVIALITY_BUDGET_VERSION
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
    if not _has_int(metadata, "tempo"):
        return "tempo"
    for key in ("source_pool_hash", "operator_bundle_hash", "canonical_hash"):
        if not _has_hex64(metadata, key):
            return key
    if metadata.get("gate_version") != GATE_VERSION:
        return "gate_version"
    if metadata.get("gate_runner") != "lean":
        return "gate_runner"
    if metadata.get("typechecked") is not True:
        return "typecheck"
    if metadata.get("prop_gate_passed") is not True:
        return "prop_gate"
    if metadata.get("triviality_checked") is not True:
        return "triviality"
    if _positive_float(metadata.get("triviality_budget_s")) is None:
        return "triviality_budget_s"
    if metadata.get("triviality_budget_version") != TRIVIALITY_BUDGET_VERSION:
        return "triviality_budget_version"
    retarget_inputs = metadata.get("triviality_retarget_inputs")
    if not isinstance(retarget_inputs, dict):
        return "triviality_retarget_inputs"
    if retarget_inputs.get("version") != TRIVIALITY_BUDGET_VERSION:
        return "triviality_retarget_inputs"
    if retarget_inputs.get("target_tempo") != metadata.get("tempo"):
        return "triviality_retarget_inputs"
    if metadata.get("baseline_solved") is True:
        return "baseline_solved"
    if metadata.get("novelty_status") != "passed":
        return "novelty_status"
    slot_weight_reason = _slot_weight_rejection_reason(task)
    if slot_weight_reason:
        return slot_weight_reason
    if _positive_float(metadata.get("slot_weight")) is None:
        return "slot_weight"
    if metadata.get("gate_receipt_sha256") != procedural_gate_receipt_sha256(task):
        return "gate_receipt_sha256"
    return ""


def procedural_gate_receipt_sha256(task: LemmaTask) -> str:
    metadata = task.metadata
    payload = {
        "version": GATE_VERSION,
        "task_id": task.id,
        "target_sha256": task.target_sha256,
        "canonical_hash": metadata.get("canonical_hash"),
        "gate_runner": metadata.get("gate_runner"),
        "typechecked": metadata.get("typechecked"),
        "typecheck_reason": metadata.get("typecheck_reason"),
        "prop_gate_passed": metadata.get("prop_gate_passed"),
        "prop_gate_reason": metadata.get("prop_gate_reason"),
        "triviality_checked": metadata.get("triviality_checked"),
        "triviality_stack": metadata.get("triviality_stack"),
        "triviality_budget_s": metadata.get("triviality_budget_s"),
        "triviality_budget_version": metadata.get("triviality_budget_version"),
        "triviality_burn_rate_basis_points": metadata.get("triviality_burn_rate_basis_points"),
        "triviality_retarget_inputs": metadata.get("triviality_retarget_inputs"),
        "triviality_reason": metadata.get("triviality_reason"),
        "baseline_solved": metadata.get("baseline_solved"),
        "baseline_solver": metadata.get("baseline_solver"),
        "novelty_status": metadata.get("novelty_status"),
        "slot_weight": metadata.get("slot_weight"),
        "slot_weight_version": metadata.get("slot_weight_version"),
        "slot_weight_basis_points": metadata.get("slot_weight_basis_points"),
        "slot_weight_inputs": metadata.get("slot_weight_inputs"),
        "source_pool_hash": metadata.get("source_pool_hash"),
        "operator_bundle_hash": metadata.get("operator_bundle_hash"),
        "mutation_chain": metadata.get("mutation_chain"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()


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


def _slot_weight_rejection_reason(task: LemmaTask) -> str:
    metadata = task.metadata
    if metadata.get("slot_weight_version") != SLOT_WEIGHT_VERSION:
        return "slot_weight_version"
    expected = slot_weight_receipt_for_task(task)
    if metadata.get("slot_weight_basis_points") != expected.basis_points:
        return "slot_weight_basis_points"
    if metadata.get("slot_weight_inputs") != expected.inputs:
        return "slot_weight_inputs"
    observed = _positive_float(metadata.get("slot_weight"))
    if observed is None or abs(observed - expected.weight) > 1e-9:
        return "slot_weight"
    return ""


def enforce_production_invariants(settings: LemmaSettings, registry: TaskRegistry) -> None:
    """Fail closed when production mode lacks the Lean safety boundary."""

    if settings.protocol_mode != "production":
        return
    if tuple(settings.enabled_domains) != ("lean",):
        raise RuntimeError("production mode currently supports only lean: LEMMA_ENABLED_DOMAINS must be lean")
    if settings.task_supply_mode != "procedural":
        raise RuntimeError("production mode requires LEMMA_TASK_SUPPLY_MODE=procedural")
    expected_source = _normalize_sha256(settings.procedural_source_sha256_expected)
    if not expected_source:
        raise RuntimeError("procedural production mode requires LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED")
    source_hashes = {str(task.metadata.get("source_pool_hash") or "") for task in registry.tasks}
    if source_hashes != {expected_source}:
        raise RuntimeError("procedural production mode source pool hash mismatch")
    expected_operator = _normalize_sha256(settings.procedural_operator_bundle_sha256_expected)
    if expected_operator:
        operator_hashes = {str(task.metadata.get("operator_bundle_hash") or "") for task in registry.tasks}
        if operator_hashes != {expected_operator}:
            raise RuntimeError("procedural production mode operator bundle hash mismatch")
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


def _normalize_sha256(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw.startswith("sha256:"):
        raw = raw.removeprefix("sha256:")
    return raw

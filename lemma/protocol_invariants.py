"""Production-mode protocol invariants."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lemma.common.config import LemmaSettings
from lemma.supply.gates import GATE_VERSION
from lemma.supply.import_graph import IMPORT_GRAPH_VERSION, ImportGraph, read_import_graph
from lemma.supply.ingredients import (
    IngredientManifest,
    expected_ingredient_generation_receipt_sha256,
    expected_ingredient_novelty_family_hash,
    ingredient_difficulty_state_active_lanes,
    ingredient_difficulty_state_records,
    ingredient_submission_stub,
    text_sha256,
    validate_ingredient_imports,
    validate_ingredient_selected_parameters,
    validate_ingredient_statement_header,
    validate_ingredient_task_title,
)
from lemma.supply.novelty import NOVELTY_CACHE_VERSION
from lemma.supply.operator_bundle import (
    MUTATION_ENGINE,
    OPERATOR_BUNDLE_VERSION,
    OPERATOR_NAMES,
    procedural_operator_bundle_hash,
)
from lemma.supply.slot_weight import SLOT_WEIGHT_VERSION, slot_weight_receipt_for_task
from lemma.supply.source_pool import SOURCE_POOL_RECEIPT_VERSION, SOURCE_SAMPLING_VERSION, source_pool_receipt_sha256
from lemma.supply.source_pricing import TaskPool, is_source_derived, parse_task_pool, source_import_status
from lemma.supply.triviality_budget import TRIVIALITY_BUDGET_VERSION
from lemma.task_activation import activation_status_for, task_reward_eligibility
from lemma.task_supply import DEFAULT_TOOLCHAIN
from lemma.tasks import LEAN_VERIFIER_ID, LEAN_VERIFIER_VERSION, LemmaTask, TaskRegistry, problem_target_sha256

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INGREDIENT_TASK_FIELD_SHADOW_KEYS = frozenset(LemmaTask.model_fields) | {"created_at_block"}
_INGREDIENT_METADATA_SIDE_CHANNEL_KEYS = frozenset(
    {"created_at", "generated_at", "generation_seed", "local_seed", "random_seed", "private_seed"}
)
_INGREDIENT_TASK_METADATA_KEYS = frozenset(
    {
        "supply_mode",
        "tempo",
        "active_K",
        "epoch_seed_sha256",
        "ingredient_manifest_sha256",
        "lemma_corpus_snapshot_sha256",
        "ingredient_repo_commit",
        "mathlib_commit",
        "recipe_bundle_sha256",
        "difficulty_state_sha256",
        "difficulty_lane",
        "selector_id",
        "recipe_id",
        "ingredient_ids",
        "ingredient_count",
        "hidden_lemma_count",
        "novelty_family_hash",
        "definition_ids",
        "fact_ids",
        "bridge_ids",
        "selected_parameters",
        "selection_seed_sha256",
        "generation_receipt_sha256",
        "gate_receipt_sha256",
        "shortcut_receipt_sha256",
        "theorem_statement_sha256",
        "active_target_sha256",
    }
)
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
        if not isinstance(step, dict):
            return "mutation_chain"
        if step.get("operator") not in OPERATOR_NAMES:
            return "mutation_chain"
        params = step.get("params")
        if not isinstance(params, dict):
            return "mutation_params"
        if params.get("engine") != MUTATION_ENGINE:
            return "mutation_engine"
        for key in ("input_hash", "output_hash"):
            if not isinstance(step.get(key), str) or not _HEX64.fullmatch(str(step[key])):
                return "mutation_chain"
    if metadata.get("operator_bundle_version") != OPERATOR_BUNDLE_VERSION:
        return "operator_bundle_version"
    if not _has_hex64(metadata, "operator_bundle_hash"):
        return "operator_bundle_hash"
    if metadata.get("operator_bundle_hash") != procedural_operator_bundle_hash():
        return "operator_bundle_hash"
    if not _has_text(metadata, "generation_seed"):
        return "generation_seed"
    if not _has_int(metadata, "drand_round"):
        return "drand_round"
    if not _has_int(metadata, "anchor_block"):
        return "anchor_block"
    if not _has_int(metadata, "tempo"):
        return "tempo"
    for key in ("source_pool_hash", "canonical_hash", "statement_hash"):
        if not _has_hex64(metadata, key):
            return key
    source_pool_reason = _source_pool_rejection_reason(task)
    if source_pool_reason:
        return source_pool_reason
    yield_history_reason = _yield_history_rejection_reason(task)
    if yield_history_reason:
        return yield_history_reason
    if metadata.get("gate_version") != GATE_VERSION:
        return "gate_version"
    if metadata.get("gate_runner") != "lean":
        return "gate_runner"
    if metadata.get("typechecked") is not True:
        return "typecheck"
    if metadata.get("prop_gate_passed") is not True:
        return "prop_gate"
    if not _has_hex64(metadata, "kernel_canonical_hash"):
        return "kernel_canonical_hash"
    if metadata.get("kernel_canonical_hash") != metadata.get("canonical_hash"):
        return "kernel_canonical_hash"
    if metadata.get("triviality_checked") is not True:
        return "triviality"
    if _triviality_budget_value(metadata) is None:
        return "triviality_budget_heartbeats"
    if metadata.get("triviality_budget_version") != TRIVIALITY_BUDGET_VERSION:
        return "triviality_budget_version"
    retarget_inputs = metadata.get("triviality_retarget_inputs")
    if not isinstance(retarget_inputs, dict):
        return "triviality_retarget_inputs"
    if retarget_inputs.get("version") != TRIVIALITY_BUDGET_VERSION:
        return "triviality_retarget_inputs"
    if retarget_inputs.get("target_tempo") != metadata.get("tempo"):
        return "triviality_retarget_inputs"
    if is_source_derived(task.source_stream, metadata):
        if metadata.get("source_import_status") != source_import_status(
            task.imports,
            metadata,
            source_path=task.source_ref.path,
        ):
            return "source_import_status"
        if metadata.get("source_oracle_checked") is not True:
            return "source_oracle"
        if metadata.get("source_oracle_solved") is True:
            return "source_oracle_solved"
    if metadata.get("baseline_solved") is True:
        return "baseline_solved"
    task_pool = parse_task_pool(metadata.get("task_pool"))
    if task_pool not in {TaskPool.SERIOUS_PAID, TaskPool.FRONTIER}:
        return f"task_pool:{task_pool.value}"
    if metadata.get("novelty_status") != "passed":
        return "novelty_status"
    if metadata.get("novelty_cache_version") != NOVELTY_CACHE_VERSION:
        return "novelty_cache_version"
    if not _has_hex64(metadata, "novelty_cache_sha256"):
        return "novelty_cache_sha256"
    if not _has_positive_int(metadata, "novelty_cache_entries"):
        return "novelty_cache_entries"
    slot_weight_reason = _slot_weight_rejection_reason(task)
    if slot_weight_reason:
        return slot_weight_reason
    if _positive_float(metadata.get("slot_weight")) is None:
        return "slot_weight"
    receipt = metadata.get("gate_receipt_sha256")
    if receipt not in {procedural_gate_receipt_sha256(task), _legacy_procedural_gate_receipt_sha256(task)}:
        return "gate_receipt_sha256"
    return ""

def procedural_gate_receipt_sha256(task: LemmaTask) -> str:
    return _procedural_gate_receipt_sha256(task, "triviality_budget_heartbeats")


def _legacy_procedural_gate_receipt_sha256(task: LemmaTask) -> str:
    metadata = task.metadata
    if _positive_float(metadata.get("triviality_budget_s")) is None:
        return ""
    return _procedural_gate_receipt_sha256(task, "triviality_budget_s")


def _procedural_gate_receipt_sha256(task: LemmaTask, budget_key: str) -> str:
    metadata = task.metadata
    payload = {
        "version": GATE_VERSION,
        "task_id": task.id,
        "target_sha256": task.target_sha256,
        "canonical_hash": metadata.get("canonical_hash"),
        "kernel_canonical_hash": metadata.get("kernel_canonical_hash"),
        "kernel_canonical_name": metadata.get("kernel_canonical_name"),
        "statement_hash": metadata.get("statement_hash"),
        "gate_runner": metadata.get("gate_runner"),
        "typechecked": metadata.get("typechecked"),
        "typecheck_reason": metadata.get("typecheck_reason"),
        "prop_gate_passed": metadata.get("prop_gate_passed"),
        "prop_gate_reason": metadata.get("prop_gate_reason"),
        "triviality_checked": metadata.get("triviality_checked"),
        "triviality_stack": metadata.get("triviality_stack"),
        budget_key: metadata.get(budget_key),
        "triviality_budget_version": metadata.get("triviality_budget_version"),
        "triviality_burn_rate_basis_points": metadata.get("triviality_burn_rate_basis_points"),
        "triviality_retarget_inputs": metadata.get("triviality_retarget_inputs"),
        "triviality_reason": metadata.get("triviality_reason"),
        "baseline_solved": metadata.get("baseline_solved"),
        "baseline_solver": metadata.get("baseline_solver"),
        "source_oracle_checked": metadata.get("source_oracle_checked"),
        "source_oracle_solved": metadata.get("source_oracle_solved"),
        "source_oracle_solver": metadata.get("source_oracle_solver"),
        "source_import_status": metadata.get("source_import_status"),
        "novelty_status": metadata.get("novelty_status"),
        "novelty_cache_version": metadata.get("novelty_cache_version"),
        "novelty_cache_entries": metadata.get("novelty_cache_entries"),
        "novelty_cache_sha256": metadata.get("novelty_cache_sha256"),
        "slot_weight": metadata.get("slot_weight"),
        "slot_weight_version": metadata.get("slot_weight_version"),
        "slot_weight_basis_points": metadata.get("slot_weight_basis_points"),
        "slot_weight_inputs": metadata.get("slot_weight_inputs"),
        "source_pool_hash": metadata.get("source_pool_hash"),
        "source_pool_receipt_sha256": metadata.get("source_pool_receipt_sha256"),
        "source_pool_source_count": metadata.get("source_pool_source_count"),
        "source_pool_stream_counts": metadata.get("source_pool_stream_counts"),
        "source_sampling_version": metadata.get("source_sampling_version"),
        "citation_alpha_basis_points": metadata.get("citation_alpha_basis_points"),
        "citation_weight_cap_micros": metadata.get("citation_weight_cap_micros"),
        "citation_window_tempos": metadata.get("citation_window_tempos"),
        "operator_bundle_version": metadata.get("operator_bundle_version"),
        "operator_bundle_hash": metadata.get("operator_bundle_hash"),
        "mutation_chain": metadata.get("mutation_chain"),
    }
    if "yield_history_version" in metadata:
        payload.update(
            {
                "yield_history_version": metadata.get("yield_history_version"),
                "yield_history_sha256": metadata.get("yield_history_sha256"),
                "yield_history_entries": metadata.get("yield_history_entries"),
            }
        )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()


def production_supply_rejections(registry: TaskRegistry, *, import_graph: ImportGraph | None = None) -> tuple[str, ...]:
    out: list[str] = []
    for task in registry.tasks:
        reason = production_supply_rejection_reason(task)
        if not reason and import_graph is not None:
            reason = _slot_weight_rejection_reason(task, import_graph=import_graph)
        if reason:
            out.append(f"{task.id}:{reason}")
    return tuple(out)


def _has_text(metadata: dict[str, Any], key: str) -> bool:
    return isinstance(metadata.get(key), str) and bool(str(metadata[key]).strip())


def _has_int(metadata: dict[str, Any], key: str) -> bool:
    return isinstance(metadata.get(key), int) and metadata[key] >= 0


def _has_positive_int(metadata: dict[str, Any], key: str) -> bool:
    return isinstance(metadata.get(key), int) and metadata[key] > 0


def _has_hex64(metadata: dict[str, Any], key: str) -> bool:
    return isinstance(metadata.get(key), str) and bool(_HEX64.fullmatch(str(metadata[key])))


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _triviality_budget_value(metadata: dict[str, Any]) -> float | None:
    return _positive_float(metadata.get("triviality_budget_heartbeats")) or _positive_float(
        metadata.get("triviality_budget_s")
    )


def _slot_weight_rejection_reason(task: LemmaTask, *, import_graph: ImportGraph | None = None) -> str:
    metadata = task.metadata
    if metadata.get("slot_weight_version") != SLOT_WEIGHT_VERSION:
        return "slot_weight_version"
    inputs = metadata.get("slot_weight_inputs")
    if not isinstance(inputs, dict):
        return "slot_weight_inputs"
    if inputs.get("kernel_dependencies_recorded") is True:
        if not _has_positive_int(inputs, "kernel_dependency_count"):
            return "slot_weight_kernel_dependencies"
        if not _has_hex64(inputs, "transitive_dependency_hash"):
            return "slot_weight_kernel_dependency_hash"
        expected = slot_weight_receipt_for_task(task)
        if metadata.get("slot_weight_basis_points") != expected.basis_points:
            return "slot_weight_basis_points"
        if metadata.get("slot_weight_inputs") != expected.inputs:
            return "slot_weight_inputs"
        return ""
    if inputs.get("import_graph_resolved") is not True:
        return "slot_weight_import_graph"
    if inputs.get("import_graph_version") != IMPORT_GRAPH_VERSION:
        return "slot_weight_import_graph_version"
    if not _has_hex64(inputs, "import_graph_sha256"):
        return "slot_weight_import_graph_sha256"
    if not _has_positive_int(inputs, "import_graph_entries"):
        return "slot_weight_import_graph_entries"
    if inputs.get("missing_import_count") != 0:
        return "slot_weight_import_graph_missing"
    expected = slot_weight_receipt_for_task(task, import_graph=import_graph)
    if metadata.get("slot_weight_basis_points") != expected.basis_points:
        return "slot_weight_basis_points"
    if metadata.get("slot_weight_inputs") != expected.inputs:
        return "slot_weight_inputs"
    observed = _positive_float(metadata.get("slot_weight"))
    if observed is None or abs(observed - expected.weight) > 1e-9:
        return "slot_weight"
    return ""


def _source_pool_rejection_reason(task: LemmaTask) -> str:
    metadata = task.metadata
    if metadata.get("source_pool_receipt_version") != SOURCE_POOL_RECEIPT_VERSION:
        return "source_pool_receipt_version"
    if metadata.get("source_sampling_version") != SOURCE_SAMPLING_VERSION:
        return "source_sampling_version"
    if not _has_positive_int(metadata, "source_pool_source_count"):
        return "source_pool_source_count"
    stream_counts = metadata.get("source_pool_stream_counts")
    if not isinstance(stream_counts, dict):
        return "source_pool_stream_counts"
    if sum(_count_value(value) for value in stream_counts.values()) != metadata.get("source_pool_source_count"):
        return "source_pool_stream_counts"
    if _count_value(stream_counts.get("mathlib_snapshot")) <= 0:
        return "source_pool_stream_counts"
    if not _has_int(metadata, "citation_alpha_basis_points"):
        return "citation_alpha_basis_points"
    if int(metadata["citation_alpha_basis_points"]) > 10_000:
        return "citation_alpha_basis_points"
    if not _has_positive_int(metadata, "citation_weight_cap_micros"):
        return "citation_weight_cap_micros"
    if not _has_positive_int(metadata, "citation_window_tempos"):
        return "citation_window_tempos"
    expected_receipt = {
        "version": SOURCE_POOL_RECEIPT_VERSION,
        "source_pool_sha256": metadata.get("source_pool_hash"),
        "source_count": metadata.get("source_pool_source_count"),
        "source_stream_counts": stream_counts,
        "sampling_version": SOURCE_SAMPLING_VERSION,
        "citation_alpha_basis_points": metadata.get("citation_alpha_basis_points"),
        "citation_weight_cap_micros": metadata.get("citation_weight_cap_micros"),
        "citation_window_tempos": metadata.get("citation_window_tempos"),
    }
    if metadata.get("source_pool_receipt_sha256") != source_pool_receipt_sha256(expected_receipt):
        return "source_pool_receipt_sha256"
    return ""


def _yield_history_rejection_reason(task: LemmaTask) -> str:
    metadata = task.metadata
    present = any(key in metadata for key in ("yield_history_version", "yield_history_sha256", "yield_history_entries"))
    if not present:
        return ""
    if metadata.get("yield_history_version") != "lemma-procedural-yield-history-v1":
        return "yield_history_version"
    if not _has_hex64(metadata, "yield_history_sha256"):
        return "yield_history_sha256"
    if not _has_int(metadata, "yield_history_entries"):
        return "yield_history_entries"
    return ""


def _count_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def enforce_production_invariants(settings: LemmaSettings, registry: TaskRegistry) -> None:
    """Fail closed when production mode lacks the Lean safety boundary."""

    if settings.protocol_mode != "production":
        return
    if tuple(settings.enabled_domains) != ("lean",):
        raise RuntimeError("production mode currently supports only lean: LEMMA_ENABLED_DOMAINS must be lean")
    if settings.task_supply_mode not in {"procedural", "ingredient"}:
        raise RuntimeError("production mode requires LEMMA_TASK_SUPPLY_MODE=procedural or ingredient")
    if settings.lean_sandbox_network.strip().lower() not in {"none", "no"}:
        raise RuntimeError("production mode requires network-disabled verifier runs")
    if not settings.require_submission_signatures:
        raise RuntimeError("production mode requires live miner authentication")
    if not settings.require_commit_reveal:
        raise RuntimeError("production mode requires LEMMA_REQUIRE_COMMIT_REVEAL=1")
    if not settings.require_strong_proof_identity:
        raise RuntimeError("production mode requires LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1")
    if settings.active_seed_mode != "epoch_randomness":
        raise RuntimeError("production mode requires LEMMA_ACTIVE_SEED_MODE=epoch_randomness")
    if settings.active_epoch_randomness_source != "chain_drand":
        raise RuntimeError("production mode requires LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand")
    if settings.task_supply_mode == "ingredient":
        _enforce_ingredient_production_invariants(settings, registry)
        return
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
    import_graph = None
    if settings.procedural_import_graph_jsonl is not None:
        import_graph = read_import_graph(settings.procedural_import_graph_jsonl)
    rejections = production_supply_rejections(registry, import_graph=import_graph)
    if rejections:
        detail = ", ".join(rejections[:5])
        raise RuntimeError(f"production mode requires paid procedural depth-2 supply: {detail}")


def _normalize_sha256(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw.startswith("sha256:"):
        raw = raw.removeprefix("sha256:")
    return raw


def _read_ingredient_production_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"ingredient production mode {label} path invalid")
    try:
        return path.read_bytes()
    except OSError as e:
        raise RuntimeError(f"ingredient production mode {label} unreadable") from e


def _enforce_ingredient_production_invariants(settings: LemmaSettings, registry: TaskRegistry) -> None:
    if registry.schema_version != 1:
        raise RuntimeError("ingredient production mode registry schema_version mismatch")
    if registry.created_at is not None:
        raise RuntimeError("ingredient production mode registry has local side channel")
    if registry.signature_status not in {"unsigned", "metadata_only", "verified"}:
        raise RuntimeError("ingredient production mode registry signature status mismatch")
    if (
        registry.signature_status == "metadata_only"
        or registry.signed_by is not None
        or registry.signature is not None
    ) and registry.signature_status != "verified":
        raise RuntimeError("ingredient production mode registry signature unverified")

    expected_manifest = _normalize_sha256(settings.ingredient_manifest_sha256_expected)
    if not expected_manifest or not _HEX64.fullmatch(expected_manifest):
        raise RuntimeError("ingredient production mode requires LEMMA_INGREDIENT_MANIFEST_SHA256")
    if settings.ingredient_manifest_json is None:
        raise RuntimeError("ingredient production mode requires LEMMA_INGREDIENT_MANIFEST_JSON")
    manifest_bytes = _read_ingredient_production_file(settings.ingredient_manifest_json, "manifest")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != expected_manifest:
        raise RuntimeError("ingredient production mode manifest sha256 mismatch")
    manifest_payload = _ingredient_manifest_payload(manifest_bytes)
    manifest_mathlib_commit = manifest_payload.mathlib_commit.strip()
    if not manifest_mathlib_commit or manifest_payload.mathlib_commit != manifest_mathlib_commit:
        raise RuntimeError("ingredient production mode manifest requires mathlib_commit")
    if manifest_payload.lemma_corpus_snapshot_sha256 is None:
        raise RuntimeError("ingredient production mode manifest requires lemma corpus snapshot sha256")

    repo_commit = settings.ingredient_repo_commit.strip()
    if not repo_commit or settings.ingredient_repo_commit != repo_commit:
        raise RuntimeError("ingredient production mode requires LEMMA_INGREDIENT_REPO_COMMIT")
    expected_recipe = _normalize_sha256(settings.ingredient_recipe_bundle_sha256_expected)
    if not expected_recipe or not _HEX64.fullmatch(expected_recipe):
        raise RuntimeError("ingredient production mode requires LEMMA_INGREDIENT_RECIPE_BUNDLE_SHA256")
    manifest_recipe = _normalize_sha256(manifest_payload.recipe_bundle_sha256)
    if not manifest_recipe:
        raise RuntimeError("ingredient production mode manifest requires recipe_bundle_sha256")
    if manifest_recipe != expected_recipe:
        raise RuntimeError("ingredient production mode manifest recipe bundle sha256 mismatch")
    if settings.ingredient_difficulty_state_jsonl is None:
        raise RuntimeError("ingredient production mode requires LEMMA_INGREDIENT_DIFFICULTY_STATE_JSONL")
    difficulty_state_bytes = _read_ingredient_production_file(
        settings.ingredient_difficulty_state_jsonl,
        "difficulty state",
    )
    if not difficulty_state_bytes.strip():
        raise RuntimeError("ingredient production mode requires nonempty LEMMA_INGREDIENT_DIFFICULTY_STATE_JSONL")
    try:
        difficulty_state_records = ingredient_difficulty_state_records(difficulty_state_bytes)
    except ValueError as e:
        raise RuntimeError(f"ingredient production mode {e}") from e

    if len(registry.tasks) != settings.active_task_count:
        raise RuntimeError("ingredient production mode active task count mismatch")
    difficulty_sha256 = hashlib.sha256(difficulty_state_bytes).hexdigest()
    expected_queue_positions = set(range(settings.active_task_count))
    seen_task_ids: set[str] = set()
    seen_queue_positions: set[int] = set()
    seen_selection_seeds: set[str] = set()
    for active_task in registry.tasks:
        if active_task.id in seen_task_ids:
            raise RuntimeError("ingredient production mode duplicate active task id")
        seen_task_ids.add(active_task.id)

        _enforce_ingredient_task_production_invariants(
            active_task,
            settings=settings,
            manifest_payload=manifest_payload,
            manifest_mathlib_commit=manifest_mathlib_commit,
            expected_manifest=expected_manifest,
            repo_commit=repo_commit,
            expected_recipe=expected_recipe,
            difficulty_sha256=difficulty_sha256,
            difficulty_state_records=difficulty_state_records,
        )

        queue_position = active_task.queue_position
        if queue_position is None or queue_position in seen_queue_positions:
            raise RuntimeError("ingredient production mode task queue position mismatch")
        seen_queue_positions.add(queue_position)

        metadata = active_task.metadata
        selection_seed = str(metadata["selection_seed_sha256"])
        if selection_seed in seen_selection_seeds:
            raise RuntimeError("ingredient production mode task selection seed duplicated")
        seen_selection_seeds.add(selection_seed)
        _enforce_ingredient_task_generation_receipt(active_task)

    if seen_queue_positions != expected_queue_positions:
        raise RuntimeError("ingredient production mode task queue position mismatch")

    if not _HEX64.fullmatch(registry.sha256) or registry.sha256 == "0" * 64:
        raise RuntimeError("ingredient production mode requires non-placeholder registry sha256")
    if registry.signature_status == "verified" and not (
        isinstance(registry.signed_by, str)
        and registry.signed_by
        and registry.signed_by == registry.signed_by.strip()
        and isinstance(registry.signature, str)
        and registry.signature
        and registry.signature == registry.signature.strip()
    ):
        raise RuntimeError("ingredient production mode registry verified signature metadata missing")


def _enforce_ingredient_task_production_invariants(
    task: LemmaTask,
    *,
    settings: LemmaSettings,
    manifest_payload: IngredientManifest,
    manifest_mathlib_commit: str,
    expected_manifest: str,
    repo_commit: str,
    expected_recipe: str,
    difficulty_sha256: str,
    difficulty_state_records: tuple[dict[str, object], ...],
) -> None:
    if task.source_stream != "ingredient":
        raise RuntimeError("ingredient production mode requires source_stream=ingredient")
    if task.source_ref.kind != "ingredient":
        raise RuntimeError("ingredient production mode requires source_ref.kind=ingredient")
    if task.source_ref.url is not None or task.source_ref.path is not None:
        raise RuntimeError("ingredient production mode source_ref url/path must be empty")
    reward = task_reward_eligibility(task)
    if not reward.eligible:
        raise RuntimeError(f"ingredient production mode requires reward-eligible task: {reward.reason}")
    if task.source_license != "Apache-2.0":
        raise RuntimeError("ingredient production mode source license mismatch")
    if task.task_version != 1:
        raise RuntimeError("ingredient production mode task_version mismatch")
    try:
        validate_ingredient_task_title(task.title)
    except ValueError as e:
        raise RuntimeError(f"ingredient production mode task title invalid: {e}") from e
    if task.triviality_status != "unknown":
        raise RuntimeError("ingredient production mode task triviality status mismatch")
    if task.active_epoch is not None or task.expires_epoch is not None or "created_at_block" in task.metadata:
        raise RuntimeError("ingredient production mode task lifecycle window mismatch")
    if task.policy != "restricted_helpers":
        raise RuntimeError("ingredient production mode requires restricted_helpers submission policy")
    if task.verifier_id != LEAN_VERIFIER_ID or task.verifier_version != LEAN_VERIFIER_VERSION:
        raise RuntimeError("ingredient production mode task verifier identity mismatch")
    if task.lean_toolchain != DEFAULT_TOOLCHAIN:
        raise RuntimeError("ingredient production mode task lean toolchain mismatch")
    try:
        validate_ingredient_imports(task.imports)
    except ValueError as e:
        raise RuntimeError(f"ingredient production mode task import envelope invalid: {e}") from e
    try:
        validate_ingredient_statement_header(
            theorem_name=task.theorem_name,
            type_expr=task.type_expr,
            statement=task.statement,
        )
    except ValueError as e:
        if str(e) == "ingredient theorem statement header mismatch":
            raise RuntimeError("ingredient production mode task theorem header mismatch") from e
        raise RuntimeError(f"ingredient production mode task theorem statement invalid: {e}") from e
    if task.submission_stub != ingredient_submission_stub(task.theorem_name, task.type_expr, task.imports):
        raise RuntimeError("ingredient production mode task submission stub mismatch")
    if task.queue_depth > settings.frontier_depth:
        raise RuntimeError("ingredient production mode task outside active frontier")
    if task.frontier_depth != settings.frontier_depth:
        raise RuntimeError("ingredient production mode task frontier depth mismatch")
    if task.queue_position is None:
        raise RuntimeError("ingredient production mode task queue position mismatch")
    metadata = task.metadata
    if _INGREDIENT_TASK_FIELD_SHADOW_KEYS & metadata.keys():
        raise RuntimeError("ingredient production mode task metadata shadows task field")
    if _INGREDIENT_METADATA_SIDE_CHANNEL_KEYS & metadata.keys():
        raise RuntimeError("ingredient production mode task metadata has local side channel")
    if set(metadata) - _INGREDIENT_TASK_METADATA_KEYS:
        raise RuntimeError("ingredient production mode task metadata schema mismatch")
    if task.mathlib_rev != manifest_mathlib_commit or metadata.get("mathlib_commit") != manifest_mathlib_commit:
        raise RuntimeError("ingredient production mode manifest mathlib commit mismatch")
    if metadata.get("supply_mode") != "ingredient":
        raise RuntimeError("ingredient production mode requires task supply_mode=ingredient")
    if metadata.get("ingredient_manifest_sha256") != expected_manifest:
        raise RuntimeError("ingredient production mode task manifest sha256 mismatch")
    if metadata.get("lemma_corpus_snapshot_sha256") != manifest_payload.lemma_corpus_snapshot_sha256:
        raise RuntimeError("ingredient production mode task corpus snapshot sha256 mismatch")
    if metadata.get("ingredient_repo_commit") != repo_commit:
        raise RuntimeError("ingredient production mode task repo commit mismatch")
    if task.source_ref.commit != repo_commit:
        raise RuntimeError("ingredient production mode source_ref commit mismatch")
    if metadata.get("recipe_bundle_sha256") != expected_recipe:
        raise RuntimeError("ingredient production mode task recipe bundle sha256 mismatch")
    if metadata.get("difficulty_state_sha256") != difficulty_sha256:
        raise RuntimeError("ingredient production mode task difficulty state sha256 mismatch")
    tempo = metadata.get("tempo")
    if not _exact_int(tempo):
        raise RuntimeError("ingredient production mode task tempo mismatch")
    active_difficulty_lanes = ingredient_difficulty_state_active_lanes(difficulty_state_records, tempo=tempo)
    if len(active_difficulty_lanes) > 1:
        raise RuntimeError("ingredient production mode difficulty state has ambiguous active tempo")
    if active_difficulty_lanes != [metadata.get("difficulty_lane")]:
        raise RuntimeError("ingredient production mode difficulty state missing active tempo/lane")
    active_k = metadata.get("active_K")
    if not _exact_int(active_k) or active_k != settings.active_task_count:
        raise RuntimeError("ingredient production mode task active_K mismatch")
    recipe_id = metadata.get("recipe_id")
    if not isinstance(recipe_id, str) or not recipe_id.strip() or recipe_id != recipe_id.strip():
        raise RuntimeError("ingredient production mode task recipe_id missing")
    selector_id = metadata.get("selector_id")
    if not isinstance(selector_id, str) or not selector_id.strip() or selector_id != selector_id.strip():
        raise RuntimeError("ingredient production mode task selector_id missing")
    if task.source_ref.name != recipe_id:
        raise RuntimeError("ingredient production mode source_ref recipe mismatch")
    if metadata.get("difficulty_lane") != task.difficulty_band:
        raise RuntimeError("ingredient production mode task difficulty lane mismatch")
    definition_ids = _text_list(metadata.get("definition_ids"))
    fact_ids = _text_list(metadata.get("fact_ids"))
    bridge_ids = _text_list(metadata.get("bridge_ids"))
    ingredient_ids = _text_list(metadata.get("ingredient_ids"))
    if definition_ids is None or fact_ids is None or bridge_ids is None or ingredient_ids is None:
        raise RuntimeError("ingredient production mode task ingredient metadata malformed")
    if not ingredient_ids:
        raise RuntimeError("ingredient production mode task selected ingredients missing")
    if not definition_ids:
        raise RuntimeError("ingredient production mode task selected definitions missing")
    if not fact_ids:
        raise RuntimeError("ingredient production mode task selected facts missing")
    if len(set(ingredient_ids)) != len(ingredient_ids):
        raise RuntimeError("ingredient production mode task selected ingredients duplicated")
    if ingredient_ids != [*definition_ids, *fact_ids, *bridge_ids]:
        raise RuntimeError("ingredient production mode task ingredient metadata mismatch")
    ingredient_count = metadata.get("ingredient_count")
    if not _exact_int(ingredient_count) or ingredient_count != len(ingredient_ids):
        raise RuntimeError("ingredient production mode task ingredient count mismatch")
    hidden_lemma_count = metadata.get("hidden_lemma_count")
    if not _exact_int(hidden_lemma_count) or hidden_lemma_count != 0:
        raise RuntimeError("ingredient production mode task hidden lemma count mismatch")
    if not _has_hex64(metadata, "novelty_family_hash"):
        raise RuntimeError("ingredient production mode task novelty family hash missing")
    if not _has_hex64(metadata, "epoch_seed_sha256"):
        raise RuntimeError("ingredient production mode task epoch seed missing")
    if metadata.get("epoch_seed_sha256") == "0" * 64:
        raise RuntimeError("ingredient production mode task epoch seed placeholder")
    if not _has_hex64(metadata, "selection_seed_sha256"):
        raise RuntimeError("ingredient production mode task selection seed missing")
    if metadata.get("selection_seed_sha256") == "0" * 64:
        raise RuntimeError("ingredient production mode task selection seed placeholder")
    try:
        validate_ingredient_selected_parameters(metadata.get("selected_parameters"))
    except ValueError as e:
        raise RuntimeError("ingredient production mode task selected parameters malformed") from e
    if not _has_hex64(metadata, "generation_receipt_sha256"):
        raise RuntimeError("ingredient production mode task generation receipt missing")
    if not _has_hex64(metadata, "gate_receipt_sha256"):
        raise RuntimeError("ingredient production mode task gate receipt missing")
    if not _has_hex64(metadata, "shortcut_receipt_sha256"):
        raise RuntimeError("ingredient production mode task shortcut receipt missing")
    if metadata.get("gate_receipt_sha256") == "0" * 64:
        raise RuntimeError("ingredient production mode task gate receipt placeholder")
    if metadata.get("shortcut_receipt_sha256") == "0" * 64:
        raise RuntimeError("ingredient production mode task shortcut receipt placeholder")
    if task.target_sha256 != problem_target_sha256(task.to_problem()):
        raise RuntimeError("ingredient production mode task target hash mismatch")
    if metadata.get("active_target_sha256") != task.target_sha256:
        raise RuntimeError("ingredient production mode task active target mismatch")
    if metadata.get("theorem_statement_sha256") != text_sha256(task.statement):
        raise RuntimeError("ingredient production mode task theorem statement hash mismatch")
    try:
        expected_novelty_family = expected_ingredient_novelty_family_hash(task)
    except ValueError as e:
        raise RuntimeError("ingredient production mode task generation receipt malformed") from e
    if metadata.get("novelty_family_hash") != expected_novelty_family:
        raise RuntimeError("ingredient production mode task novelty family hash mismatch")


def _enforce_ingredient_task_generation_receipt(task: LemmaTask) -> None:
    try:
        expected_receipt = expected_ingredient_generation_receipt_sha256(task)
    except ValueError as e:
        raise RuntimeError("ingredient production mode task generation receipt malformed") from e
    metadata = task.metadata
    if metadata.get("generation_receipt_sha256") != expected_receipt:
        raise RuntimeError("ingredient production mode task generation receipt mismatch")


def _text_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) and item.strip() and item == item.strip() for item in value):
        return None
    return value


def _exact_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _ingredient_manifest_payload(raw: bytes) -> IngredientManifest:
    try:
        return IngredientManifest.model_validate_json(raw)
    except ValidationError as e:
        raise RuntimeError("ingredient production mode requires valid ingredient manifest schema") from e

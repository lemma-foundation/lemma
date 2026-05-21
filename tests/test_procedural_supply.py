from __future__ import annotations

import json
from pathlib import Path

from lemma.common.config import LemmaSettings
from lemma.protocol_invariants import enforce_production_invariants
from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
from lemma.supply.procedural import (
    generate_depth2_candidates,
    procedural_operator_bundle_hash,
    source_pool_hash,
)
from lemma.validator import active_epoch_seed, active_tasks_for_validation, task_registry_for_validation


def _write_snapshot(path: Path) -> None:
    rows = [
        {
            "theorem_name": "True.intro",
            "type_expr": "True",
            "imports": ["Mathlib"],
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Init.lean",
            "source_license": "Apache-2.0",
            "queue_depth": 0,
        },
        {
            "theorem_name": "Eq.refl",
            "type_expr": "∀ n : Nat, n = n",
            "imports": ["Mathlib"],
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Init.lean",
            "source_license": "Apache-2.0",
            "queue_depth": 0,
        },
    ]
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_depth2_generation_is_epoch_seeded_not_static(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    sources = mathlib_candidates_from_jsonl(snapshot)
    randomness = json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True)

    first = generate_depth2_candidates(
        sources,
        generation_seed="epoch-a",
        epoch_randomness=randomness,
        count=2,
        tempo=3,
    )
    repeat = generate_depth2_candidates(
        sources,
        generation_seed="epoch-a",
        epoch_randomness=randomness,
        count=2,
        tempo=3,
    )
    next_epoch = generate_depth2_candidates(
        sources,
        generation_seed="epoch-b",
        epoch_randomness=randomness,
        count=2,
        tempo=4,
    )

    assert [candidate.id for candidate in first] == [candidate.id for candidate in repeat]
    assert [candidate.id for candidate in first] != [candidate.id for candidate in next_epoch]
    assert all(candidate.source_stream == "procedural" for candidate in first)
    assert all(candidate.metadata["mutation_depth"] == 2 for candidate in first)
    assert all(len(candidate.metadata["mutation_chain"]) == 2 for candidate in first)
    assert all(candidate.metadata["source_pool_hash"] == source_pool_hash(sources) for candidate in first)


def test_procedural_supply_mode_rebuilds_active_registry_from_public_inputs(
    monkeypatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    sources = mathlib_candidates_from_jsonl(snapshot)
    source_hash = source_pool_hash(sources)
    randomness = json.dumps(
        {
            "source": "chain_drand",
            "anchor_block": 720,
            "anchor_block_hash": "0xabc",
            "drand_round": 11,
            "drand_signature": "0xsig",
        },
        sort_keys=True,
    )
    monkeypatch.setattr("lemma.validator.resolve_active_epoch_randomness", lambda settings, *, tempo: randomness)
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        procedural_source_jsonl=snapshot,
        procedural_source_sha256_expected=source_hash,
        procedural_operator_bundle_sha256_expected=procedural_operator_bundle_hash(),
        procedural_candidate_count=2,
        protocol_mode="production",
        active_task_count=2,
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
        lean_sandbox_network="none",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
    )

    registry = task_registry_for_validation(settings, tempo=3)
    active = active_tasks_for_validation(registry, settings, tempo=3)

    enforce_production_invariants(settings, registry)
    assert len(registry.tasks) == 2
    assert len(active) == 2
    assert {task.source_stream for task in active} == {"procedural"}
    assert {task.metadata["generation_seed"] for task in active} == {active_epoch_seed(settings, tempo=3)}
    assert {task.metadata["anchor_block"] for task in active} == {720}
    assert {task.metadata["drand_round"] for task in active} == {11}

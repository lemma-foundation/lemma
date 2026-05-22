from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.corpus import build_corpus_row, write_jsonl
from lemma.lean.sandbox import VerifyResult
from lemma.protocol_invariants import enforce_production_invariants
from lemma.submissions import build_submission
from lemma.supply.gates import AssumedProceduralGateRunner, LeanProceduralGateRunner, ProceduralGateVerdict
from lemma.supply.import_graph import ImportGraphRow, extract_import_graph_rows, read_import_graph
from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
from lemma.supply.mutation import LeanAstMutationEngine, PreviewMutationEngine
from lemma.supply.novelty import novelty_cache_from_hashes, read_novelty_cache, statement_hash
from lemma.supply.operator_bundle import OPERATOR_BUNDLE_VERSION, OPERATOR_NAMES
from lemma.supply.procedural import (
    build_procedural_registry_tasks,
    corpus_sources_from_dir,
    generate_depth2_candidates,
    procedural_operator_bundle_hash,
    source_pool_hash,
)
from lemma.supply.slot_weight import slot_weight_receipt_for_candidate
from lemma.supply.triviality_budget import TrivialityRetargetConfig, triviality_budget_receipt
from lemma.supply.types import fixture_candidate
from lemma.task_supply import make_task
from lemma.validator import active_epoch_seed, active_tasks_for_validation, task_registry_for_validation


class _PreviewMutationEngineForProduction:
    def __init__(self, settings: LemmaSettings) -> None:
        _ = settings
        self.preview = PreviewMutationEngine()

    def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001
        result = self.preview.apply(source, type_expr, operator, step=step, param_seed=param_seed, peer=peer)
        return result.__class__(result.type_expr, {**result.params, "engine": "lean_ast_elaborator"})


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


def _write_novelty_cache(path: Path) -> None:
    path.write_text(json.dumps({"statement_hash": "0" * 64}, sort_keys=True) + "\n", encoding="utf-8")


def _write_import_graph(path: Path) -> None:
    rows = (
        ImportGraphRow(module="Mathlib", imports=("Mathlib.Init", "Mathlib.Data.Nat.Basic")),
        ImportGraphRow(module="Mathlib.Init", imports=()),
        ImportGraphRow(module="Mathlib.Data.Nat.Basic", imports=("Mathlib.Init",)),
        ImportGraphRow(module="Mathlib.Algebra.Group.Basic", imports=("Mathlib.Init",)),
    )
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


def test_import_graph_accepts_prime_module_names(tmp_path: Path) -> None:
    mathlib_root = tmp_path / "mathlib"
    module_path = mathlib_root / "Mathlib" / "Tactic" / "LinearCombination'.lean"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("import Mathlib.Tactic.Widget'\n", encoding="utf-8")

    rows = extract_import_graph_rows(mathlib_root, ("Mathlib/Tactic/LinearCombination'.lean",))

    assert rows == (
        ImportGraphRow(module="Mathlib.Tactic.LinearCombination'", imports=("Mathlib.Tactic.Widget'",)),
    )


def _fake_lean_gate(self, candidate, *, seen_canonical_hashes) -> ProceduralGateVerdict:  # noqa: ANN001
    canonical_hash = str(candidate.metadata.get("canonical_hash") or "")
    slot_weight = slot_weight_receipt_for_candidate(candidate, import_graph=self.import_graph)
    novelty_cache = novelty_cache_from_hashes(("0" * 64,))
    triviality_budget = triviality_budget_receipt(
        (),
        tempo=int(candidate.metadata["tempo"]),
        config=TrivialityRetargetConfig(genesis_budget_s=5, max_budget_s=5),
    )
    return ProceduralGateVerdict(
        typechecked=True,
        prop_gate_passed=True,
        triviality_checked=True,
        baseline_solved=False,
        novelty_status="duplicate" if canonical_hash in set(seen_canonical_hashes) else "passed",
        slot_weight=slot_weight.weight,
        metadata={
            "gate_runner": "lean",
            "typecheck_reason": "ok",
            "prop_gate_reason": "ok",
            "kernel_canonical_hash": canonical_hash,
            "kernel_canonical_name": "LemmaProceduralGate.prop_gate",
            "triviality_stack": ["pytest"],
            "triviality_reason": "baseline_failed",
            "baseline_solver": None,
            **novelty_cache.metadata(),
            **triviality_budget.metadata(),
            **slot_weight.metadata(),
        },
    )


def test_lean_ast_mutation_engine_uses_lean_eval_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_run_lean_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        captured["settings"] = settings
        captured["timeout"] = verify_timeout_s
        captured["problem"] = problem
        captured["proof_script"] = proof_script
        captured["submission_policy"] = submission_policy
        return VerifyResult(
            passed=True,
            reason="ok",
            stdout_tail=(
                'LEMMA_AST_MUTATION {"params":{"binder":"p","binder_type":"Prop"},'
                '"type_expr":"∀ p : Prop, p → True"}'
            ),
        )

    monkeypatch.setattr("lemma.supply.mutation.run_lean_verify", fake_run_lean_verify)
    source = fixture_candidate(
        slug="source_true",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source_true",
        type_expr="True",
        queue_depth=0,
    )

    result = LeanAstMutationEngine(LemmaSettings(_env_file=None, lean_use_docker=False)).apply(
        source,
        "True",
        "generalize",
        step=0,
        param_seed="a" * 64,
        peer=source,
    )

    assert result.type_expr == "∀ p : Prop, p → True"
    assert result.params["engine"] == "lean_ast_elaborator"
    assert "replaceIdent" in captured["problem"].extra["challenge_full"]
    assert captured["problem"].extra["lean_eval_commands"] == ("#eval! LemmaProceduralMutator.emit",)
    assert "theorem lemma_ast_mutation_dummy : True" in captured["proof_script"]


def test_depth2_generation_is_epoch_seeded_not_static(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    novelty_cache = tmp_path / "novelty.jsonl"
    _write_snapshot(snapshot)
    _write_novelty_cache(novelty_cache)
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
    assert all(candidate.metadata["operator_bundle_version"] == OPERATOR_BUNDLE_VERSION for candidate in first)
    assert all(
        step["operator"] in OPERATOR_NAMES and isinstance(step["params"], dict)
        for candidate in first
        for step in candidate.metadata["mutation_chain"]
    )
    assert all(candidate.metadata["source_pool_hash"] == source_pool_hash(sources) for candidate in first)
    assert all(
        candidate.metadata["source_pool_receipt_version"] == "lemma-source-pool-receipt-v1" for candidate in first
    )
    assert all(candidate.metadata["source_sampling_version"] == "lemma-source-sampling-v1" for candidate in first)
    assert all(candidate.metadata["source_pool_stream_counts"] == {"mathlib_snapshot": 2} for candidate in first)
    assert all(candidate.metadata["citation_alpha_basis_points"] == 5000 for candidate in first)
    assert all(candidate.metadata["citation_window_tempos"] == 2000 for candidate in first)


def test_depth2_generation_skips_failed_mutations(tmp_path: Path) -> None:
    class FlakyMutationEngine:
        def __init__(self) -> None:
            self.calls = 0
            self.preview = PreviewMutationEngine()

        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001
            self.calls += 1
            if self.calls == 1:
                raise ValueError("bad generated mutation")
            return self.preview.apply(source, type_expr, operator, step=step, param_seed=param_seed, peer=peer)

    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    engine = FlakyMutationEngine()

    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
        mutation_engine=engine,
    )

    assert len(candidates) == 1
    assert engine.calls >= 3


def test_procedural_registry_rejects_assumed_gate_receipts(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
        mutation_engine=_PreviewMutationEngineForProduction(LemmaSettings(_env_file=None)),
    )

    build = build_procedural_registry_tasks(candidates, seed="epoch-a")

    assert build.tasks == ()
    assert build.rejected[0].reason == "gate_runner"


def test_lean_gate_runner_records_generation_time_gates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidate = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
    )[0]
    calls: list[str] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        if problem.id.endswith(".gate"):
            assert problem.extra["lean_build_target"] == "Challenge"
            gate_source = str(problem.extra["challenge_full"])
            is_typecheck = "def typecheck_gate" in gate_source
            if not is_typecheck:
                assert problem.extra["lean_eval_commands"] == ("#eval! LemmaProceduralGate.emit_kernel_normal",)
            calls.append("typecheck" if is_typecheck else "prop")
            return VerifyResult(
                passed=True,
                reason="ok",
                stdout_tail=""
                if is_typecheck
                else "LEMMA_KERNEL_NORMAL_FORM (forall default const:True:[] const:True:[])",
                declaration_fingerprints={str(problem.extra["lean_fingerprint_names"][0]): "8" * 64},
            )
        calls.append("triviality")
        return VerifyResult(passed=False, reason="compile_error")

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdict = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    )(candidate, seen_canonical_hashes=())

    assert verdict.accepted is True
    assert verdict.metadata["gate_runner"] == "lean"
    assert verdict.metadata["novelty_cache_version"] == "lemma-novelty-cache-v1"
    kernel_hash = hashlib.sha256(b"(forall default const:True:[] const:True:[])").hexdigest()
    assert verdict.metadata["kernel_canonical_hash"] == kernel_hash
    assert verdict.metadata["canonical_hash"] == kernel_hash
    assert verdict.metadata["triviality_budget_version"] == "lemma-triviality-retarget-v1"
    assert verdict.metadata["triviality_reason"] == "baseline_failed"
    assert calls[:2] == ["typecheck", "prop"]
    assert "triviality" in calls


def test_public_novelty_cache_marks_known_statement_duplicate(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidate = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
    )[0]
    cache = novelty_cache_from_hashes((str(candidate.metadata["statement_hash"]),))
    verdict = AssumedProceduralGateRunner(novelty_cache=cache)(candidate, seen_canonical_hashes=())

    assert verdict.novelty_status == "duplicate"
    assert verdict.accepted is False


def test_public_novelty_cache_can_be_built_from_type_expr_rows(tmp_path: Path) -> None:
    path = tmp_path / "novelty.jsonl"
    path.write_text(json.dumps({"type_expr": "True   →   True"}, sort_keys=True) + "\n", encoding="utf-8")

    cache = read_novelty_cache(path)

    assert cache.contains(statement_hash("True → True"))


def test_procedural_slot_weight_receipt_uses_dependency_metadata(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(
        json.dumps(
            {
                "theorem_name": "Deep.weight",
                "type_expr": "True",
                "imports": ["Mathlib.Data.Nat.Basic", "Mathlib.Algebra.Group.Basic"],
                "mathlib_rev": "abc123",
                "source_path": "Mathlib/Deep.lean",
                "source_license": "Apache-2.0",
                "queue_depth": 2,
                "citation_weight": 7.5,
                "direct_dependency_count": 11,
                "dependency_depth": 5,
                "transitive_dependency_hash": "a" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    candidate = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
    )[0]
    inputs = candidate.metadata["slot_weight_inputs"]

    assert candidate.metadata["slot_weight_version"] == "lemma-slot-weight-v3"
    assert inputs["direct_dependency_count"] == 11
    assert inputs["dependency_depth"] == 5
    assert inputs["import_breadth"] == 2
    assert candidate.metadata["slot_weight_basis_points"] > 1000


def test_procedural_slot_weight_receipt_uses_public_import_graph(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    import_graph_path = tmp_path / "import-graph.jsonl"
    _write_snapshot(snapshot)
    _write_import_graph(import_graph_path)
    import_graph = read_import_graph(import_graph_path)

    candidate = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
        gate_runner=AssumedProceduralGateRunner(import_graph=import_graph),
    )[0]
    inputs = candidate.metadata["slot_weight_inputs"]

    assert candidate.metadata["slot_weight_version"] == "lemma-slot-weight-v3"
    assert inputs["import_graph_resolved"] is True
    assert inputs["import_graph_sha256"] == import_graph.sha256
    assert inputs["missing_import_count"] == 0
    assert inputs["direct_dependency_count"] == 2
    assert inputs["transitive_dependency_count"] == 2


def test_procedural_supply_mode_rebuilds_active_registry_from_public_inputs(
    monkeypatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    novelty_cache = tmp_path / "novelty.jsonl"
    import_graph = tmp_path / "import-graph.jsonl"
    corpus_dir = tmp_path / "corpus"
    _write_snapshot(snapshot)
    _write_novelty_cache(novelty_cache)
    _write_import_graph(import_graph)
    corpus_dir.mkdir()
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
    monkeypatch.setattr("lemma.supply.gates.LeanProceduralGateRunner.__call__", _fake_lean_gate)
    monkeypatch.setattr("lemma.supply.mutation.LeanAstMutationEngine", _PreviewMutationEngineForProduction)
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        procedural_source_jsonl=snapshot,
        procedural_novelty_cache_jsonl=novelty_cache,
        procedural_import_graph_jsonl=import_graph,
        procedural_prior_corpus_dir=corpus_dir,
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


def test_procedural_source_pool_includes_prior_accepted_corpus(
    monkeypatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    novelty_cache = tmp_path / "novelty.jsonl"
    import_graph = tmp_path / "import-graph.jsonl"
    _write_snapshot(snapshot)
    _write_novelty_cache(novelty_cache)
    _write_import_graph(import_graph)
    corpus_dir = tmp_path / "corpus"
    task = make_task(
        task_id="lemma.accepted.prior",
        title="Prior accepted",
        theorem_name="prior_true",
        type_expr="True",
        source_stream="procedural",
        source_name="tempo-1",
        source_license="Apache-2.0",
        metadata={"activation_status": "paid", "triviality_checked": True, "baseline_solved": False},
    )
    row = build_corpus_row(
        task,
        build_submission(
            task,
            solver_hotkey="hk",
            proof_script="import Mathlib\n\ntheorem prior_true : True := by\n  trivial\n",
        ),
        VerifyResult(passed=True, reason="ok", proof_term_hash="prior-term"),
        validator_hotkey="vhk",
        rewarded=True,
        tempo=1,
    )
    write_jsonl([row], corpus_dir / "epoch-000001.jsonl")
    sources = mathlib_candidates_from_jsonl(snapshot) + corpus_sources_from_dir(corpus_dir, before_tempo=3)
    source_hash = source_pool_hash(sources)
    monkeypatch.setattr(
        "lemma.validator.resolve_active_epoch_randomness",
        lambda settings, *, tempo: json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
    )
    monkeypatch.setattr("lemma.supply.gates.LeanProceduralGateRunner.__call__", _fake_lean_gate)
    monkeypatch.setattr("lemma.supply.mutation.LeanAstMutationEngine", _PreviewMutationEngineForProduction)
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        procedural_source_jsonl=snapshot,
        procedural_novelty_cache_jsonl=novelty_cache,
        procedural_import_graph_jsonl=import_graph,
        procedural_prior_corpus_dir=corpus_dir,
        procedural_source_sha256_expected=source_hash,
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

    assert any(source.source_stream == "lemma_substrate" for source in sources)
    assert {task.metadata["source_pool_hash"] for task in registry.tasks} == {source_hash}
    assert {
        tuple(sorted(task.metadata["source_pool_stream_counts"].items())) for task in registry.tasks
    } == {(("lemma_substrate", 1), ("mathlib_snapshot", 2))}
    assert {task.metadata["citation_window_tempos"] for task in registry.tasks} == {2000}


def test_procedural_source_pool_ignores_non_rewarded_corpus_rows(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    task = make_task(
        task_id="lemma.accepted.prior",
        title="Prior accepted",
        theorem_name="prior_true",
        type_expr="True",
        source_stream="procedural",
        source_name="tempo-1",
        source_license="Apache-2.0",
        metadata={"activation_status": "paid", "triviality_checked": True, "baseline_solved": False},
    )
    submission = build_submission(
        task,
        solver_hotkey="hk",
        proof_script="import Mathlib\n\ntheorem prior_true : True := by\n  trivial\n",
    )
    rewarded = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok", proof_term_hash="prior-term"),
        validator_hotkey="vhk",
        rewarded=True,
        tempo=1,
    )
    alternate = build_corpus_row(
        task,
        submission.model_copy(update={"solver_hotkey": "hk-late"}),
        VerifyResult(passed=True, reason="ok", proof_term_hash="alternate-term"),
        validator_hotkey="vhk",
        rewarded=False,
        tempo=1,
    )
    write_jsonl([rewarded, alternate], corpus_dir / "epoch-000001.jsonl")

    sources = corpus_sources_from_dir(corpus_dir, before_tempo=3)

    assert [source.metadata["substrate_row_id"] for source in sources] == [rewarded.row_id]


def test_procedural_source_pool_reads_canonical_accepted_entry_directories(tmp_path: Path) -> None:
    from lemma.corpus.storage import build_epoch_storage_from_rows

    canonical_root = tmp_path / "canonical"
    task = make_task(
        task_id="lemma.accepted.canonical",
        title="Canonical accepted",
        theorem_name="canonical_true",
        type_expr="True",
        source_stream="procedural",
        source_name="tempo-1",
        source_license="Apache-2.0",
        metadata={"activation_status": "paid", "triviality_checked": True, "baseline_solved": False},
    )
    row = build_corpus_row(
        task,
        build_submission(
            task,
            solver_hotkey="hk",
            proof_script="import Mathlib\n\ntheorem canonical_true : True := by\n  trivial\n",
        ),
        VerifyResult(passed=True, reason="ok", proof_term_hash="canonical-term"),
        validator_hotkey="vhk",
        rewarded=True,
        tempo=1,
    )
    build_epoch_storage_from_rows([row], canonical_root, netuid="sn467", tempo=1, resolver="hippius-ipfs")

    sources = corpus_sources_from_dir(canonical_root, before_tempo=3)

    assert [source.metadata["substrate_row_id"] for source in sources] == [row.row_id]


def test_procedural_source_pool_weights_lemma_rows_by_recent_citations(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    base_task = make_task(
        task_id="lemma.accepted.base",
        title="Base accepted",
        theorem_name="base_true",
        type_expr="True",
        source_stream="procedural",
        source_name="tempo-1",
        source_license="Apache-2.0",
        metadata={"activation_status": "paid", "triviality_checked": True, "baseline_solved": False},
    )
    base_submission = build_submission(
        base_task,
        solver_hotkey="hk-a",
        proof_script="import Mathlib\n\ntheorem base_true : True := by\n  trivial\n",
    )
    base_row = build_corpus_row(
        base_task,
        base_submission,
        VerifyResult(passed=True, reason="ok", proof_term_hash="base-term"),
        validator_hotkey="vhk",
        rewarded=True,
        tempo=1,
    )
    citing_task = make_task(
        task_id="lemma.accepted.citing",
        title="Citing accepted",
        theorem_name="citing_true",
        type_expr="True",
        source_stream="procedural",
        source_name="tempo-2",
        source_license="Apache-2.0",
        metadata={
            "activation_status": "paid",
            "triviality_checked": True,
            "baseline_solved": False,
            "lemma_rows_used": (base_row.row_id,),
        },
    )
    citing_row = build_corpus_row(
        citing_task,
        build_submission(
            citing_task,
            solver_hotkey="hk-b",
            proof_script="import Mathlib\n\ntheorem citing_true : True := by\n  trivial\n",
        ),
        VerifyResult(passed=True, reason="ok", proof_term_hash="citing-term"),
        validator_hotkey="vhk",
        rewarded=True,
        tempo=2,
    )
    write_jsonl([base_row, citing_row], corpus_dir / "epoch-000001.jsonl")

    sources = corpus_sources_from_dir(corpus_dir, before_tempo=3, citation_window_tempos=2000)
    weights = {source.metadata["substrate_row_id"]: source.metadata["citation_weight"] for source in sources}

    assert weights[base_row.row_id] == 1
    assert weights[citing_row.row_id] == 0

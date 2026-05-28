from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.corpus import build_corpus_row, write_jsonl
from lemma.lean.sandbox import VerifyResult
from lemma.lean.workspace import materialize_workspace
from lemma.problems.base import Problem
from lemma.protocol_invariants import enforce_production_invariants
from lemma.submissions import build_submission
from lemma.supply.gates import (
    AssumedProceduralGateRunner,
    LeanProceduralGateRunner,
    ProceduralGateVerdict,
)
from lemma.supply.import_graph import ImportGraphRow, extract_import_graph_rows, read_import_graph
from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
from lemma.supply.mutation import LeanAstMutationEngine, MutationResult, PreviewMutationEngine, StructuralMutationEngine
from lemma.supply.novelty import novelty_cache_from_hashes, read_novelty_cache, statement_hash
from lemma.supply.operator_bundle import (
    MUTATION_ENGINE,
    OPERATOR_BUNDLE_VERSION,
    OPERATOR_NAMES,
    SMALL_VALUES_BY_TYPE,
)
from lemma.supply.procedural import (
    _candidate_from_source,
    _depth_balanced_sources,
    _eligible_depth2_sources,
    build_procedural_registry_tasks,
    corpus_sources_from_dir,
    generate_depth2_candidates,
    procedural_operator_bundle_hash,
    read_yield_history,
    source_pool_hash,
)
from lemma.supply.slot_weight import slot_weight_receipt_for_candidate
from lemma.supply.triviality_budget import TrivialityRetargetConfig, triviality_budget_receipt
from lemma.supply.types import fixture_candidate
from lemma.task_supply import make_task, write_registry
from lemma.validator import (
    active_epoch_seed,
    active_tasks_for_validation,
    task_registry_for_validation,
)


def test_operator_bundle_includes_lean_pretty_value_aliases() -> None:
    assert SMALL_VALUES_BY_TYPE["\u2115"] == SMALL_VALUES_BY_TYPE["Nat"]
    assert SMALL_VALUES_BY_TYPE["\u2124"] == SMALL_VALUES_BY_TYPE["Int"]
    assert SMALL_VALUES_BY_TYPE["\u211A"] == SMALL_VALUES_BY_TYPE["Rat"]
    assert SMALL_VALUES_BY_TYPE["\u211D"] == SMALL_VALUES_BY_TYPE["Real"]
    assert "0" not in SMALL_VALUES_BY_TYPE["Nat"]
    assert "Prop" not in SMALL_VALUES_BY_TYPE


def test_structural_mutation_engine_marks_current_bundle_engine() -> None:
    source = fixture_candidate(
        slug="source_bool",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source_bool",
        type_expr="true = false",
        queue_depth=0,
    )

    first = StructuralMutationEngine().apply(
        source,
        "true = false",
        "symm",
        step=0,
        param_seed="a" * 64,
        peer=source,
    )
    second = StructuralMutationEngine().apply(
        source,
        first.type_expr,
        "generalize",
        step=1,
        param_seed="b" * 64,
        peer=source,
    )

    assert first.type_expr == "false = true"
    assert first.params["engine"] == MUTATION_ENGINE
    assert second.type_expr == "∀ lemma_p1_bbbbbb : Prop, lemma_p1_bbbbbb → (false = true)"
    assert second.params["engine"] == MUTATION_ENGINE

    implication = StructuralMutationEngine().apply(
        source,
        "¬b = false → b = true",
        "symm",
        step=0,
        param_seed="c" * 64,
        peer=source,
    )
    assert implication.type_expr == "¬b = false → true = b"

    bind_relation = StructuralMutationEngine().apply(
        source,
        "x >>= f = x.bind f",
        "symm",
        step=0,
        param_seed="d" * 64,
        peer=source,
    )
    assert bind_relation.type_expr == "x.bind f = x >>= f"

    field_notation = StructuralMutationEngine().apply(
        source,
        "∀ a b : Nat, a.succ = b",
        "specialize",
        step=1,
        param_seed="e" * 64,
        peer=source,
    )
    assert field_notation.type_expr == "∀ b : Nat, (1 : Nat).succ = b"


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
            "theorem_name": "Nat.add_comm_smoke",
            "type_expr": "∀ n m : Nat, n + m = m + n",
            "imports": ["Mathlib.Data.Nat.Hyperoperation"],
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Data/Nat/Hyperoperation.lean",
            "source_license": "Apache-2.0",
            "queue_depth": 0,
        },
        {
            "theorem_name": "Nat.mul_comm_smoke",
            "type_expr": "∀ n m : Nat, n * m = m * n",
            "imports": ["Mathlib.Data.Nat.Factorization.Basic"],
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Data/Nat/Factorization/Basic.lean",
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
        ImportGraphRow(module="Mathlib.Data.Nat.Hyperoperation", imports=("Mathlib.Data.Nat.Basic",)),
        ImportGraphRow(module="Mathlib.Data.Nat.Factorization.Basic", imports=("Mathlib.Data.Nat.Basic",)),
        ImportGraphRow(module="Mathlib.Algebra.Group.Basic", imports=("Mathlib.Init",)),
    )
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


def _test_valid_mutation(source, *, step: int, engine: str = MUTATION_ENGINE) -> MutationResult:  # noqa: ANN001
    if step == 0:
        return MutationResult(
            "∀ n m : Nat, m = n",
            {"rule": "reverse_relation", "relation": "=", "engine": engine},
        )
    suffix = source.theorem_name.rsplit("_", 1)[-1]
    value = int(suffix) + 1 if suffix.isdigit() else 1 + (
        int(hashlib.sha256(source.theorem_name.encode()).hexdigest()[:8], 16) % 1_000_000
    )
    return MutationResult(
        f"∀ m : Nat, ({value} : Nat) = m",
        {"binder": "n", "binder_type": "Nat", "value": str(value), "engine": engine},
    )


def test_materialize_workspace_writes_lean_heartbeat_budget(tmp_path: Path) -> None:
    problem = Problem(
        id="heartbeat",
        theorem_name="heartbeat",
        type_expr="True",
        split="pytest",
        lean_toolchain="leanprover/lean4:v4.15.0",
        mathlib_rev="abc123",
        imports=("Mathlib",),
        extra={"lean_max_heartbeats": 12345},
    )

    materialize_workspace(tmp_path, problem, problem.submission_stub())

    assert "maxHeartbeats = 12345" in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


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


def _fake_lean_gate_batch(self, candidates, *, seen_canonical_hashes) -> tuple[ProceduralGateVerdict, ...]:  # noqa: ANN001
    return tuple(
        _fake_lean_gate(self, candidate, seen_canonical_hashes=seen_canonical_hashes) for candidate in candidates
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
    assert "replaceConst" in captured["problem"].extra["challenge_full"]
    assert 'def inputSource : String := "True"' in captured["problem"].extra["challenge_full"]
    assert "def sourceTermOrThrow" in captured["problem"].extra["challenge_full"]
    assert "containsSyntheticHole" in captured["problem"].extra["challenge_full"]
    assert "let roundtrip ← parseTermOrThrow rendered" in captured["problem"].extra["challenge_full"]
    assert captured["problem"].extra["lean_max_heartbeats"] == 400_000
    assert captured["problem"].extra["lean_skip_axiom_check"] is True
    assert captured["problem"].extra["lean_skip_submission_axiom_check"] is True
    assert 'elab "#lemma_emit_mutation" : command => emit' in captured["problem"].extra["challenge_full"]
    assert "\n#lemma_emit_mutation\n" in captured["problem"].extra["challenge_full"]
    assert "theorem lemma_ast_mutation_dummy : True" in captured["proof_script"]


def test_lean_ast_mutation_engine_chains_from_prior_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_run_lean_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        captured["problem"] = problem
        return VerifyResult(
            passed=True,
            reason="ok",
            stdout_tail='LEMMA_AST_MUTATION {"params":{"from":"Nat","to":"Int"},"type_expr":"Int = Int"}',
        )

    monkeypatch.setattr("lemma.supply.mutation.run_lean_verify", fake_run_lean_verify)
    source = fixture_candidate(
        slug="source_nat",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source_nat",
        type_expr="Nat = Nat",
        queue_depth=0,
    )

    result = LeanAstMutationEngine(LemmaSettings(_env_file=None, lean_use_docker=False)).apply(
        source,
        "Int = Int",
        "substitute-type",
        step=1,
        param_seed="b" * 64,
        peer=source,
    )

    assert result.type_expr == "Int = Int"
    challenge = captured["problem"].extra["challenge_full"]
    assert 'def inputSource : String := "Int = Int"' in challenge
    assert "sourceTheoremName" not in challenge


def test_lean_ast_mutation_engine_accepts_marker_before_postcheck_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_lean_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        return VerifyResult(
            passed=False,
            reason="compile_error",
            stdout_tail='LEMMA_AST_MUTATION {"params":{"from":"ℕ","to":"ℤ"},"type_expr":"ℤ = ℤ"}',
            stderr_tail="AxiomCheck.lean:2:7: error: unknown universe level `u_1`",
        )

    monkeypatch.setattr("lemma.supply.mutation.run_lean_verify", fake_run_lean_verify)
    source = fixture_candidate(
        slug="source_nat",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source_nat",
        type_expr="Nat = Nat",
        queue_depth=0,
    )

    result = LeanAstMutationEngine(LemmaSettings(_env_file=None, lean_use_docker=False)).apply(
        source,
        "ℕ = ℕ",
        "substitute-type",
        step=0,
        param_seed="c" * 64,
        peer=source,
    )

    assert result.type_expr == "ℤ = ℤ"


def test_lean_ast_mutation_engine_parses_lake_build_info_prefixed_marker() -> None:
    output = (
        'info: Challenge.lean:206:0: LEMMA_AST_MUTATION {"params":{"rule":"conjoin_self"},'
        '"type_expr":"(True) ∧ (True)"}'
    )

    result = LeanAstMutationEngine(LemmaSettings(_env_file=None, lean_use_docker=False))._parse_result(output)

    assert result.type_expr == "(True) ∧ (True)"
    assert result.params["engine"] == "lean_ast_elaborator"


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
    assert all(candidate.metadata["source_sampling_version"] == "lemma-source-sampling-v3" for candidate in first)
    assert all(candidate.metadata["source_pool_stream_counts"] == {"mathlib_snapshot": 3} for candidate in first)
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


@pytest.mark.parametrize(
    "params",
    (
        {"fallback": "true_premise"},
        {"fallback": "unsupported_binder_type"},
        {"fallback": "no_supported_type_occurrence"},
        {"mode": "peer_premise"},
        {"rule": "conjoin_peer_conclusion"},
        {"rule": "false_disjunct"},
    ),
)
def test_candidate_from_source_rejects_low_value_mutation(params: dict[str, object]) -> None:
    class LowValueMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return MutationResult(f"True → ({type_expr})", params)

    source = fixture_candidate(
        slug="source",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source",
        type_expr="True",
        queue_depth=0,
    )

    with pytest.raises(ValueError, match="low-value procedural mutation"):
        _candidate_from_source(
            source,
            source_pool=(source,),
            generation_seed="epoch-a",
            epoch_fields={},
            operator_chain=("specialize", "weaken"),
            mutation_engine=LowValueMutationEngine(),
            source_pool_hash_value="a" * 64,
            source_pool_receipt_value={
                "version": "test",
                "source_count": 1,
                "source_stream_counts": {"mathlib_snapshot": 1},
                "sampling_version": "test",
                "citation_alpha_basis_points": 5000,
                "citation_weight_cap_micros": 64_000_000,
                "citation_window_tempos": 2000,
            },
            operator_bundle_hash="b" * 64,
            tempo=3,
            sequence=0,
        )


def test_candidate_from_source_uses_specialize_as_second_step() -> None:
    operators = []

    class RecordingMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            operators.append(operator)
            if step == 0:
                return MutationResult(
                    "∀ n m : Nat, m = n",
                    {"rule": "reverse_relation", "engine": MUTATION_ENGINE},
                )
            return MutationResult(
                "∀ m : Nat, (1 : Nat) = m",
                {"binder": "n", "binder_type": "Nat", "value": "1", "engine": MUTATION_ENGINE},
            )

    source = fixture_candidate(
        slug="source",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source",
        type_expr="∀ n m : Nat, n = m",
        queue_depth=0,
    )

    candidate = _candidate_from_source(
        source,
        source_pool=(source,),
        generation_seed="epoch-a",
        epoch_fields={},
        mutation_engine=RecordingMutationEngine(),
        source_pool_hash_value="a" * 64,
        source_pool_receipt_value={
            "version": "test",
            "source_count": 1,
            "source_stream_counts": {"mathlib_snapshot": 1},
            "sampling_version": "test",
            "citation_alpha_basis_points": 5000,
            "citation_weight_cap_micros": 64_000_000,
            "citation_window_tempos": 2000,
        },
        operator_bundle_hash="b" * 64,
        tempo=3,
        sequence=0,
    )

    assert operators == ["symm", "specialize"]
    assert [step["operator"] for step in candidate.metadata["mutation_chain"]] == operators
    assert candidate.type_expr == "∀ m : Nat, (1 : Nat) = m"


def test_candidate_from_source_skips_peer_lookup_for_non_peer_operators(monkeypatch: pytest.MonkeyPatch) -> None:
    source = fixture_candidate(
        slug="source",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source",
        type_expr="∀ n m : Nat, n = m",
        queue_depth=0,
    )
    monkeypatch.setattr(
        "lemma.supply.procedural._peer_source",
        lambda *args, **kwargs: pytest.fail("non-peer operators should not scan the source pool"),
    )

    candidate = _candidate_from_source(
        source,
        source_pool=(source,),
        generation_seed="epoch-a",
        epoch_fields={},
        operator_chain=("symm", "specialize"),
        mutation_engine=StructuralMutationEngine(),
        source_pool_hash_value="a" * 64,
        source_pool_receipt_value={
            "version": "test",
            "source_count": 1,
            "source_stream_counts": {"mathlib_snapshot": 1},
            "sampling_version": "test",
            "citation_alpha_basis_points": 5000,
            "citation_weight_cap_micros": 64_000_000,
            "citation_window_tempos": 2000,
        },
        operator_bundle_hash="b" * 64,
        tempo=3,
        sequence=0,
    )

    assert [step["operator"] for step in candidate.metadata["mutation_chain"]] == ["symm", "specialize"]


def test_candidate_from_source_rejects_placeholder_mutation() -> None:
    class PlaceholderMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return MutationResult("∀ n : Nat, ?_mvar.1 n = sorry", {"engine": "pytest"})

    source = fixture_candidate(
        slug="source",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source",
        type_expr="Nat = Nat",
        queue_depth=0,
    )

    with pytest.raises(ValueError, match="placeholder"):
        _candidate_from_source(
            source,
            source_pool=(source,),
            generation_seed="epoch-a",
            epoch_fields={},
            operator_chain=("substitute-type", "generalize"),
            mutation_engine=PlaceholderMutationEngine(),
            source_pool_hash_value="a" * 64,
            source_pool_receipt_value={
                "version": "test",
                "source_count": 1,
                "source_stream_counts": {"mathlib_snapshot": 1},
                "sampling_version": "test",
                "citation_alpha_basis_points": 5000,
                "citation_weight_cap_micros": 64_000_000,
                "citation_window_tempos": 2000,
            },
            operator_bundle_hash="b" * 64,
            tempo=3,
            sequence=0,
        )


def test_candidate_from_source_rejects_noop_mutation() -> None:
    class NoopMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return MutationResult(type_expr, {"engine": "pytest"})

    source = fixture_candidate(
        slug="source",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source",
        type_expr="Nat = Nat",
        queue_depth=0,
    )

    with pytest.raises(ValueError, match="no-op"):
        _candidate_from_source(
            source,
            source_pool=(source,),
            generation_seed="epoch-a",
            epoch_fields={},
            operator_chain=("substitute-type", "substitute-type"),
            mutation_engine=NoopMutationEngine(),
            source_pool_hash_value="a" * 64,
            source_pool_receipt_value={
                "version": "test",
                "source_count": 1,
                "source_stream_counts": {"mathlib_snapshot": 1},
                "sampling_version": "test",
                "citation_alpha_basis_points": 5000,
                "citation_weight_cap_micros": 64_000_000,
                "citation_window_tempos": 2000,
            },
            operator_bundle_hash="b" * 64,
            tempo=3,
            sequence=0,
        )


def test_candidate_from_source_rejects_all_specialize_chain() -> None:
    class SpecializeOnlyMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return MutationResult("True" if step == 0 else "False", {"binder": "n", "binder_type": "Nat", "value": "0"})

    source = fixture_candidate(
        slug="source",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source",
        type_expr="∀ n : Nat, n = n",
        queue_depth=0,
    )

    with pytest.raises(ValueError, match="specialize_only"):
        _candidate_from_source(
            source,
            source_pool=(source,),
            generation_seed="epoch-a",
            epoch_fields={},
            operator_chain=("specialize", "specialize"),
            mutation_engine=SpecializeOnlyMutationEngine(),
            source_pool_hash_value="a" * 64,
            source_pool_receipt_value={
                "version": "test",
                "source_count": 1,
                "source_stream_counts": {"mathlib_snapshot": 1},
                "sampling_version": "test",
                "citation_alpha_basis_points": 5000,
                "citation_weight_cap_micros": 64_000_000,
                "citation_window_tempos": 2000,
            },
            operator_bundle_hash="b" * 64,
            tempo=3,
            sequence=0,
        )


def test_depth2_generation_attempt_limit_scales_with_requested_count() -> None:
    class AlwaysGoodMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return _test_valid_mutation(source, step=step)

    class RejectingGate:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, candidate, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            self.calls += 1
            return ProceduralGateVerdict(
                typechecked=True,
                prop_gate_passed=True,
                triviality_checked=True,
                baseline_solved=False,
                novelty_status="duplicate",
                slot_weight=1.0,
            )

    sources = tuple(
        fixture_candidate(
            slug=f"source_{index}",
            source_stream="mathlib_snapshot",
            source_name="snapshot",
            theorem_name=f"source_{index}",
            type_expr="∀ n m : Nat, n = m",
            queue_depth=0,
        )
        for index in range(100)
    )
    gate = RejectingGate()

    with pytest.raises(ValueError, match="procedural gates accepted 0 candidates, needed 1"):
        generate_depth2_candidates(
            sources,
            generation_seed="epoch-a",
            epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
            count=1,
            tempo=3,
            gate_runner=gate,
            mutation_engine=AlwaysGoodMutationEngine(),
        )

    assert gate.calls == 50


def test_depth2_generation_can_return_partial_when_allowed() -> None:
    class AlwaysGoodMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return _test_valid_mutation(source, step=step)

    class OneGoodGate:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, candidate, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            self.calls += 1
            return ProceduralGateVerdict(
                typechecked=True,
                prop_gate_passed=True,
                triviality_checked=True,
                baseline_solved=False,
                novelty_status="passed" if self.calls == 1 else "duplicate",
                slot_weight=1.0,
            )

    gate = OneGoodGate()
    candidates = generate_depth2_candidates(
        tuple(
            fixture_candidate(
                slug=f"source_{index}",
                source_stream="mathlib_snapshot",
                source_name="snapshot",
                theorem_name=f"source_{index}",
                    type_expr="∀ n m : Nat, n = m",
                queue_depth=0,
            )
            for index in range(4)
        ),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=2,
        tempo=3,
        allow_partial=True,
        gate_runner=gate,
        mutation_engine=AlwaysGoodMutationEngine(),
        generation_workers=1,
    )

    assert gate.calls == 4
    assert len(candidates) == 1
    assert candidates[0].metadata["procedural_generation_target_count"] == 2
    assert candidates[0].metadata["procedural_generation_accepted_count"] == 1
    assert candidates[0].metadata["procedural_generation_attempt_count"] == 100
    assert candidates[0].metadata["procedural_generation_attempt_limit"] == 100


def test_depth2_partial_generation_keeps_trying_for_target_when_allowed() -> None:
    class AlwaysGoodMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return _test_valid_mutation(source, step=step)

    class CountingGate:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, candidate, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            self.calls += 1
            return ProceduralGateVerdict(
                typechecked=True,
                prop_gate_passed=True,
                triviality_checked=True,
                baseline_solved=False,
                novelty_status="passed",
                slot_weight=1.0,
            )

    gate = CountingGate()
    candidates = generate_depth2_candidates(
        tuple(
            fixture_candidate(
                slug=f"source_{index}",
                source_stream="mathlib_snapshot",
                source_name="snapshot",
                theorem_name=f"source_{index}",
                    type_expr="∀ n m : Nat, n = m",
                queue_depth=0,
            )
            for index in range(12)
        ),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=6,
        min_count=4,
        tempo=3,
        allow_partial=True,
        gate_runner=gate,
        mutation_engine=AlwaysGoodMutationEngine(),
        generation_workers=1,
    )

    assert gate.calls == 6
    assert len(candidates) == 6
    assert {candidate.metadata["procedural_generation_target_count"] for candidate in candidates} == {6}
    assert {candidate.metadata["procedural_generation_accepted_count"] for candidate in candidates} == {6}
    assert {candidate.metadata["procedural_generation_attempt_count"] for candidate in candidates} == {6}
    assert {candidate.metadata["procedural_generation_attempt_limit"] for candidate in candidates} == {300}


def test_depth2_generation_batches_lean_gate_attempts() -> None:
    class AlwaysGoodMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return _test_valid_mutation(source, step=step)

    class BatchRejectingGate:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def __call__(self, candidate, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            raise AssertionError("parallel generation should use the batch gate")

        def batch_capacity(self, generation_workers):  # noqa: ANN001, ARG002
            return 96

        def batch(self, candidates, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            self.batch_sizes.append(len(candidates))
            return tuple(
                ProceduralGateVerdict(
                    typechecked=True,
                    prop_gate_passed=True,
                    triviality_checked=True,
                    baseline_solved=False,
                    novelty_status="duplicate",
                    slot_weight=1.0,
                )
                for _candidate in candidates
            )

    sources = tuple(
        fixture_candidate(
            slug=f"source_{index}",
            source_stream="mathlib_snapshot",
            source_name="snapshot",
            theorem_name=f"source_{index}",
            type_expr="∀ n m : Nat, n = m",
            queue_depth=0,
        )
        for index in range(100)
    )
    gate = BatchRejectingGate()

    with pytest.raises(ValueError, match="procedural gates accepted 0 candidates, needed 1"):
        generate_depth2_candidates(
            sources,
            generation_seed="epoch-a",
            epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
            count=1,
            tempo=3,
            gate_runner=gate,
            generation_workers=2,
            mutation_engine=AlwaysGoodMutationEngine(),
        )

    assert gate.batch_sizes[0] == 8
    assert sum(gate.batch_sizes) == 50


def test_depth2_generation_rejects_mismatched_mutation_engine_before_lean() -> None:
    class DriftedMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return _test_valid_mutation(source, step=step, engine="old-engine")

    class NoLeanGate:
        def __call__(self, candidate, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            raise AssertionError("pre-Lean candidate checks should reject this mutation chain")

        def batch(self, candidates, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            raise AssertionError("pre-Lean candidate checks should reject this mutation chain")

    sources = tuple(
        fixture_candidate(
            slug=f"source_{index}",
            source_stream="mathlib_snapshot",
            source_name="snapshot",
            theorem_name=f"source_{index}",
            type_expr="∀ n m : Nat, n = m",
            queue_depth=0,
        )
        for index in range(100)
    )

    with pytest.raises(ValueError, match="procedural gates accepted 0 candidates, needed 1"):
        generate_depth2_candidates(
            sources,
            generation_seed="epoch-a",
            epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
            count=1,
            tempo=3,
            gate_runner=NoLeanGate(),
            generation_workers=2,
            mutation_engine=DriftedMutationEngine(),
        )


def test_depth2_generation_parallel_matches_sequential(tmp_path: Path) -> None:
    class LatencyGate:
        def __call__(self, candidate, *, seen_canonical_hashes):  # noqa: ANN001, ARG002
            sequence = int(candidate.source_ref.name.rsplit("-", 1)[1])
            time.sleep((5 - sequence % 5) * 0.001)
            return ProceduralGateVerdict(
                typechecked=True,
                prop_gate_passed=True,
                triviality_checked=True,
                baseline_solved=False,
                novelty_status="passed",
                slot_weight=1.0,
                metadata={"gate_runner": "latency-test"},
            )

    snapshot = tmp_path / "snapshot.jsonl"
    novelty_cache = tmp_path / "novelty.jsonl"
    _write_snapshot(snapshot)
    _write_novelty_cache(novelty_cache)
    sources = mathlib_candidates_from_jsonl(snapshot)
    randomness = json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True)
    kwargs = {
        "generation_seed": "epoch-a",
        "epoch_randomness": randomness,
        "count": 2,
        "tempo": 3,
    }

    sequential = generate_depth2_candidates(sources, generation_workers=1, gate_runner=LatencyGate(), **kwargs)

    for workers in (2, 4, 8, 16):
        parallel = generate_depth2_candidates(sources, generation_workers=workers, gate_runner=LatencyGate(), **kwargs)
        assert [candidate.id for candidate in sequential] == [candidate.id for candidate in parallel]


def test_procedural_candidate_keeps_peer_imports() -> None:
    class PeerMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return MutationResult(
                f"({peer.type_expr}) → ({type_expr})",
                {"peer_source_id": peer.id},
            )

    source = fixture_candidate(
        slug="source",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="source",
        type_expr="True",
        queue_depth=0,
    ).model_copy(update={"imports": ("Mathlib.Algebra.Ring.Basic",)})
    peer = fixture_candidate(
        slug="peer",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="peer",
        type_expr="Nat",
        queue_depth=0,
    ).model_copy(update={"imports": ("Mathlib.Data.Nat.Basic",)})

    candidate = _candidate_from_source(
        source,
        source_pool=(source, peer),
        generation_seed="epoch-a",
        epoch_fields={},
        operator_chain=("conjoin", "weaken"),
        mutation_engine=PeerMutationEngine(),
        source_pool_hash_value="a" * 64,
        source_pool_receipt_value={
            "version": "test",
            "source_count": 2,
            "source_stream_counts": {"mathlib_snapshot": 2},
            "sampling_version": "test",
            "citation_alpha_basis_points": 5000,
            "citation_weight_cap_micros": 64_000_000,
            "citation_window_tempos": 2000,
        },
        operator_bundle_hash="b" * 64,
        tempo=3,
        sequence=0,
    )

    assert candidate.imports == ("Mathlib.Algebra.Ring.Basic", "Mathlib.Data.Nat.Basic")
    assert candidate.submission_stub.startswith("import Mathlib.Algebra.Ring.Basic\nimport Mathlib.Data.Nat.Basic\n")


def test_procedural_registry_rejects_assumed_gate_receipts(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
        mutation_engine=StructuralMutationEngine(),
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
            assert problem.extra["lean_max_heartbeats"] == 400_000
            gate_source = str(problem.extra["challenge_full"])
            assert "set_option autoImplicit false" in gate_source
            assert "containsSyntheticHole" in gate_source
            assert "def typecheck_gate" in gate_source
            assert "theorem prop_gate" in gate_source
            assert problem.extra["lean_fingerprint_names"] == (
                "LemmaProceduralGate.typecheck_gate",
                "LemmaProceduralGate.prop_gate",
            )
            assert problem.extra["lean_eval_commands"] == (
                "#lemma_emit_kernel_normal",
                "set_option maxHeartbeats 200000",
                "#lemma_emit_triviality",
            )
            assert 'elab "#lemma_emit_kernel_normal" : command => emit_kernel_normal' in problem.extra[
                "challenge_full"
            ]
            assert 'elab "#lemma_emit_triviality" : command => emit_triviality' in problem.extra["challenge_full"]
            assert "def proofSourceSucceeds" in problem.extra["challenge_full"]
            calls.append("gate")
            return VerifyResult(
                passed=True,
                reason="ok",
                stdout_tail=(
                    "LEMMA_KERNEL_NORMAL_FORM (forall default const:True:[] const:True:[])\n"
                    'LEMMA_TRIVIALITY {"checked":true,"baseline_solved":false,'
                    '"baseline_solver":null,"triviality_reason":"baseline_failed"}'
                ),
                build_seconds=1.25,
                declaration_fingerprints={
                    "LemmaProceduralGate.typecheck_gate": "7" * 64,
                    "LemmaProceduralGate.prop_gate": "8" * 64,
                },
            )
        raise AssertionError("triviality should be embedded in the gate build")

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
    assert verdict.metadata["triviality_budget_heartbeats"] == 200_000
    assert verdict.metadata["triviality_reason"] == "baseline_failed"
    assert verdict.metadata["lean_gate_mode"] == "combined_prop_triviality"
    assert verdict.metadata["lean_gate_invocations"] == 1
    assert "lean_gate_build_seconds" not in verdict.metadata
    assert calls == ["gate"]


def test_lean_gate_runner_embeds_triviality_stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
        assert problem.id.endswith(".gate")
        assert problem.extra["lean_max_heartbeats"] == 400_000
        assert problem.extra["lean_skip_submission_axiom_check"] is True
        assert verify_timeout_s == 5
        gate_source = str(problem.extra["challenge_full"])
        assert "def typecheck_gate" in gate_source
        assert "theorem prop_gate" in gate_source
        assert "def combinedTrivialitySource" in gate_source
        assert "by\\n  first\\n  | decide" in gate_source
        assert "  | aesop" in gate_source
        calls.append("gate")
        return VerifyResult(
            passed=True,
            reason="ok",
            stdout_tail=(
                "LEMMA_KERNEL_NORMAL_FORM (forall default const:True:[] const:True:[])\n"
                'LEMMA_TRIVIALITY {"checked":true,"baseline_solved":false,'
                '"baseline_solver":null,"triviality_reason":"baseline_failed"}'
            ),
            declaration_fingerprints={
                "LemmaProceduralGate.typecheck_gate": "7" * 64,
                "LemmaProceduralGate.prop_gate": "8" * 64,
            },
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdict = LeanProceduralGateRunner(
        LemmaSettings(
            _env_file=None,
            lean_use_docker=False,
            procedural_gate_timeout_s=5,
            procedural_lean_workers=2,
        )
    )(candidate, seen_canonical_hashes=())

    assert verdict.accepted is True
    assert calls == ["gate"]


def test_lean_gate_runner_does_not_embed_source_theorem_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidate = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
    )[0]
    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        assert problem.id.endswith(".gate")
        gate_source = str(problem.extra["challenge_full"])
        assert "ancestorBaselineSource" not in gate_source
        assert "source_theorem" not in gate_source
        return VerifyResult(
            passed=True,
            reason="ok",
            stdout_tail=(
                "LEMMA_KERNEL_NORMAL_FORM (forall default const:True:[] const:True:[])\n"
                'LEMMA_TRIVIALITY {"checked":true,"baseline_solved":false,'
                '"baseline_solver":null,"triviality_reason":"baseline_failed"}'
            ),
            declaration_fingerprints={
                "LemmaProceduralGate.typecheck_gate": "7" * 64,
                "LemmaProceduralGate.prop_gate": "8" * 64,
            },
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdict = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    )(candidate, seen_canonical_hashes=())

    assert verdict.accepted is True
    assert verdict.baseline_solved is False
    assert verdict.metadata["baseline_solver"] is None


def test_lean_gate_runner_batches_gate_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=2,
        tempo=3,
    )
    calls: list[str] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        assert problem.id.endswith(".gate.batch")
        gate_source = str(problem.extra["challenge_full"])
        assert "set_option autoImplicit false" in gate_source
        assert "containsSyntheticHole" in gate_source
        assert problem.extra["lean_eval_commands"] == ("set_option maxHeartbeats 200000", "#lemma_emit_gate_results")
        assert "ancestorBaselineSource" not in gate_source
        assert "source_theorem" not in gate_source
        calls.append("batch")
        return VerifyResult(
            passed=True,
            reason="ok",
            build_seconds=2.5,
            stdout_tail="\n".join(
                (
                    'LEMMA_GATE_RESULT {"id":"0","typechecked":true,"kernel_normal_form":"first",'
                    '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                    '"triviality_reason":"baseline_failed"}',
                    'LEMMA_GATE_RESULT {"id":"1","typechecked":true,"kernel_normal_form":"second",'
                    '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                    '"triviality_reason":"baseline_failed"}',
                )
            ),
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdicts = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    ).batch(candidates, seen_canonical_hashes=())

    assert [verdict.accepted for verdict in verdicts] == [True, True]
    assert [verdict.metadata["kernel_canonical_hash"] for verdict in verdicts] == [
        hashlib.sha256(b"first").hexdigest(),
        hashlib.sha256(b"second").hexdigest(),
    ]
    assert verdicts[0].metadata["lean_gate_mode"] == "batched_prop_triviality"
    assert verdicts[0].metadata["lean_gate_batch_size"] == 2
    assert "lean_gate_batch_seconds" not in verdicts[0].metadata
    assert calls == ["batch"]


def test_lean_gate_runner_ignores_payload_from_failed_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    (candidate,) = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
    )

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        assert problem.id.endswith(".gate.batch")
        return VerifyResult(
            passed=False,
            reason="compile_error",
            stdout_tail=(
                'LEMMA_GATE_RESULT {"id":"0","typechecked":true,"kernel_normal_form":"bad",'
                '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                '"triviality_reason":"baseline_failed"}'
            ),
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    (verdict,) = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    ).batch((candidate,), seen_canonical_hashes=())

    assert verdict.accepted is False
    assert verdict.typechecked is False
    assert verdict.metadata["typecheck_reason"] == "compile_error"
    assert verdict.novelty_status == "missing_kernel_fingerprint"


def test_lean_gate_runner_uses_complete_failed_batch_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=2,
        tempo=3,
    )
    batch_sizes: list[int] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        gate_source = str(problem.extra["challenge_full"])
        batch_sizes.append(gate_source.count("canonicalSource :="))
        return VerifyResult(
            passed=False,
            reason="compile_error",
            stdout_tail="\n".join(
                (
                    'LEMMA_GATE_RESULT {"id":"0","typechecked":true,"kernel_normal_form":"first",'
                    '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                    '"triviality_reason":"baseline_failed"}',
                    'LEMMA_GATE_RESULT {"id":"1","typechecked":true,"kernel_normal_form":"second",'
                    '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                    '"triviality_reason":"baseline_failed"}',
                    "LEMMA_GATE_DONE 2",
                )
            ),
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdicts = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    ).batch(candidates, seen_canonical_hashes=())

    assert batch_sizes == [2]
    assert [verdict.accepted for verdict in verdicts] == [True, True]
    assert [verdict.metadata["lean_gate_batch_size"] for verdict in verdicts] == [2, 2]


def test_lean_gate_runner_splits_failed_batch_to_salvage_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=2,
        tempo=3,
    )
    batch_sizes: list[int] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        if problem.id.endswith(".gate.standalone"):
            return VerifyResult(passed=True, reason="ok")
        gate_source = str(problem.extra["challenge_full"])
        batch_size = gate_source.count("canonicalSource :=")
        batch_sizes.append(batch_size)
        if batch_size > 1:
            return VerifyResult(passed=False, reason="timeout", build_seconds=3.0)
        return VerifyResult(
            passed=True,
            reason="ok",
            build_seconds=1.0,
            stdout_tail=(
                'LEMMA_GATE_RESULT {"id":"0","typechecked":true,"kernel_normal_form":"single",'
                '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                '"triviality_reason":"baseline_failed"}'
            ),
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdicts = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    ).batch(candidates, seen_canonical_hashes=())

    assert batch_sizes == [2, 1, 1]
    assert [verdict.accepted for verdict in verdicts] == [True, True]
    assert [verdict.metadata["lean_gate_batch_size"] for verdict in verdicts] == [1, 1]


def test_lean_gate_runner_splits_compile_error_batch_with_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=2,
        tempo=3,
    )
    batch_sizes: list[int] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        if problem.id.endswith(".gate.standalone"):
            return VerifyResult(passed=True, reason="ok")
        batch_size = str(problem.extra["challenge_full"]).count("canonicalSource :=")
        batch_sizes.append(batch_size)
        if batch_size > 1:
            return VerifyResult(passed=False, reason="compile_error", build_seconds=3.0)
        return VerifyResult(
            passed=True,
            reason="ok",
            build_seconds=1.0,
            stdout_tail=(
                'LEMMA_GATE_RESULT {"id":"0","typechecked":true,"kernel_normal_form":"single",'
                '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                '"triviality_reason":"baseline_failed"}'
            ),
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdicts = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    ).batch(candidates, seen_canonical_hashes=())

    assert batch_sizes == [2, 1, 1]
    assert [verdict.accepted for verdict in verdicts] == [True, True]
    assert [verdict.metadata["lean_gate_batch_size"] for verdict in verdicts] == [1, 1]


def test_lean_gate_runner_compile_error_split_budget_can_stop_salvage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=2,
        tempo=3,
    )
    batch_sizes: list[int] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        batch_sizes.append(str(problem.extra["challenge_full"]).count("canonicalSource :="))
        return VerifyResult(passed=False, reason="compile_error", build_seconds=3.0)

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdicts = LeanProceduralGateRunner(
        LemmaSettings(
            _env_file=None,
            lean_use_docker=False,
            procedural_gate_timeout_s=5,
            procedural_lean_compile_error_split_limit=0,
        )
    ).batch(candidates, seen_canonical_hashes=())

    assert batch_sizes == [2]
    assert [verdict.accepted for verdict in verdicts] == [False, False]
    assert [verdict.metadata["lean_gate_batch_size"] for verdict in verdicts] == [2, 2]


def test_lean_gate_runner_uses_configured_batch_capacity() -> None:
    default_runner = LeanProceduralGateRunner(LemmaSettings(_env_file=None, lean_use_docker=False))
    assert default_runner.batch_capacity(2) == 96

    runner = LeanProceduralGateRunner(
        LemmaSettings(
            _env_file=None,
            lean_use_docker=False,
            procedural_lean_batch_size=32,
            procedural_lean_batch_parallelism=3,
        )
    )

    assert runner.batch_capacity(2) == 96


def test_lean_gate_runner_uses_configured_parallel_batches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=2,
        tempo=3,
    )
    batch_sizes: list[int] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        if problem.id.endswith(".gate.standalone"):
            return VerifyResult(passed=True, reason="ok")
        gate_source = str(problem.extra["challenge_full"])
        batch_sizes.append(gate_source.count("canonicalSource :="))
        return VerifyResult(
            passed=True,
            reason="ok",
            build_seconds=1.0,
            stdout_tail=(
                'LEMMA_GATE_RESULT {"id":"0","typechecked":true,"kernel_normal_form":"single",'
                '"triviality_checked":true,"baseline_solved":false,"baseline_solver":null,'
                '"triviality_reason":"baseline_failed"}'
            ),
        )

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdicts = LeanProceduralGateRunner(
        LemmaSettings(
            _env_file=None,
            lean_use_docker=False,
            procedural_gate_timeout_s=5,
            procedural_lean_batch_size=1,
            procedural_lean_batch_parallelism=2,
        )
    ).batch(candidates, seen_canonical_hashes=())

    assert sorted(batch_sizes) == [1, 1]
    assert [verdict.accepted for verdict in verdicts] == [True, True]


def test_lean_gate_runner_skips_triviality_when_compile_gate_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
            calls.append("gate")
            return VerifyResult(passed=False, reason="compile_error")
        calls.append("triviality")
        raise AssertionError("triviality should not run after a failed compile gate")

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdict = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    )(candidate, seen_canonical_hashes=())

    assert calls == ["gate"]
    assert verdict.accepted is False
    assert verdict.typechecked is False
    assert verdict.prop_gate_passed is False
    assert verdict.triviality_checked is False
    assert verdict.baseline_solved is False
    assert verdict.metadata["triviality_reason"] == "not_run"


def test_lean_gate_runner_uses_prop_fingerprint_when_kernel_marker_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidate = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
    )[0]
    fallback_hash = "9" * 64

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        if problem.id.endswith(".gate"):
            return VerifyResult(
                passed=True,
                reason="ok",
                stdout_tail="",
                declaration_fingerprints={"LemmaProceduralGate.prop_gate": fallback_hash},
            )
        return VerifyResult(passed=False, reason="compile_error")

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdict = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    )(candidate, seen_canonical_hashes=(fallback_hash,))

    assert verdict.accepted is False
    assert verdict.novelty_status == "duplicate"
    assert verdict.metadata["kernel_canonical_hash"] == fallback_hash
    assert verdict.metadata["canonical_hash"] == fallback_hash


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


def test_lean_gate_runner_skips_lean_when_statement_duplicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        raise AssertionError("duplicate statements should be rejected before Lean runs")

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdict = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5),
        novelty_cache=cache,
    )(candidate, seen_canonical_hashes=())

    assert verdict.accepted is False
    assert verdict.novelty_status == "duplicate"


def test_public_novelty_cache_can_be_built_from_type_expr_rows(tmp_path: Path) -> None:
    path = tmp_path / "novelty.jsonl"
    path.write_text(json.dumps({"type_expr": "True   →   True"}, sort_keys=True) + "\n", encoding="utf-8")

    cache = read_novelty_cache(path)

    assert cache.contains(statement_hash("True → True"))


def test_procedural_slot_weight_receipt_uses_dependency_metadata(tmp_path: Path) -> None:
    class SlotWeightMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return _test_valid_mutation(source, step=step)

    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(
        json.dumps(
            {
                "theorem_name": "Deep.weight",
                "type_expr": "∀ n m : Nat, n = m",
                "imports": ["Mathlib.Data.Nat.Basic", "Mathlib.Algebra.Group.Basic"],
                "mathlib_rev": "abc123",
                "source_path": "Mathlib/Deep.lean",
                "source_license": "Apache-2.0",
                "queue_depth": 2,
                "citation_weight": 7.5,
                "direct_dependency_count": 0,
                "dependency_depth": 0,
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
        mutation_engine=SlotWeightMutationEngine(),
    )[0]
    inputs = candidate.metadata["slot_weight_inputs"]

    assert candidate.metadata["slot_weight_version"] == "lemma-slot-weight-v3"
    assert inputs["direct_dependency_count"] == 0
    assert inputs["dependency_depth"] == 2
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
    assert inputs["direct_dependency_count"] == 1
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
    monkeypatch.setattr("lemma.supply.gates.LeanProceduralGateRunner.batch", _fake_lean_gate_batch)
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


def test_procedural_supply_filters_sources_to_frontier_depth(monkeypatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    with snapshot.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "theorem_name": "Hard.depth_one",
                    "type_expr": "∀ x : ℂ, Complex.re x = Complex.re x",
                    "imports": ["Mathlib"],
                    "mathlib_rev": "abc123",
                    "source_path": "Mathlib/Hard.lean",
                    "source_license": "Apache-2.0",
                    "queue_depth": 1,
                },
                sort_keys=True,
            )
            + "\n"
    )
    full_source_hash = source_pool_hash(mathlib_candidates_from_jsonl(snapshot))
    captured_depths = []
    captured_max_queue_depth = None

    class StopGeneration(Exception):
        pass

    def fake_generate_depth2_candidates(sources, **kwargs):  # noqa: ANN001, ANN202
        nonlocal captured_max_queue_depth
        captured_max_queue_depth = kwargs["max_queue_depth"]
        captured_depths.extend(source.queue_depth for source in sources)
        raise StopGeneration

    monkeypatch.setattr("lemma.supply.procedural.generate_depth2_candidates", fake_generate_depth2_candidates)
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        procedural_source_jsonl=snapshot,
        procedural_source_sha256_expected=full_source_hash,
        procedural_candidate_count=1,
        active_task_count=1,
        frontier_depth=0,
    )

    with pytest.raises(StopGeneration):
        task_registry_for_validation(settings, tempo=3)

    assert captured_max_queue_depth == 0
    assert captured_depths == [0, 0, 0, 1]


def test_procedural_candidate_count_cannot_shrink_active_generation(monkeypatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    full_source_hash = source_pool_hash(mathlib_candidates_from_jsonl(snapshot))
    captured_count = None

    class StopGeneration(Exception):
        pass

    def fake_generate_depth2_candidates(sources, **kwargs):  # noqa: ANN001, ANN202
        nonlocal captured_count
        captured_count = kwargs["count"]
        raise StopGeneration

    monkeypatch.setattr("lemma.supply.procedural.generate_depth2_candidates", fake_generate_depth2_candidates)
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        procedural_source_jsonl=snapshot,
        procedural_source_sha256_expected=full_source_hash,
        procedural_candidate_count=1,
        active_task_count=6,
    )

    with pytest.raises(StopGeneration):
        task_registry_for_validation(settings, tempo=3)

    assert captured_count == 6


def test_depth2_generation_filters_ordering_without_changing_source_hash(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    with snapshot.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "theorem_name": "Hard.depth_one",
                    "type_expr": "∀ x : ℂ, Complex.re x = Complex.re x",
                    "imports": ["Mathlib"],
                    "mathlib_rev": "abc123",
                    "source_path": "Mathlib/Hard.lean",
                    "source_license": "Apache-2.0",
                    "queue_depth": 1,
                },
                sort_keys=True,
            )
            + "\n"
        )
    sources = mathlib_candidates_from_jsonl(snapshot)
    full_source_hash = source_pool_hash(sources)

    candidates = generate_depth2_candidates(
        sources,
        generation_seed="frontier",
        epoch_randomness="frontier",
        count=1,
        tempo=3,
        max_queue_depth=0,
    )

    assert candidates[0].queue_depth == 0
    assert candidates[0].metadata["source_pool_hash"] == full_source_hash


def test_depth2_generation_skips_toy_basic_sources(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "theorem_name": "Bool.source",
                        "type_expr": "∀ a b : Bool, a = b",
                        "imports": ["Mathlib.Data.Bool.Basic"],
                        "mathlib_rev": "abc123",
                        "source_path": "Mathlib/Data/Bool/Basic.lean",
                        "source_license": "Apache-2.0",
                        "queue_depth": 0,
                        "difficulty_score": 0,
                        "direct_dependency_count": 0,
                        "dependency_depth": 0,
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "theorem_name": "Nat.source",
                        "type_expr": "∀ n m : Nat, n = m",
                        "imports": ["Mathlib.Data.Nat.Basic"],
                        "mathlib_rev": "abc123",
                        "source_path": "Mathlib/Data/Nat/Basic.lean",
                        "source_license": "Apache-2.0",
                        "queue_depth": 0,
                        "difficulty_score": 0,
                        "direct_dependency_count": 0,
                        "dependency_depth": 0,
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "theorem_name": "Nat.hyper",
                        "type_expr": "∀ n m : Nat, n + m = m + n",
                        "imports": ["Mathlib.Data.Nat.Hyperoperation"],
                        "mathlib_rev": "abc123",
                        "source_path": "Mathlib/Data/Nat/Hyperoperation.lean",
                        "source_license": "Apache-2.0",
                        "queue_depth": 0,
                        "difficulty_score": 0,
                        "direct_dependency_count": 0,
                        "dependency_depth": 0,
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "theorem_name": "Nat.factor",
                        "type_expr": "∀ n m : Nat, n * m = m * n",
                        "imports": ["Mathlib.Data.Nat.Factorization.Basic"],
                        "mathlib_rev": "abc123",
                        "source_path": "Mathlib/Data/Nat/Factorization/Basic.lean",
                        "source_license": "Apache-2.0",
                        "queue_depth": 0,
                        "difficulty_score": 0,
                        "direct_dependency_count": 0,
                        "dependency_depth": 0,
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="source-diversity",
        epoch_randomness="source-diversity",
        count=2,
        tempo=3,
        max_queue_depth=0,
        gate_runner=AssumedProceduralGateRunner(),
        mutation_engine=StructuralMutationEngine(),
    )

    assert {candidate.source_ref.path for candidate in candidates} == {
        "Mathlib/Data/Nat/Hyperoperation.lean",
        "Mathlib/Data/Nat/Factorization/Basic.lean",
    }
    assert all(
        candidate.source_ref.path not in {"Mathlib/Data/Bool/Basic.lean", "Mathlib/Data/Nat/Basic.lean"}
        for candidate in candidates
    )


def test_depth2_source_bound_admits_controlled_deeper_rows() -> None:
    easy = fixture_candidate(
        slug="easy_set",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="Set.easy",
        type_expr="∀ n m : Nat, n = m",
        queue_depth=0,
        metadata={
            "difficulty_score": 2,
            "direct_dependency_count": 0,
            "dependency_depth": 0,
        },
    ).model_copy(update={"imports": ("Mathlib.Data.Nat.Factorization.Basic",)})
    medium = fixture_candidate(
        slug="medium_set",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="Set.medium",
        type_expr="∀ n m : Nat, n + m = m + n",
        queue_depth=2,
        metadata={
            "difficulty_score": 4,
            "direct_dependency_count": 0,
            "dependency_depth": 0,
        },
    ).model_copy(update={"imports": ("Mathlib.Data.Nat.Hyperoperation",)})
    too_broad = fixture_candidate(
        slug="broad_set",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="Set.broad",
        type_expr="∀ x : Set Nat, x ∩ x = x",
        queue_depth=3,
        metadata={
            "difficulty_score": 5,
            "direct_dependency_count": 0,
            "dependency_depth": 0,
        },
    ).model_copy(update={"imports": ("Mathlib.Data.Set.Basic",)})
    broad_import = fixture_candidate(
        slug="nat_prime",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="Nat.prime",
        type_expr="∀ n m : Nat, n = m",
        queue_depth=0,
        metadata={
            "difficulty_score": 1,
            "direct_dependency_count": 0,
            "dependency_depth": 0,
        },
    ).model_copy(update={"imports": ("Mathlib.Data.Nat.Prime.Basic",)})
    free_type_var = fixture_candidate(
        slug="free_alpha",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="Set.freeAlpha",
        type_expr="∀ x : Set α, x = x",
        queue_depth=0,
        metadata={
            "difficulty_score": 1,
            "direct_dependency_count": 0,
            "dependency_depth": 0,
        },
    ).model_copy(update={"imports": ("Mathlib.Data.Set.Basic",)})
    free_value_var = fixture_candidate(
        slug="free_set",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="Set.freeSet",
        type_expr="s.Nonempty ↔ ∅ ⊂ s",
        queue_depth=0,
        metadata={
            "difficulty_score": 1,
            "direct_dependency_count": 0,
            "dependency_depth": 0,
        },
    ).model_copy(update={"imports": ("Mathlib.Data.Set.Basic",)})
    fixture_without_dependency_metadata = fixture_candidate(
        slug="fixture_mathlib",
        source_stream="mathlib_snapshot",
        source_name="snapshot",
        theorem_name="Fixture.mathlib",
        type_expr="true = false",
        queue_depth=0,
    ).model_copy(update={"imports": ("Mathlib",)})

    eligible = _eligible_depth2_sources(
        (
            easy,
            medium,
            too_broad,
            broad_import,
            free_type_var,
            free_value_var,
            fixture_without_dependency_metadata,
        ),
        max_queue_depth=16,
    )

    assert {source.id for source in eligible} == {
        easy.id,
        medium.id,
        broad_import.id,
    }


def test_depth2_sources_require_productive_specialization() -> None:
    def source(slug: str, type_expr: str):
        return fixture_candidate(
            slug=slug,
            source_stream="mathlib_snapshot",
            source_name="snapshot",
            theorem_name=f"Source.{slug}",
            type_expr=type_expr,
            queue_depth=0,
            metadata={
                "difficulty_score": 1,
                "direct_dependency_count": 0,
                "dependency_depth": 0,
            },
        ).model_copy(update={"imports": ("Mathlib.Data.Nat.Basic",)})

    good = source("good", "∀ n m : Nat, n = m")
    one_binder = source("one_binder", "∀ n : Nat, n = n")
    instance_after_value = source("instance_after_value", "∀ n : Nat, ∀ [NeZero n], n = n")
    prop_first = source("prop_first", "∀ p : Prop, ∀ n : Nat, n = n")
    no_binder = source("no_binder", "true = false")

    eligible = _eligible_depth2_sources(
        (good, one_binder, instance_after_value, prop_first, no_binder),
        max_queue_depth=16,
    )

    assert tuple(source.id for source in eligible) == (good.id,)


def test_depth2_generation_uses_public_yield_history_for_source_order(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "theorem_name": "Yield.low",
                        "type_expr": "∀ a b : Bool, a = b",
                        "imports": ["Mathlib.Data.Bool.Basic"],
                        "mathlib_rev": "abc123",
                        "source_path": "Mathlib/Low.lean",
                        "source_license": "Apache-2.0",
                        "queue_depth": 0,
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "theorem_name": "Yield.high",
                        "type_expr": "∀ a b : Bool, a ≠ b ↔ b ≠ a",
                        "imports": ["Mathlib.Data.Bool.Basic"],
                        "mathlib_rev": "abc123",
                        "source_path": "Mathlib/High.lean",
                        "source_license": "Apache-2.0",
                        "queue_depth": 0,
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    history_path = tmp_path / "yield-history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "accepted_operator_chains": {"symm,specialize": 2},
                "accepted_source_families": {"Mathlib/High.lean": 5},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    history = read_yield_history(history_path)

    candidates = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="yield-order",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
        citation_alpha=0.0,
        yield_history=history,
        mutation_engine=StructuralMutationEngine(),
    )

    assert candidates[0].metadata["source_theorem_name"] == "Yield.high"
    assert candidates[0].metadata["yield_history_version"] == "lemma-procedural-yield-history-v1"
    assert candidates[0].metadata["yield_history_sha256"] == history.sha256
    assert candidates[0].metadata["yield_history_entries"] == 1


def test_depth_balanced_sources_interleave_available_depths() -> None:
    sources = tuple(
        fixture_candidate(
            slug=f"depth_{depth}_{index}",
            source_stream="mathlib_snapshot",
            source_name="snapshot",
            theorem_name=f"Depth.t{depth}_{index}",
            type_expr="true = false",
            queue_depth=depth,
        )
        for depth, index in ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (2, 0))
    )

    ordered = _depth_balanced_sources(sources)

    assert [source.queue_depth for source in ordered] == [2, 1, 0, 1, 0, 0]


def test_depth2_generation_skips_alpha_equivalent_duplicates_before_lean(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(
        json.dumps(
            {
                "theorem_name": "Duplicate.bool",
                "type_expr": "∀ a b : Bool, a = b",
                "imports": ["Mathlib.Data.Bool.Basic"],
                "mathlib_rev": "abc123",
                "source_path": "Mathlib/Duplicate.lean",
                "source_license": "Apache-2.0",
                "queue_depth": 0,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    class CountingBatchGate(AssumedProceduralGateRunner):
        def __init__(self) -> None:
            super().__init__()
            self.batch_sizes: list[int] = []

        def batch(
            self,
            candidates,  # noqa: ANN001
            *,
            seen_canonical_hashes,  # noqa: ANN001
        ) -> tuple[ProceduralGateVerdict, ...]:
            self.batch_sizes.append(len(candidates))
            return tuple(self(candidate, seen_canonical_hashes=seen_canonical_hashes) for candidate in candidates)

    class DuplicateMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            if step == 0:
                return MutationResult(
                    "∀ a b : Bool, true = b",
                    {"rule": "reverse_relation", "relation": "=", "engine": MUTATION_ENGINE},
                )
            return MutationResult(
                "true = true",
                {"binder": "b", "binder_type": "Bool", "value": "true", "engine": MUTATION_ENGINE},
            )

    gate = CountingBatchGate()
    with pytest.raises(ValueError, match="procedural gates accepted 1 candidates, needed 2"):
        generate_depth2_candidates(
            mathlib_candidates_from_jsonl(snapshot),
            generation_seed="dedupe",
            epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
            count=2,
            tempo=3,
            citation_alpha=0.0,
            gate_runner=gate,
            mutation_engine=DuplicateMutationEngine(),
            generation_workers=4,
        )

    assert gate.batch_sizes == [1]


def test_procedural_supply_mode_uses_explicit_active_registry_cache(tmp_path: Path) -> None:
    task = make_task(
        task_id="lemma.cached.active",
        title="Cached active",
        theorem_name="cached_active",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    registry_path = tmp_path / "tempo-3.registry.json"
    write_registry([task], registry_path)
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        active_registry_json=registry_path,
    )

    registry = task_registry_for_validation(settings, tempo=3)

    assert [task.id for task in registry.tasks] == ["lemma.cached.active"]


def test_procedural_supply_mode_fails_closed_for_missing_explicit_active_registry(tmp_path: Path) -> None:
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        active_registry_json=tmp_path / "missing.registry.json",
    )

    with pytest.raises(RuntimeError, match="active registry file does not exist"):
        task_registry_for_validation(settings, tempo=3)


def test_procedural_supply_mode_uses_tempo_active_registry_cache_dir(tmp_path: Path) -> None:
    task = make_task(
        task_id="lemma.cached.tempo",
        title="Cached tempo",
        theorem_name="cached_tempo",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    cache_dir = tmp_path / "registries"
    cache_dir.mkdir()
    write_registry([task], cache_dir / "tempo-7.registry.json")
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        active_registry_cache_dir=cache_dir,
    )

    registry = task_registry_for_validation(settings, tempo=7)

    assert [task.id for task in registry.tasks] == ["lemma.cached.tempo"]


def test_active_registry_auditor_mode_refuses_local_generation(tmp_path: Path) -> None:
    settings = LemmaSettings(
        _env_file=None,
        task_supply_mode="procedural",
        active_registry_role="auditor",
        active_registry_cache_dir=tmp_path / "registries",
    )

    with pytest.raises(RuntimeError, match="auditor mode requires a current active-registry cache"):
        task_registry_for_validation(settings, tempo=7)


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
    monkeypatch.setattr("lemma.supply.gates.LeanProceduralGateRunner.batch", _fake_lean_gate_batch)
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
    } == {(("lemma_substrate", 1), ("mathlib_snapshot", 3))}
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

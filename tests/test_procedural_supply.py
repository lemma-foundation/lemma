from __future__ import annotations

import hashlib
import json
import threading
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
    TRIVIALITY_STACK,
    AssumedProceduralGateRunner,
    LeanProceduralGateRunner,
    ProceduralGateVerdict,
)
from lemma.supply.import_graph import ImportGraphRow, extract_import_graph_rows, read_import_graph
from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
from lemma.supply.mutation import LeanAstMutationEngine, MutationResult, PreviewMutationEngine
from lemma.supply.novelty import novelty_cache_from_hashes, read_novelty_cache, statement_hash
from lemma.supply.operator_bundle import OPERATOR_BUNDLE_VERSION, OPERATOR_NAMES, SMALL_VALUES_BY_TYPE
from lemma.supply.procedural import (
    _candidate_from_source,
    build_procedural_registry_tasks,
    corpus_sources_from_dir,
    generate_depth2_candidates,
    procedural_operator_bundle_hash,
    source_pool_hash,
)
from lemma.supply.slot_weight import slot_weight_receipt_for_candidate
from lemma.supply.triviality_budget import TrivialityRetargetConfig, triviality_budget_receipt
from lemma.supply.types import fixture_candidate
from lemma.task_supply import make_task, write_registry
from lemma.validator import active_epoch_seed, active_tasks_for_validation, task_registry_for_validation


def test_operator_bundle_includes_lean_pretty_type_aliases() -> None:
    assert SMALL_VALUES_BY_TYPE["\u2115"] == SMALL_VALUES_BY_TYPE["Nat"]
    assert SMALL_VALUES_BY_TYPE["\u2124"] == SMALL_VALUES_BY_TYPE["Int"]
    assert SMALL_VALUES_BY_TYPE["\u211A"] == SMALL_VALUES_BY_TYPE["Rat"]
    assert SMALL_VALUES_BY_TYPE["\u211D"] == SMALL_VALUES_BY_TYPE["Real"]


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
    assert "let roundtrip ← parseTermOrThrow rendered" in captured["problem"].extra["challenge_full"]
    assert captured["problem"].extra["lean_max_heartbeats"] == 400_000
    assert captured["problem"].extra["lean_eval_commands"] == ("#lemma_emit_mutation",)
    assert 'elab "#lemma_emit_mutation" : command => emit' in captured["problem"].extra["challenge_full"]
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


@pytest.mark.parametrize(
    "params",
    (
        {"fallback": "true_premise"},
        {"fallback": "unsupported_binder_type"},
        {"fallback": "no_supported_type_occurrence"},
        {"mode": "peer_premise"},
        {"rule": "conjoin_peer_conclusion"},
        {"rule": "false_disjunct"},
        {"target": "fresh_prop_hypothesis"},
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


def test_depth2_generation_attempt_limit_scales_with_requested_count() -> None:
    class AlwaysGoodMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return MutationResult(f"∀ p{step} : Prop, p{step} → ({type_expr})", {"target": "pytest"})

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
            type_expr="∀ n : Nat, n = n",
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
            assert problem.extra["lean_max_heartbeats"] == 400_000
            gate_source = str(problem.extra["challenge_full"])
            is_typecheck = "def typecheck_gate" in gate_source
            if not is_typecheck:
                assert problem.extra["lean_eval_commands"] == ("#lemma_emit_kernel_normal",)
                assert 'elab "#lemma_emit_kernel_normal" : command => emit_kernel_normal' in problem.extra[
                    "challenge_full"
                ]
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
        assert problem.extra["lean_max_heartbeats"] == 200_000
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
    assert verdict.metadata["triviality_budget_heartbeats"] == 200_000
    assert verdict.metadata["triviality_reason"] == "baseline_failed"
    assert calls[:2] == ["typecheck", "prop"]
    assert "triviality" in calls


def test_lean_gate_runner_caps_parallel_verify_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    candidate = generate_depth2_candidates(
        mathlib_candidates_from_jsonl(snapshot),
        generation_seed="epoch-a",
        epoch_randomness=json.dumps({"anchor_block": 720, "drand_round": 11}, sort_keys=True),
        count=1,
        tempo=3,
    )[0]
    lock = threading.Lock()
    active = 0
    max_active = 0
    calls: list[str] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            if problem.id.endswith(".gate"):
                assert problem.extra["lean_max_heartbeats"] == 400_000
                gate_source = str(problem.extra["challenge_full"])
                is_typecheck = "def typecheck_gate" in gate_source
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
            assert problem.extra["lean_max_heartbeats"] == 200_000
            assert verify_timeout_s == 5
            time.sleep(0.02)
            return VerifyResult(passed=False, reason="compile_error")
        finally:
            with lock:
                active -= 1

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
    assert calls[:2] == ["typecheck", "prop"]
    assert calls.count("triviality") == len(TRIVIALITY_STACK) + 1
    assert max_active == 2


def test_lean_gate_runner_rejects_source_theorem_wrappers(
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
    proof_scripts: list[str] = []

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001, ARG001
        if problem.id.endswith(".gate"):
            gate_source = str(problem.extra["challenge_full"])
            is_typecheck = "def typecheck_gate" in gate_source
            return VerifyResult(
                passed=True,
                reason="ok",
                stdout_tail=""
                if is_typecheck
                else "LEMMA_KERNEL_NORMAL_FORM (forall default const:True:[] const:True:[])",
                declaration_fingerprints={str(problem.extra["lean_fingerprint_names"][0]): "8" * 64},
            )
        proof_scripts.append(proof_script)
        return VerifyResult(passed="apply " in proof_script, reason="ok")

    monkeypatch.setattr("lemma.supply.gates.run_lean_verify", fake_verify)
    verdict = LeanProceduralGateRunner(
        LemmaSettings(_env_file=None, lean_use_docker=False, procedural_gate_timeout_s=5)
    )(candidate, seen_canonical_hashes=())

    assert verdict.accepted is False
    assert verdict.baseline_solved is True
    assert verdict.metadata["baseline_solver"] == "source_theorem"
    assert len(proof_scripts) == 1
    assert "apply " in proof_scripts[0]


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
                declaration_fingerprints={str(problem.extra["lean_fingerprint_names"][0]): fallback_hash},
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


def test_public_novelty_cache_can_be_built_from_type_expr_rows(tmp_path: Path) -> None:
    path = tmp_path / "novelty.jsonl"
    path.write_text(json.dumps({"type_expr": "True   →   True"}, sort_keys=True) + "\n", encoding="utf-8")

    cache = read_novelty_cache(path)

    assert cache.contains(statement_hash("True → True"))


def test_procedural_slot_weight_receipt_uses_dependency_metadata(tmp_path: Path) -> None:
    class SlotWeightMutationEngine:
        def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001, ARG002
            return MutationResult(f"∀ p{step} : Prop, p{step} → ({type_expr})", {"target": "pytest"})

    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(
        json.dumps(
            {
                "theorem_name": "Deep.weight",
                "type_expr": "∀ n : Nat, n = n",
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
        mutation_engine=SlotWeightMutationEngine(),
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


def test_procedural_supply_filters_sources_to_frontier_depth(monkeypatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    with snapshot.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "theorem_name": "Hard.depth_one",
                    "type_expr": "∀ n : Nat, n = n",
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
    assert captured_depths == [0, 0, 1]


def test_depth2_generation_filters_ordering_without_changing_source_hash(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    _write_snapshot(snapshot)
    with snapshot.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "theorem_name": "Hard.depth_one",
                    "type_expr": "∀ n : Nat, n = n",
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

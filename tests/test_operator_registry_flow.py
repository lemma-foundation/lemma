"""Executable smoke for the documented operator registry flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bittensor_wallet import Keypair
from click.testing import CliRunner
from lemma.chain.commitments import (
    ciphertext_sha256,
    miner_bucket_commitment_payload,
    miner_submission_merkle_root,
)
from lemma.chain.miner_buckets import MinerBucketReveal, RevealedBucketBlob
from lemma.cli.main import main
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport, OperatorRegistryInspectReport
from lemma.submissions import build_submission
from lemma.supply.controller import CurriculumTempoRecord, append_curriculum_record
from lemma.supply.gates import ProceduralGateVerdict
from lemma.supply.import_graph import ImportGraphRow, read_import_graph
from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
from lemma.supply.mutation import PreviewMutationEngine
from lemma.supply.novelty import novelty_cache_from_hashes
from lemma.supply.procedural import procedural_operator_bundle_hash, source_pool_hash
from lemma.supply.slot_weight import slot_weight_receipt_for_candidate
from lemma.supply.triviality_budget import TrivialityRetargetConfig, triviality_budget_receipt
from lemma.tasks import load_task_registry
from lemma.validator import active_epoch_seed, active_tasks_for_validation, task_registry_for_validation


class _PreviewMutationEngineForProduction:
    def __init__(self, settings: LemmaSettings) -> None:
        _ = settings
        self.preview = PreviewMutationEngine()

    def apply(self, source, type_expr, operator, *, step, param_seed, peer):  # noqa: ANN001
        result = self.preview.apply(source, type_expr, operator, step=step, param_seed=param_seed, peer=peer)
        return result.__class__(result.type_expr, {**result.params, "engine": "lean_ast_elaborator"})


def _proof_for(theorem_name: str) -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def _write_import_graph(path: Path) -> None:
    rows = (
        ImportGraphRow(module="Mathlib", imports=("Mathlib.Init",)),
        ImportGraphRow(module="Mathlib.Init", imports=()),
    )
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


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


def test_operator_registry_flow_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    fixture_dir = Path("examples/operator-smoke")
    snapshot = fixture_dir / "snapshot.jsonl"
    registry_path = tmp_path / "tasks" / "mathlib-snapshot.registry.json"

    build = runner.invoke(
        main,
        [
            "tasks",
            "build-mathlib-snapshot",
            "--input",
            str(snapshot),
            "--output",
            str(registry_path),
            "--seed",
            "operator-smoke",
            "--frontier-depth",
            "0",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )

    assert build.exit_code == 0, build.output
    registry_sha256 = json.loads(build.output)["registry_sha256"]
    registry = load_task_registry(registry_path.read_bytes(), registry_sha256)
    active_task = registry.get("lemma.mathlib_snapshot.operator_smoke_true_0")
    inactive_task = registry.get("lemma.mathlib_snapshot.operator_smoke_deep_true")

    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
        "LEMMA_ACTIVE_K": "10",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_ACTIVE_QUEUE_SEED": "operator-smoke",
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_USE_DOCKER": "0",
        "LEMMA_ALLOW_HOST_LEAN": "1",
        "BT_WALLET_HOT": "validator-smoke",
    }
    proof_path = fixture_dir / "Submission.lean"
    package_path = tmp_path / "submission.json"

    inspect = runner.invoke(main, ["operator", "registry-inspect"], env=env)

    assert inspect.exit_code == 0, inspect.output
    inspect_payload = OperatorRegistryInspectReport.model_validate_json(inspect.output)
    assert inspect_payload.registry_sha256 == registry_sha256
    assert inspect_payload.total_task_count == 11
    assert inspect_payload.active_task_count == 10
    assert inspect_payload.eligible_task_count == 10
    assert inspect_payload.waiting_task_count == 0
    assert inspect_payload.parked_task_count == 1
    assert inspect_payload.queue_depth_counts == {"0": 10, "2": 1}

    preflight = runner.invoke(main, ["operator", "preflight"], env=env)

    assert preflight.exit_code == 0, preflight.output
    preflight_payload = OperatorPreflightReport.model_validate_json(preflight.output)
    preflight_checks = {check.name: check for check in preflight_payload.checks}
    assert preflight_payload.ok is True
    assert preflight_payload.registry_sha256 == registry_sha256
    assert preflight_payload.active_K == 10
    assert preflight_checks["registry_hash_pin"].ok is True
    assert preflight_checks["active_window"].detail.startswith("10 active / K=10")
    assert preflight_checks["corpus_output_dir"].ok is True
    assert preflight_checks["operator_data_dir"].ok is True
    assert preflight_checks["lean_verifier"].detail == "host Lean enabled"

    diagnostics_path = tmp_path / "operator-diagnostics.json"
    diagnostics = runner.invoke(
        main,
        ["operator", "diagnostics", "--output", str(diagnostics_path)],
        env=env,
    )

    assert diagnostics.exit_code == 0, diagnostics.output
    diagnostics_summary = json.loads(diagnostics.output)
    diagnostics_text = diagnostics_path.read_text(encoding="utf-8")
    diagnostics_payload = OperatorDiagnosticsReport.model_validate_json(diagnostics_text)
    assert diagnostics_summary["active_task_count"] == 10
    assert diagnostics_summary["eligible_task_count"] == 10
    assert diagnostics_summary["parked_task_count"] == 1
    assert diagnostics_payload.preflight.ok is True
    assert diagnostics_payload.registry_sha256 == registry_sha256
    assert diagnostics_payload.registry_inspect == inspect_payload
    assert diagnostics_payload.artifacts.validator_run_count == 0
    assert diagnostics_payload.artifacts.verification_record_count == 0
    assert diagnostics_payload.artifacts.score_event_count == 0
    assert diagnostics_payload.artifacts.corpus_row_count == 0
    assert active_task.id in diagnostics_payload.active_task_ids
    assert inactive_task.id not in diagnostics_payload.active_task_ids
    assert str(tmp_path) not in diagnostics_text
    assert "LEMMA_TASK_REGISTRY_URL" not in diagnostics_text

    submit = runner.invoke(
        main,
        [
            "submit",
            active_task.id,
            "--submission",
            str(proof_path),
            "--solver-hotkey",
            "miner-active",
            "--output",
            str(package_path),
        ],
        env=env,
    )

    assert submit.exit_code == 0, submit.output
    active_submission = json.loads(package_path.read_text(encoding="utf-8"))
    inactive_submission = build_submission(
        inactive_task,
        solver_hotkey="miner-inactive",
        proof_script=_proof_for(inactive_task.theorem_name),
    ).model_dump(mode="json", exclude_none=True)
    submissions_jsonl = tmp_path / "submissions.jsonl"
    submissions_jsonl.write_text(
        json.dumps(active_submission, sort_keys=True) + "\n"
        + json.dumps(inactive_submission, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    validate = runner.invoke(
        main,
        [
            "validate",
            "--once",
            "--submissions-jsonl",
            str(submissions_jsonl),
            "--validator-hotkey",
            "validator-smoke",
            "--no-set-weights",
        ],
        env=env,
    )

    assert validate.exit_code == 0, validate.output
    validation = json.loads(validate.output)
    assert validation["accepted_unique"] == 1
    assert validation["corpus_rows"] == 1
    assert validation["scores"] == {"miner-active": 0.1}
    assert validation["unearned_policy"] == "burn"
    assert validation["unearned_share"] == 0.9
    assert validation["weights"] == {"burn_uid:0": 0.9, "miner-active": 0.1}
    assert validation["weights_set"] is False
    assert "inactive_task" in (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    run_summary = json.loads((tmp_path / "operator" / "validator-runs.jsonl").read_text(encoding="utf-8"))
    assert run_summary["registry_sha256"] == registry_sha256
    assert run_summary["active_K"] == 10
    assert run_summary["verified_count"] == 1
    assert run_summary["accepted_unique_count"] == 1
    assert run_summary["rewarded_count"] == 1
    assert run_summary["score_event_count"] == 1
    assert run_summary["corpus_row_count"] == 1
    assert run_summary["unearned_share"] == 0.9
    assert run_summary["weights_set"] is False

    post_diagnostics_path = tmp_path / "operator-diagnostics-after.json"
    post_diagnostics = runner.invoke(
        main,
        ["operator", "diagnostics", "--output", str(post_diagnostics_path)],
        env=env,
    )

    assert post_diagnostics.exit_code == 0, post_diagnostics.output
    post_diagnostics_payload = OperatorDiagnosticsReport.model_validate_json(
        post_diagnostics_path.read_text(encoding="utf-8")
    )
    assert post_diagnostics_payload.artifacts.verification_record_count == 2
    assert post_diagnostics_payload.artifacts.validator_run_count == 1
    assert post_diagnostics_payload.artifacts.score_event_count == 1
    assert post_diagnostics_payload.artifacts.corpus_jsonl_file_count == 1
    assert post_diagnostics_payload.artifacts.corpus_row_count == 1

    corpus_jsonl = tmp_path / "corpus" / "epoch-000001.jsonl"
    corpus_validate = runner.invoke(main, ["corpus", "validate", str(corpus_jsonl)], env=env)

    assert corpus_validate.exit_code == 0, corpus_validate.output
    corpus_row = json.loads(corpus_jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert corpus_row["active_K"] == 10
    assert corpus_row["queue_depth"] == 0
    assert corpus_row["source_stream"] == "mathlib_snapshot"
    assert corpus_row["rewarded"] is True

    corpus_index = tmp_path / "exports" / "corpus-index.json"
    corpus_export = runner.invoke(
        main,
        ["corpus", "export", "--input", str(tmp_path / "corpus"), "--output", str(corpus_index)],
        env=env,
    )

    assert corpus_export.exit_code == 0, corpus_export.output
    assert json.loads(corpus_index.read_text(encoding="utf-8"))["row_count"] == 1

    benchmark_jsonl = tmp_path / "exports" / "lemma-proofs.jsonl"
    benchmark_index = tmp_path / "exports" / "benchmark-index.json"
    benchmark_export = runner.invoke(
        main,
        [
            "corpus",
            "benchmark-export",
            "--input",
            str(tmp_path / "corpus"),
            "--output",
            str(benchmark_jsonl),
            "--index",
            str(benchmark_index),
        ],
        env=env,
    )

    assert benchmark_export.exit_code == 0, benchmark_export.output
    benchmark_summary = json.loads(benchmark_export.output)
    assert benchmark_summary["row_count"] == 1
    assert benchmark_summary["source_streams"] == {"mathlib_snapshot": 1}
    benchmark_record = json.loads(benchmark_jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert benchmark_record["reward"]["active_K"] == 10
    assert benchmark_record["provenance"]["validator_hotkey"] == "validator-smoke"


def test_production_like_procedural_submission_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    snapshot_path = Path("examples/operator-smoke/snapshot.jsonl")
    novelty_cache_path = tmp_path / "novelty-cache.jsonl"
    import_graph_path = tmp_path / "import-graph.jsonl"
    prior_corpus_dir = tmp_path / "prior-corpus"
    novelty_cache_path.write_text(json.dumps({"statement_hash": "0" * 64}, sort_keys=True) + "\n", encoding="utf-8")
    _write_import_graph(import_graph_path)
    prior_corpus_dir.mkdir()
    registry_path = tmp_path / "tasks" / "mainnet.procedural.registry.json"
    active_randomness = json.dumps(
        {
            "source": "chain_drand",
            "anchor_block": 360,
            "drand_round": 10,
            "anchor_block_hash": "0xabc",
            "drand_signature": "0xsig",
        },
        sort_keys=True,
    )
    monkeypatch.setattr(
        "lemma.validator.resolve_active_epoch_randomness",
        lambda settings, *, tempo: active_randomness,
    )
    monkeypatch.setattr("lemma.supply.gates.LeanProceduralGateRunner.__call__", _fake_lean_gate)
    monkeypatch.setattr("lemma.supply.mutation.LeanAstMutationEngine", _PreviewMutationEngineForProduction)
    source_hash = source_pool_hash(mathlib_candidates_from_jsonl(snapshot_path))
    base_settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_supply_mode="procedural",
        procedural_source_jsonl=snapshot_path,
        procedural_novelty_cache_jsonl=novelty_cache_path,
        procedural_import_graph_jsonl=import_graph_path,
        procedural_prior_corpus_dir=prior_corpus_dir,
        procedural_source_sha256_expected=source_hash,
        procedural_operator_bundle_sha256_expected=procedural_operator_bundle_hash(),
        procedural_candidate_count=1,
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        active_task_count=1,
        frontier_depth=0,
        active_queue_seed="mainnet-readiness",
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
        active_tempo_source="wall_clock",
        active_tempo_seconds=999999999999,
        corpus_output_dir=tmp_path / "corpus",
        operator_data_dir=tmp_path / "operator",
        lean_sandbox_network="none",
        wallet_hot="validator-mainnet-readiness",
    )
    reveal_tempo = 7
    generation_seed = active_epoch_seed(base_settings, tempo=reveal_tempo)

    build = runner.invoke(
        main,
        [
            "tasks",
            "rebuild-procedural-registry",
            "--mathlib-snapshot",
            str(snapshot_path),
            "--output",
            str(registry_path),
            "--generation-seed",
            generation_seed,
            "--epoch-randomness",
            active_randomness,
            "--tempo",
            str(reveal_tempo),
            "--count",
            "1",
            "--frontier-depth",
            "0",
            "--novelty-cache-jsonl",
            str(novelty_cache_path),
            "--import-graph-jsonl",
            str(import_graph_path),
            "--prior-corpus-dir",
            str(prior_corpus_dir),
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )
    assert build.exit_code == 0, build.output
    assert json.loads(build.output)["source_pool_sha256"] == source_hash
    assert json.loads(build.output)["import_graph_sha256"] == read_import_graph(import_graph_path).sha256

    registry = task_registry_for_validation(base_settings, tempo=reveal_tempo)
    active_task = active_tasks_for_validation(registry, base_settings, tempo=reveal_tempo)[0]

    miner_keypair = Keypair.create_from_uri("//LemmaMainnetReadinessMiner")
    proof_script = _proof_for(active_task.theorem_name)
    ciphertext = "cipher-mainnet-readiness"
    merkle_root = miner_submission_merkle_root(((0, ciphertext_sha256(ciphertext.encode("utf-8"))),))
    bucket_reveals_jsonl = tmp_path / "bucket-reveals.jsonl"
    reveal = MinerBucketReveal(
        tempo=reveal_tempo,
        miner_hotkey=miner_keypair.ss58_address,
        drand_round=10,
        drand_signature="0xsig",
        commit_block=42,
        commit_extrinsic_hash="0xabc",
        merkle_root=merkle_root,
        bucket_url="https://bucket.example/mainnet-readiness",
        blobs=(RevealedBucketBlob(slot_index=0, ciphertext=ciphertext, proof_script=proof_script),),
    )
    bucket_reveals_jsonl.write_text(reveal.model_dump_json() + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "lemma.chain.commitments.read_all_commitments",
        lambda settings: {
            miner_keypair.ss58_address: miner_bucket_commitment_payload(
                tempo=reveal_tempo,
                drand_round=10,
                merkle_root=merkle_root,
            )
        },
    )

    import lemma.chain.miner_buckets as miner_buckets

    convert_bucket_reveals = miner_buckets.submissions_from_bucket_reveals

    def fake_bucket_reveals(*args: object, **kwargs: object):
        kwargs["decrypt_timelocked"] = lambda ciphertext, signature: proof_script.encode("utf-8")
        return convert_bucket_reveals(*args, **kwargs)

    monkeypatch.setattr(miner_buckets, "submissions_from_bucket_reveals", fake_bucket_reveals)

    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_PROTOCOL_MODE": "production",
        "LEMMA_TASK_SUPPLY_MODE": "procedural",
        "LEMMA_PROCEDURAL_SOURCE_JSONL": str(snapshot_path),
        "LEMMA_PROCEDURAL_NOVELTY_CACHE_JSONL": str(novelty_cache_path),
        "LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL": str(import_graph_path),
        "LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR": str(prior_corpus_dir),
        "LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED": source_hash,
        "LEMMA_PROCEDURAL_OPERATOR_BUNDLE_SHA256_EXPECTED": procedural_operator_bundle_hash(),
        "LEMMA_PROCEDURAL_CANDIDATE_COUNT": "1",
        "LEMMA_REQUIRE_SUBMISSION_SIGNATURES": "1",
        "LEMMA_REQUIRE_COMMIT_REVEAL": "1",
        "LEMMA_REQUIRE_STRONG_PROOF_IDENTITY": "1",
        "LEMMA_ACTIVE_K": "1",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_ACTIVE_QUEUE_SEED": "mainnet-readiness",
        "LEMMA_ACTIVE_SEED_MODE": "epoch_randomness",
        "LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE": "chain_drand",
        "LEMMA_ACTIVE_TEMPO_SOURCE": "wall_clock",
        "LEMMA_ACTIVE_TEMPO_SECONDS": "999999999999",
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_USE_DOCKER": "1",
        "LEAN_SANDBOX_NETWORK": "none",
        "BT_WALLET_HOT": "validator-mainnet-readiness",
    }

    preflight = runner.invoke(main, ["operator", "preflight"], env=env)
    assert preflight.exit_code == 0, preflight.output
    preflight_payload = OperatorPreflightReport.model_validate_json(preflight.output)
    checks = {check.name: check for check in preflight_payload.checks}
    assert preflight_payload.ok is True
    assert checks["registry_signature"].detail == "unsigned"
    assert checks["lean_network"].ok is True
    assert checks["live_submission_signatures"].ok is True
    assert checks["commit_reveal"].ok is True
    assert checks["strong_proof_identity"].ok is True
    assert checks["procedural_supply"].ok is True

    before_path = tmp_path / "operator-diagnostics-before.json"
    before = runner.invoke(main, ["operator", "diagnostics", "--output", str(before_path)], env=env)
    assert before.exit_code == 0, before.output

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok", proof_term_hash="term-mainnet-readiness")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    validate = runner.invoke(
        main,
        [
            "validate",
            "--once",
            "--bucket-reveals-jsonl",
            str(bucket_reveals_jsonl),
            "--validator-hotkey",
            "validator-mainnet-readiness",
            "--no-set-weights",
        ],
        env=env,
    )
    assert validate.exit_code == 0, validate.output
    validation = json.loads(validate.output)
    assert validation["accepted_unique"] == 1
    assert validation["scores"] == {miner_keypair.ss58_address: 1.0}
    assert validation["weights_set"] is False

    after_path = tmp_path / "operator-diagnostics-after.json"
    after = runner.invoke(main, ["operator", "diagnostics", "--output", str(after_path)], env=env)
    assert after.exit_code == 0, after.output
    after_payload = OperatorDiagnosticsReport.model_validate_json(after_path.read_text(encoding="utf-8"))
    assert after_payload.artifacts.validator_run_count == 1
    assert after_payload.artifacts.corpus_row_count == 1

    corpus_jsonl = tmp_path / "corpus" / "epoch-000001.jsonl"
    corpus_validate = runner.invoke(main, ["corpus", "validate", str(corpus_jsonl)], env=env)
    assert corpus_validate.exit_code == 0, corpus_validate.output
    corpus_row = json.loads(corpus_jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert corpus_row["rewarded"] is True
    assert corpus_row["proof_identity"] == "term-mainnet-readiness"
    assert corpus_row["proof_identity_source"] == "proof_term_hash"
    assert corpus_row["proof_identity_strength"] == "strong"
    assert corpus_row["full_reward_eligible"] is True


def test_operator_reports_use_curriculum_controlled_active_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    fixture_dir = Path("examples/operator-smoke")
    registry_path = tmp_path / "tasks" / "mathlib-snapshot.registry.json"
    build = runner.invoke(
        main,
        [
            "tasks",
            "build-mathlib-snapshot",
            "--input",
            str(fixture_dir / "snapshot.jsonl"),
            "--output",
            str(registry_path),
            "--seed",
            "operator-smoke",
            "--frontier-depth",
            "2",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )
    assert build.exit_code == 0, build.output
    registry_sha256 = json.loads(build.output)["registry_sha256"]
    state_path = tmp_path / "curriculum.jsonl"
    append_curriculum_record(
        state_path,
        CurriculumTempoRecord(
            tempo=4,
            active_K=2,
            frontier_depth=2,
            ema_solve_rate=0.5,
            solved_slots=1,
            parked_task_ids=(),
            action="hold",
            variant_stream_requested=False,
        ),
    )
    monkeypatch.setattr("lemma.validator.current_active_tempo", lambda settings: 5)
    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
        "LEMMA_ACTIVE_K": "1",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_ACTIVE_QUEUE_SEED": "operator-smoke",
        "LEMMA_CURRICULUM_RETARGET": "1",
        "LEMMA_CURRICULUM_STATE_JSONL": str(state_path),
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_USE_DOCKER": "0",
        "LEMMA_ALLOW_HOST_LEAN": "1",
    }

    inspect = runner.invoke(main, ["operator", "registry-inspect"], env=env)
    assert inspect.exit_code == 0, inspect.output
    inspect_payload = OperatorRegistryInspectReport.model_validate_json(inspect.output)
    assert inspect_payload.active_K == 2
    assert inspect_payload.frontier_depth == 2
    assert inspect_payload.active_task_count == 2
    assert inspect_payload.eligible_task_count == 11

    preflight = runner.invoke(main, ["operator", "preflight"], env=env)
    assert preflight.exit_code == 0, preflight.output
    preflight_payload = OperatorPreflightReport.model_validate_json(preflight.output)
    checks = {check.name: check for check in preflight_payload.checks}
    assert preflight_payload.active_K == 2
    assert preflight_payload.frontier_depth == 2
    assert checks["active_window"].detail.startswith("2 active / K=2 at frontier_depth=2")


def test_production_preflight_fails_closed_without_launch_flags(tmp_path: Path) -> None:
    runner = CliRunner()
    fixture_dir = Path("examples/operator-smoke")
    registry_path = tmp_path / "tasks" / "registry.json"
    build = runner.invoke(
        main,
        [
            "tasks",
            "build-mathlib-snapshot",
            "--input",
            str(fixture_dir / "snapshot.jsonl"),
            "--output",
            str(registry_path),
            "--seed",
            "operator-smoke",
            "--frontier-depth",
            "0",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )
    registry_sha256 = json.loads(build.output)["registry_sha256"]
    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_PROTOCOL_MODE": "production",
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
        "LEMMA_ACTIVE_K": "10",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_ACTIVE_QUEUE_SEED": "operator-smoke",
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_USE_DOCKER": "1",
        "LEAN_SANDBOX_NETWORK": "bridge",
    }

    preflight = runner.invoke(main, ["operator", "preflight"], env=env)

    assert preflight.exit_code == 1
    payload = OperatorPreflightReport.model_validate_json(preflight.output)
    checks = {check.name: check.ok for check in payload.checks}
    assert checks["registry_load"] is False
    assert checks["registry_hash_pin"] is False
    assert checks["lean_network"] is False
    assert checks["live_submission_signatures"] is False
    assert checks["commit_reveal"] is False
    assert checks["strong_proof_identity"] is False
    assert checks["procedural_supply"] is False

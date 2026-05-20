"""Executable smoke for the documented operator registry flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bittensor_wallet import Keypair
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.lean.sandbox import VerifyResult
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport, OperatorRegistryInspectReport
from lemma.submissions import build_submission, sign_submission
from lemma.supply.types import TaskCandidate, lean_stub
from lemma.tasks import SourceRef, Ss58RegistrySignatureVerifier, load_task_registry


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


def test_production_like_signed_registry_submission_smoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    candidate_path = tmp_path / "candidates.jsonl"
    registry_path = tmp_path / "tasks" / "mainnet.registry.json"
    signed_registry_path = tmp_path / "tasks" / "mainnet.signed.registry.json"
    theorem_name = "mainnet_readiness_true"
    candidate = TaskCandidate(
        id="lemma.procedural.mainnet_readiness_true",
        title="Mainnet readiness true",
        source_stream="procedural",
        source_ref=SourceRef(
            kind="procedural",
            name="mainnet_readiness_true",
            commit="anchor-block-360",
        ),
        source_license="Apache-2.0",
        imports=("Mathlib",),
        theorem_name=theorem_name,
        type_expr="True",
        statement=f"theorem {theorem_name} : True := by\n  sorry",
        submission_stub=lean_stub(theorem_name, "True"),
        mathlib_rev="mainnet-readiness-rev",
        queue_depth=0,
        metadata={
            "activation_status": "paid",
            "supply_mode": "procedural",
            "mutation_depth": 2,
            "mutation_chain": [
                {"operator": "generalize", "input_hash": "1" * 64, "output_hash": "2" * 64},
                {"operator": "specialize", "input_hash": "2" * 64, "output_hash": "3" * 64},
            ],
            "generation_seed": "mainnet-readiness",
            "drand_round": 10,
            "anchor_block": 360,
            "source_pool_hash": "4" * 64,
            "operator_bundle_hash": "5" * 64,
            "canonical_hash": "6" * 64,
            "typechecked": True,
            "prop_gate_passed": True,
            "novelty_status": "passed",
            "baseline_solved": False,
            "slot_weight": 1.0,
            "license_state": "clean_open",
            "triviality_checked": True,
        },
    )
    candidate_path.write_text(candidate.model_dump_json() + "\n", encoding="utf-8")

    build = runner.invoke(
        main,
        [
            "tasks",
            "build-procedural-registry",
            "--candidate-jsonl",
            str(candidate_path),
            "--output",
            str(registry_path),
            "--seed",
            "mainnet-readiness",
            "--frontier-depth",
            "0",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )
    assert build.exit_code == 0, build.output

    sign_registry = runner.invoke(
        main,
        [
            "tasks",
            "sign-registry",
            "--input",
            str(registry_path),
            "--output",
            str(signed_registry_path),
            "--key-uri",
            "//LemmaRegistrySigner",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )
    assert sign_registry.exit_code == 0, sign_registry.output
    signed_registry_sha256 = json.loads(sign_registry.output)["registry_sha256"]
    registry = load_task_registry(
        signed_registry_path.read_bytes(),
        signed_registry_sha256,
        signature_verifier=Ss58RegistrySignatureVerifier(),
    )
    active_task = registry.get(candidate.id)

    miner_keypair = Keypair.create_from_uri("//LemmaMainnetReadinessMiner")
    submission = sign_submission(
        build_submission(
            active_task,
            solver_hotkey=miner_keypair.ss58_address,
            proof_script=_proof_for(active_task.theorem_name),
        ).model_copy(
            update={
                "timelock_ciphertext": "ciphertext",
                "drand_round": 10,
                "commit_block": 42,
                "commit_extrinsic_hash": "0xabc",
            }
        ),
        miner_keypair,
    )
    submissions_jsonl = tmp_path / "submissions.jsonl"
    submissions_jsonl.write_text(submission.model_dump_json(exclude_none=True) + "\n", encoding="utf-8")

    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_PROTOCOL_MODE": "production",
        "LEMMA_VERIFY_REGISTRY_SIGNATURES": "1",
        "LEMMA_REQUIRE_SUBMISSION_SIGNATURES": "1",
        "LEMMA_REQUIRE_COMMIT_REVEAL": "1",
        "LEMMA_REQUIRE_STRONG_PROOF_IDENTITY": "1",
        "LEMMA_TASK_REGISTRY_URL": str(signed_registry_path),
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": signed_registry_sha256,
        "LEMMA_ACTIVE_K": "1",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_ACTIVE_QUEUE_SEED": "mainnet-readiness",
        "LEMMA_ACTIVE_TEMPO_SOURCE": "wall_clock",
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
    assert checks["registry_signature"].detail == "verified"
    assert checks["lean_network"].ok is True
    assert checks["live_submission_signatures"].ok is True
    assert checks["commit_reveal"].ok is True
    assert checks["strong_proof_identity"].ok is True
    assert checks["procedural_supply"].ok is True

    before_path = tmp_path / "operator-diagnostics-before.json"
    before = runner.invoke(main, ["operator", "diagnostics", "--output", str(before_path)], env=env)
    assert before.exit_code == 0, before.output

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok", structural_fingerprint="structural-mainnet-readiness")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    validate = runner.invoke(
        main,
        [
            "validate",
            "--once",
            "--submissions-jsonl",
            str(submissions_jsonl),
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
    assert corpus_row["proof_identity"] == "structural-mainnet-readiness"
    assert corpus_row["proof_identity_source"] == "structural_fingerprint"
    assert corpus_row["proof_identity_strength"] == "strong"
    assert corpus_row["full_reward_eligible"] is True


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
    assert checks["registry_signature"] is False
    assert checks["lean_network"] is False
    assert checks["live_submission_signatures"] is False
    assert checks["commit_reveal"] is False
    assert checks["strong_proof_identity"] is False
    assert checks["procedural_supply"] is False

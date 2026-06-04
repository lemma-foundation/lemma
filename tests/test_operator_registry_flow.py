"""Executable smoke for the registry-only operator flow."""

from __future__ import annotations

import hashlib
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
from lemma.lean.sandbox import VerifyResult
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport, OperatorRegistryInspectReport
from lemma.task_supply import make_task, write_registry
from lemma.tasks import SourceRef


def _proof_for(theorem_name: str, type_expr: str) -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : {type_expr} := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def _real_task():
    return make_task(
        task_id="lemma.sorrydb.true_intro",
        title="Real true intro",
        theorem_name="real_true_intro",
        type_expr="True",
        source_stream="sorrydb",
        source_name="pytest-sorrydb",
        source_license="Apache-2.0",
        triviality_status="paid_medium",
        metadata={"triviality_checked": True},
    ).model_copy(
        update={
            "difficulty_band": "medium",
            "source_ref": SourceRef(
                kind="sorrydb",
                name="pytest-sorrydb",
                url="https://example.test/repo",
                commit="abc123",
                path="Smoke.lean",
            ),
        }
    )


def _write_registry(tmp_path: Path) -> tuple[Path, str]:
    registry_path = tmp_path / "registry.json"
    write_registry((_real_task(),), registry_path)
    return registry_path, hashlib.sha256(registry_path.read_bytes()).hexdigest()


def test_operator_registry_flow_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    registry_path, registry_sha = _write_registry(tmp_path)
    miner_keypair = Keypair.create_from_uri("//LemmaRegistryFlowMiner")
    task = _real_task()
    proof_script = _proof_for(task.theorem_name, task.type_expr)
    ciphertext = "cipher-registry-flow"
    merkle_root = miner_submission_merkle_root(((0, ciphertext_sha256(ciphertext.encode())),))
    reveal_tempo = 7
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
    bucket_reveals_jsonl = tmp_path / "bucket-reveals.jsonl"
    bucket_reveals_jsonl.write_text(
        MinerBucketReveal(
            tempo=reveal_tempo,
            miner_hotkey=miner_keypair.ss58_address,
            drand_round=10,
            drand_signature="0xsig",
            commit_block=42,
            commit_extrinsic_hash="0xabc",
            merkle_root=merkle_root,
            bucket_url="https://bucket.example/registry-flow",
            blobs=(RevealedBucketBlob(slot_index=0, ciphertext=ciphertext, proof_script=proof_script),),
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("lemma.validator.resolve_active_epoch_randomness", lambda settings, *, tempo: active_randomness)
    monkeypatch.setattr("lemma.validator.current_active_tempo", lambda settings: reveal_tempo + 1)
    monkeypatch.setattr(
        "lemma.chain.commitments.read_all_commitments",
        lambda settings, *, block=None: {
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
        kwargs["decrypt_timelocked"] = lambda ciphertext, signature: proof_script.encode()
        return convert_bucket_reveals(*args, **kwargs)

    monkeypatch.setattr(miner_buckets, "submissions_from_bucket_reveals", fake_bucket_reveals)
    monkeypatch.setattr(
        "lemma.verifiers.lean.run_lean_verify",
        lambda *args, **kwargs: VerifyResult(passed=True, reason="ok", proof_term_hash="term-registry-flow"),
    )

    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_PROTOCOL_MODE": "production",
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
        "LEMMA_VERIFY_REGISTRY_SIGNATURES": "0",
        "LEMMA_REQUIRE_SUBMISSION_SIGNATURES": "1",
        "LEMMA_REQUIRE_COMMIT_REVEAL": "1",
        "LEMMA_REQUIRE_STRONG_PROOF_IDENTITY": "1",
        "LEMMA_ACTIVE_K": "1",
        "LEMMA_FRONTIER_DEPTH": "0",
        "LEMMA_ACTIVE_QUEUE_SEED": "registry-flow",
        "LEMMA_ACTIVE_SEED_MODE": "epoch_randomness",
        "LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE": "chain_drand",
        "LEMMA_ACTIVE_TEMPO_SOURCE": "wall_clock",
        "LEMMA_ACTIVE_TEMPO_SECONDS": "999999999999",
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_USE_DOCKER": "1",
        "LEAN_SANDBOX_NETWORK": "none",
        "BT_WALLET_HOT": "validator-registry-flow",
    }

    preflight = runner.invoke(main, ["operator", "preflight"], env=env)
    assert preflight.exit_code == 1, preflight.output
    preflight_payload = OperatorPreflightReport.model_validate_json(preflight.output)
    checks = {check.name: check for check in preflight_payload.checks}
    assert checks["real_task_supply"].ok is True
    assert checks["registry_signature"].ok is False

    env["LEMMA_PROTOCOL_MODE"] = "dev"
    validate = runner.invoke(
        main,
        [
            "validate",
            "--once",
            "--bucket-reveals-jsonl",
            str(bucket_reveals_jsonl),
            "--validator-hotkey",
            "validator-registry-flow",
            "--no-set-weights",
            "--verify-chain-commitments",
            "--verify-drand-reveals",
        ],
        env=env,
    )
    assert validate.exit_code == 0, validate.output
    payload = json.loads(validate.output)
    assert payload["accepted_unique"] == 1
    assert payload["scores"] == {miner_keypair.ss58_address: 1.0}

    diagnostics_path = tmp_path / "diagnostics.json"
    diagnostics = runner.invoke(main, ["operator", "diagnostics", "--output", str(diagnostics_path)], env=env)
    assert diagnostics.exit_code == 0, diagnostics.output
    report = OperatorDiagnosticsReport.model_validate_json(diagnostics_path.read_text(encoding="utf-8"))
    assert report.registry_inspect is not None
    assert report.registry_inspect.active_task_count == 1


def test_operator_registry_inspect_counts_active_waiting_and_parked(tmp_path: Path) -> None:
    active = _real_task()
    waiting = active.model_copy(update={"id": "lemma.sorrydb.waiting", "theorem_name": "waiting_true"})
    parked = active.model_copy(update={"id": "lemma.sorrydb.parked", "theorem_name": "parked_true", "queue_depth": 2})
    registry_path = tmp_path / "registry.json"
    write_registry((active, waiting, parked), registry_path)
    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()

    result = CliRunner().invoke(
        main,
        ["operator", "registry-inspect"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha,
            "LEMMA_ACTIVE_K": "1",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_ACTIVE_QUEUE_SEED": "registry-inspect",
        },
    )

    assert result.exit_code == 0, result.output
    report = OperatorRegistryInspectReport.model_validate_json(result.output)
    assert report.active_task_count == 1
    assert report.eligible_task_count == 2
    assert report.waiting_task_count == 1
    assert report.parked_task_count == 1

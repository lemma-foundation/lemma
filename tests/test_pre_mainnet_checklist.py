"""Automated coverage for pre-mainnet readiness script checks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lemma.common.config import LemmaSettings
from scripts import pre_mainnet_checklist
from scripts.pre_mainnet_checklist import _read_last_jsonl_row, run_audit


def _settings(tmp_path: Path, **updates: object) -> LemmaSettings:
    base = {
        "_env_file": None,
        "operator_data_dir": tmp_path / "operator",
        "active_registry_cache_dir": tmp_path / "active-registries",
        "chain_commitment_checkpoint_dir": tmp_path / "commitments",
        "protocol_mode": "dev",
    }
    return LemmaSettings(**(base | updates))


def _now_iso8601_z() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _status_by_name(checks: list) -> dict[str, str]:
    return {check.name: check.status for check in checks}


def test_read_last_jsonl_row_returns_last_valid_dict(tmp_path: Path) -> None:
    path = tmp_path / "validator-runs.jsonl"
    path.write_text('{"a": 1}\ninvalid\n{"a": 2}\n', encoding="utf-8")

    assert _read_last_jsonl_row(path) == {"a": 2}


def test_audit_reports_present_hardening_bundle_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path, active_registry_cache_dir=tmp_path / "active-registries")
    (tmp_path / "operator").mkdir()

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)

    assert status["hardening bundle files"] == "pass"


def test_audit_warns_when_hardening_bundle_file_is_missing(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path, active_registry_cache_dir=tmp_path / "active-registries")
    (tmp_path / "operator").mkdir()

    module = pre_mainnet_checklist
    repo_root = module.Path(module.__file__).resolve().parent.parent
    missing_file = repo_root / "lemma" / "cli" / "main.py"
    original_exists = module.Path.exists

    def fake_exists(self: Path) -> bool:
        if self == missing_file:
            return False
        return original_exists(self)

    monkeypatch.setattr(module.Path, "exists", fake_exists)

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        check.detail for check in checks if check.name == "hardening bundle files"
    )

    assert status["hardening bundle files"] == "warn"
    assert "lemma/cli/main.py" in detail


def test_audit_active_registry_role_passes_when_builder_is_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LEMMA_ACTIVE_REGISTRY_ROLE", raising=False)
    settings = _settings(tmp_path)
    (tmp_path / "operator").mkdir()

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)

    assert status["active registry role"] == "pass"


def test_audit_active_registry_role_requires_public_cache_source_for_auditor(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_ROLE", "auditor")
    settings = LemmaSettings(_env_file=None, operator_data_dir=tmp_path / "operator")

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)

    assert status["active registry role"] == "fail"
    assert "auditor role requires public cache source" in next(
        check.detail for check in checks if check.name == "active registry role"
    )


def test_audit_fails_if_commitment_publish_path_missing_for_set_commitment(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        enable_set_commitment=True,
        canonical_publish_ipfs_api_url="",
    )

    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["commitment publication path"] == "fail"
    assert code == 2


def test_audit_requires_production_gates_for_mainnet_mode(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        protocol_mode="production",
        require_submission_signatures=False,
        require_commit_reveal=False,
        require_strong_proof_identity=False,
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )

    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["proof/auth gates"] == "fail"
    assert code == 2


def test_audit_fails_in_production_without_commitment_checkpoint_root(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        protocol_mode="production",
        chain_commitment_checkpoint_dir=None,
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
    )

    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["second-validator commitment parity"] == "fail"
    assert code == 2


def test_audit_reports_stale_work_and_critical_operator_alerts(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()

    run_row = {
        "schema_version": 1,
        "run_at": _now_iso8601_z(),
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 0,
        "accepted_unique_count": 0,
        "bucket_reveals_consumed": 0,
        "corpus_row_count": 0,
        "weights_set": False,
        "chain_commitment_set": False,
        "canonical_publish_uri": "",
        "canonical_publish_count": 0,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    run_line = json.dumps(run_row, separators=(",", ":"))
    (operator_dir / "validator-runs.jsonl").write_text(
        "\n".join([run_line] * 3) + "\n",
        encoding="utf-8",
    )

    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["tempo work evidence: bucket_reveals_consumed"] == "warn"
    assert status["tempo work evidence: verified_count"] == "warn"
    assert status["tempo work evidence: accepted_unique_count"] == "warn"
    assert status["tempo work evidence: corpus_row_count"] == "warn"
    assert status["chain-write evidence"] == "warn"
    assert status["artifact commitment evidence"] == "warn"
    assert status["artifact visibility evidence"] == "warn"
    assert status["operator alerts"] == "fail"
    assert code == 2


def test_audit_checks_chain_write_and_commitment_receipts_for_tempos(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": _now_iso8601_z(),
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "active_tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.8, 0.2],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "active_tempo": 11,
                "extrinsic_hash": "0xdef",
                "payload": "tempo-commitment",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "commitments").mkdir()

    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["chain-write evidence"] == "pass"
    assert status["artifact commitment evidence"] == "pass"
    assert code == 1


def test_audit_fails_chain_write_evidence_on_failed_chain_submission(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": _now_iso8601_z(),
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": False,
                "active_tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.6, 0.4],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "active_tempo": 11,
                "extrinsic_hash": "0xdef",
                "payload": "tempo-commitment",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["chain-write evidence"] == "fail"
    assert status["artifact commitment evidence"] == "pass"
    assert code == 2


def test_audit_matches_string_tempo_fields_for_chain_write_receipts(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": _now_iso8601_z(),
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": "11",
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.5, 0.5],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": "11",
                "extrinsic_hash": "0xdef",
                "payload": "tempo-commitment",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "commitments").mkdir()
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["chain-write evidence"] == "pass"
    assert code == 1


def test_audit_warns_on_missing_chain_weight_payload_fields(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": _now_iso8601_z(),
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": False,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(check.detail for check in checks if check.name == "chain-write evidence")

    assert status["chain-write evidence"] == "warn"
    assert "missing resolved weights" in detail
    assert code == 1


def test_audit_matches_string_active_tempo_in_validator_runs(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": _now_iso8601_z(),
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": "11",
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "active_tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.5, 0.5],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "active_tempo": 11,
                "extrinsic_hash": "0xdef",
                "payload": "tempo-commitment",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "commitments").mkdir()
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["chain-write evidence"] == "pass"
    assert code == 1


def test_audit_warns_on_chain_weight_payload_length_mismatch(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": False,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.4],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(check.detail for check in checks if check.name == "chain-write evidence")

    assert status["chain-write evidence"] == "warn"
    assert "uids/weights lengths" in detail
    assert code == 1


def test_audit_warns_when_commitment_readback_mismatch(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.5, 0.5],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "payload": "tempo-commitment",
                "readback_matches": False,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "commitments").mkdir()

    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        check.detail
        for check in checks
        if check.name == "artifact commitment evidence"
    )

    assert status["artifact commitment evidence"] == "warn"
    assert "did not match on-chain readback" in detail
    assert code == 1


def test_audit_warns_when_tempo_commitment_payload_is_missing(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": False,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "payload": "tempo-commitment",
                "readback_matches": True,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "commitments").mkdir()

    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["tempo commitment payload"] == "warn"
    assert (
        "latest run expected chain commitment but tempo_commitment_payload is missing"
        in next(
            check.detail
            for check in checks
            if check.name == "tempo commitment payload"
        )
    )
    assert code == 1


def test_audit_warns_on_weight_oscillation(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": False,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.2, 0.8],
            },
            separators=(",", ":"),
        )
        + "\n"
        + json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xdef",
                "uids": [1, 3],
                "weights": [0.6, 0.4],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        check.detail
        for check in checks
        if check.name == "chain-write evidence"
    )

    assert status["chain-write evidence"] == "warn"
    assert "oscillation" in detail.lower()
    assert code == 1


def test_audit_warns_on_weight_oscillation_with_same_uids_and_different_weights(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": True,
        "chain_commitment_set": False,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.2, 0.8],
            },
            separators=(",", ":"),
        )
        + "\n"
        + json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xdef",
                "uids": [1, 2],
                "weights": [0.3, 0.7],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["chain-write evidence"] == "warn"
    assert code == 1


def test_audit_passes_artifact_visibility_with_ipfs_canonical_publish_only(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "",
        "canonical_publish_count": 2,
        "weights_set": True,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.5, 0.5],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xdef",
                "payload": "tempo-commitment",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "canonical-publish.jsonl").write_text(
        json.dumps(
            {
                "kind": "ipfs_directory",
                "local_path": "tempo-000011",
                "cid": "bafy...placeholder",
                "file_count": "7",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        canonical_publish_ipfs_api_url="http://ipfs.local:5001",
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["artifact visibility evidence"] == "pass"
    assert status["artifact commitment evidence"] == "pass"
    assert status["chain-write evidence"] == "pass"
    assert status["canonical publish staging errors"] == "pass"
    assert "canonical publish staging errors" in status
    assert code == 1


def test_audit_warns_on_canonical_publish_error_rows(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "",
        "canonical_publish_count": 2,
        "weights_set": True,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.5, 0.5],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xdef",
                "payload": "tempo-commitment",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (operator_dir / "canonical-publish.jsonl").write_text(
        json.dumps(
            {
                "kind": "s3_publish_error",
                "error": "permission denied for object",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        canonical_publish_ipfs_api_url="http://ipfs.local:5001",
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["canonical publish staging errors"] == "warn"
    assert status["artifact commitment evidence"] == "pass"
    assert "permission denied" in next(
        check.detail
        for check in checks
        if check.name == "canonical publish staging errors"
    )
    assert code == 1


def test_audit_warns_when_canonical_publish_records_missing_with_nonzero_publish_count(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "",
        "canonical_publish_count": 2,
        "weights_set": True,
        "chain_commitment_set": False,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "weight-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "uids": [1, 2],
                "weights": [0.5, 0.5],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        canonical_publish_ipfs_api_url="http://ipfs.local:5001",
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["canonical publish staging errors"] == "warn"
    assert "canonical publish count is nonzero but no recent canonical-publish log file was found" in next(
        check.detail
        for check in checks
        if check.name == "canonical publish staging errors"
    )
    assert code == 1


def test_audit_fails_chain_commitment_gate_when_no_publish_records_exist(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "",
        "canonical_publish_count": 0,
        "weights_set": False,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "tempo-commitment",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xdef",
                "payload": "tempo-commitment",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        canonical_publish_ipfs_api_url="http://ipfs.local:5001",
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["artifact commitment evidence"] == "pass"
    assert status["commitment publication gate"] == "fail"
    assert "chain commitment recorded without canonical publish records" in next(
        check.detail
        for check in checks
        if check.name == "commitment publication gate"
    )
    assert code == 2


def test_audit_warns_on_commitment_oscillation(tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": "2025-01-01T00:00:00Z",
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "s3://example",
        "canonical_publish_count": 1,
        "weights_set": False,
        "chain_commitment_set": True,
        "tempo_commitment_payload": "payload-1",
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (operator_dir / "commitment-submissions.jsonl").write_text(
        json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xabc",
                "payload": "payload-1",
            },
            separators=(",", ":"),
        )
        + "\n"
        + json.dumps(
            {
                "success": True,
                "tempo": 11,
                "extrinsic_hash": "0xdef",
                "payload": "payload-2",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )
    checks, code = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        check.detail
        for check in checks
        if check.name == "artifact commitment evidence"
    )

    assert status["artifact commitment evidence"] == "warn"
    assert "oscillation" in detail.lower()
    assert code == 1


def test_audit_skips_checkpoint_parity_without_history_in_dev_mode(tmp_path: Path, monkeypatch: object) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    run_row = {
        "schema_version": 1,
        "run_at": _now_iso8601_z(),
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": 11,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "weights_set": False,
        "chain_commitment_set": False,
        "canonical_publish_uri": "",
        "canonical_publish_count": 0,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }
    (operator_dir / "validator-runs.jsonl").write_text(
        json.dumps(run_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        protocol_mode="dev",
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )
    (tmp_path / "commitments").mkdir()
    monkeypatch.delenv("LEMMA_HISTORY_BLOCK", raising=False)

    checks, code = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        (
        check.detail
        for check in reversed(checks)
        if check.name == "second-validator commitment parity"
        ),
        "second-validator commitment parity missing",
    )

    assert status["second-validator commitment parity"] == "skip"
    assert "LEMMA_HISTORY_BLOCK is not set" in detail
    assert code == 1


def test_audit_fails_checkpoint_parity_when_history_block_missing_in_production(
    tmp_path: Path, monkeypatch: object
) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    settings = _settings(
        tmp_path,
        protocol_mode="production",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        enable_set_commitment=False,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )
    (tmp_path / "commitments").mkdir()
    monkeypatch.delenv("LEMMA_HISTORY_BLOCK", raising=False)
    monkeypatch.setattr(
        pre_mainnet_checklist,
        "_operator_alert_checks",
        lambda checks, operator_data_dir: checks.append(
            pre_mainnet_checklist.AuditCheck(
                name="operator alerts",
                status="pass",
                detail="stubbed for test",
            )
        ),
    )

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        (
        check.detail
        for check in reversed(checks)
        if check.name == "second-validator commitment parity"
        ),
        "second-validator commitment parity missing",
    )

    assert status["second-validator commitment parity"] == "fail"
    assert "LEMMA_HISTORY_BLOCK is not set" in detail


def test_audit_fails_if_checkpoint_dir_missing_in_production(tmp_path: Path, monkeypatch: object) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    settings = _settings(
        tmp_path,
        protocol_mode="production",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        enable_set_commitment=False,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )
    monkeypatch.setenv("LEMMA_HISTORY_BLOCK", "123")
    monkeypatch.setattr(
        pre_mainnet_checklist,
        "_operator_alert_checks",
        lambda checks, operator_data_dir: checks.append(
            pre_mainnet_checklist.AuditCheck(
                name="operator alerts",
                status="pass",
                detail="stubbed for test",
            )
        ),
    )

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        (
        check.detail
        for check in reversed(checks)
        if check.name == "second-validator commitment parity"
        ),
        "second-validator commitment parity missing",
    )

    assert status["second-validator commitment parity"] == "fail"
    assert "checkpoint directory does not exist yet" in detail


def test_audit_fails_checkpoint_parity_when_readback_raises_in_production(tmp_path: Path, monkeypatch: object) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    settings = _settings(
        tmp_path,
        protocol_mode="production",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )
    (tmp_path / "commitments").mkdir()
    monkeypatch.setenv("LEMMA_HISTORY_BLOCK", "123456")

    import lemma.chain.commitments

    monkeypatch.setattr(
        lemma.chain.commitments,
        "read_all_commitments",
        lambda _settings, block: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        pre_mainnet_checklist,
        "_operator_alert_checks",
        lambda checks, operator_data_dir: checks.append(
            pre_mainnet_checklist.AuditCheck(
                name="operator alerts",
                status="pass",
                detail="stubbed for test",
            )
        ),
    )

    checks, code = run_audit(settings)
    status = _status_by_name(checks)
    detail = next(
        (
        check.detail
        for check in reversed(checks)
        if check.name == "second-validator commitment parity"
        ),
        "second-validator commitment parity missing",
    )

    assert status["second-validator commitment parity"] == "fail"
    assert "readback failed at block 123456" in detail
    assert code == 2


def test_audit_reports_privacy_hygiene_failures(monkeypatch, tmp_path: Path) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    settings = _settings(
        tmp_path,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
    )

    def _fake_check_repo(_path: Path) -> list[str]:
        return ["operator/.env:agent-state"]

    monkeypatch.setattr(pre_mainnet_checklist.leak_check, "check_repo", _fake_check_repo)
    checks, _ = run_audit(settings)
    status = _status_by_name(checks)

    assert status["privacy hygiene check"] == "fail"


def _burn_row(*, run_at: str, active_tempo: int = 11) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_at": run_at,
        "registry_sha256": "0" * 64,
        "active_K": 2,
        "frontier_depth": 0,
        "active_tempo": active_tempo,
        "active_seed_mode": "static",
        "active_epoch_randomness_source": "manual",
        "active_selection_seed_sha256": "0" * 64,
        "active_epoch_randomness_sha256": "0" * 64,
        "verified_count": 1,
        "accepted_unique_count": 1,
        "bucket_reveals_consumed": 1,
        "corpus_row_count": 2,
        "canonical_publish_uri": "",
        "canonical_publish_count": 1,
        "weights_set": False,
        "chain_commitment_set": False,
        "unearned_share": 0.0,
        "unearned_policy": "burn",
    }


def test_audit_reports_burn_in_failures_when_history_is_short_in_production(
    tmp_path: Path, monkeypatch: object
) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    now = datetime.now(tz=UTC).replace(microsecond=0, tzinfo=None)
    rows = [
        _burn_row(run_at=(now.isoformat() + "Z"), active_tempo=100),
        _burn_row(
            run_at=((now + timedelta(hours=1)).isoformat() + "Z"),
            active_tempo=101,
        ),
    ]
    (operator_dir / "validator-runs.jsonl").write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    settings = _settings(
        tmp_path,
        protocol_mode="production",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )
    (tmp_path / "commitments").mkdir()
    monkeypatch.setenv("LEMMA_HISTORY_BLOCK", "123")
    monkeypatch.setattr(
        pre_mainnet_checklist,
        "_operator_alert_checks",
        lambda checks, operator_data_dir: checks.append(
            pre_mainnet_checklist.AuditCheck(
                name="operator alerts",
                status="pass",
                detail="stubbed for test",
            )
        ),
    )

    checks, code = run_audit(settings)
    status = _status_by_name(checks)

    assert status["burn-in continuity: 72h closed window"] == "fail"
    assert status["burn-in continuity: 7d public window"] == "fail"
    assert status["burn-in continuity: tempo progression"] == "pass"
    assert status["burn-in continuity: work progress"] == "pass"
    assert code == 2


def test_audit_reports_burn_in_checks_and_tempo_regressions(tmp_path: Path, monkeypatch: object) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    now = datetime.now(tz=UTC).replace(microsecond=0, tzinfo=None)
    rows = [
        _burn_row(
            run_at=(now.isoformat() + "Z"),
            active_tempo=11,
        ),
        _burn_row(
            run_at=((now + timedelta(hours=1, minutes=1)).isoformat() + "Z"),
            active_tempo=10,
        ),
        _burn_row(
            run_at=((now + timedelta(hours=2, minutes=2)).isoformat() + "Z"),
            active_tempo=12,
        ),
    ]
    (operator_dir / "validator-runs.jsonl").write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    settings = _settings(
        tmp_path,
        protocol_mode="production",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )
    (tmp_path / "commitments").mkdir()
    monkeypatch.setenv("LEMMA_HISTORY_BLOCK", "123")
    monkeypatch.setattr(
        pre_mainnet_checklist,
        "_operator_alert_checks",
        lambda checks, operator_data_dir: checks.append(
            pre_mainnet_checklist.AuditCheck(
                name="operator alerts",
                status="pass",
                detail="stubbed for test",
            )
        ),
    )

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)

    assert status["burn-in continuity: 72h closed window"] == "fail"
    assert status["burn-in continuity: 7d public window"] == "fail"
    assert status["burn-in continuity: tempo progression"] == "fail"
    assert status["burn-in continuity: work progress"] == "pass"


def test_audit_reports_production_burn_in_failures_with_single_tempo_sample(
    tmp_path: Path, monkeypatch: object
) -> None:
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    rows = [
        _burn_row(
            run_at=(datetime.now(tz=UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"),
            active_tempo=11,
        )
    ]
    (operator_dir / "validator-runs.jsonl").write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    settings = _settings(
        tmp_path,
        protocol_mode="production",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        operator_data_dir=operator_dir,
        active_registry_cache_dir=tmp_path / "active-registries",
        chain_commitment_checkpoint_dir=tmp_path / "commitments",
    )
    (tmp_path / "commitments").mkdir()
    monkeypatch.setenv("LEMMA_HISTORY_BLOCK", "123")
    monkeypatch.setattr(
        pre_mainnet_checklist,
        "_operator_alert_checks",
        lambda checks, operator_data_dir: checks.append(
            pre_mainnet_checklist.AuditCheck(
                name="operator alerts",
                status="pass",
                detail="stubbed for test",
            )
        ),
    )

    checks, _ = run_audit(settings)
    status = _status_by_name(checks)

    assert status["burn-in continuity: 72h closed window"] == "fail"
    assert status["burn-in continuity: 7d public window"] == "fail"
    assert status["burn-in continuity: tempo progression"] == "fail"
    assert status["burn-in continuity: work progress"] == "fail"

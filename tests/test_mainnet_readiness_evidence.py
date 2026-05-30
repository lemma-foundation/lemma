"""Tests for host/manual pre-mainnet evidence snapshot generation."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.mainnet_readiness_evidence import run_audit


def _commands_by_item(payload: dict[str, object], *, item: str) -> list[str]:
    return [
        record["command"]
        for record in payload["records"]
        if record["checklist_item"] == item
    ]


def test_mainnet_readiness_evidence_writes_skip_records(tmp_path: Path) -> None:
    output = tmp_path / "artifacts" / "rollout" / "mainnet-readiness-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    code = run_audit(
        repo_root=repo_root,
        output=output,
        execute=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert code == 0
    assert payload["repo"] == str(repo_root)
    assert payload["exit_status"] == "pass"
    assert payload["execute"] is False
    assert payload["records"], "expected snapshot records for checklist items"
    assert all(item["status"] == "skip" for item in payload["records"])
    assert output.parent.exists()
    assert {
        item["checklist_item"]
        for item in payload["records"]
    } == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16"}


def test_mainnet_readiness_evidence_collects_rollout_parity_commands(tmp_path: Path) -> None:
    output = tmp_path / "rollout-parity-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item1 = _commands_by_item(payload, item="1")

    assert any("git -C . rev-parse HEAD" in command for command in item1)
    assert any("LEMMA_GIT_SHA" in command for command in item1)
    assert any("lemma-publisher" in command for command in item1)


def test_mainnet_readiness_evidence_collects_second_validator_commands(tmp_path: Path) -> None:
    output = tmp_path / "second-validator-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item3 = _commands_by_item(payload, item="3")
    item4 = _commands_by_item(payload, item="4")

    assert any("validator_service_count=" in command for command in item3)
    assert any("LEMMA_OPERATOR_DATA_DIR" in command for command in item4)


def test_mainnet_readiness_evidence_collects_observability_restart_checks(tmp_path: Path) -> None:
    output = tmp_path / "observability-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item12 = _commands_by_item(payload, item="12")

    assert any("list-units --state=failed" in command for command in item12)
    assert any("journalctl -u lemma-validator-bucket.service" in command for command in item12)


def test_mainnet_readiness_evidence_collects_role_semantics_commands(tmp_path: Path) -> None:
    output = tmp_path / "role-semantics-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item2 = _commands_by_item(payload, item="2")

    assert any("LEMMA_ACTIVE_REGISTRY_ROLE" in command for command in item2)
    assert any("result=pass" in command for command in item2)


def test_mainnet_readiness_evidence_collects_work_progress_threshold_commands(tmp_path: Path) -> None:
    output = tmp_path / "work-progress-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item8 = _commands_by_item(payload, item="8")

    assert any("bucket_reveals_consumed" in command for command in item8)
    assert any("verified_count" in command for command in item8)
    assert any("accepted_unique_count" in command for command in item8)
    assert any("corpus_row_count" in command for command in item8)


def test_mainnet_readiness_evidence_collects_chain_write_validation_commands(tmp_path: Path) -> None:
    output = tmp_path / "chain-write-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item9 = _commands_by_item(payload, item="9")

    assert any("weight-submissions.jsonl" in command for command in item9)
    assert any("latest_weight_payload_signature" in command for command in item9)
    assert not any("latest weight submission did not report success" in command for command in item9)


def test_mainnet_readiness_evidence_collects_commitment_validation_commands(tmp_path: Path) -> None:
    output = tmp_path / "commitment-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item10 = _commands_by_item(payload, item="10")

    assert any("commitment-submissions.jsonl" in command for command in item10)
    assert any("latest_commitment_payload=" in command for command in item10)
    assert any("latest_commitment_readback=" in command for command in item10)


def test_mainnet_readiness_evidence_collects_runbook_commands(tmp_path: Path) -> None:
    output = tmp_path / "runbook-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    item13 = _commands_by_item(payload, item="13")

    assert any("role flip" in command for command in item13)
    assert any(
        'rg -n "preflight|operator alerts|active registry|burn-in|mainnet"' in command
        for command in item13
    )


def test_mainnet_readiness_evidence_command_matrix_covers_all_items(tmp_path: Path) -> None:
    output = tmp_path / "full-checklist-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    expected_markers = {
        "1": ["git -C . rev-parse HEAD", "lemma-publisher", "LEMMA_GIT_SHA"],
        "2": ["LEMMA_ACTIVE_REGISTRY_ROLE", "result=pass"],
        "3": ["validator_service_count=", "LEMMA_ACTIVE_REGISTRY_ROLE"],
        "4": ["LEMMA_OPERATOR_DATA_DIR"],
        "5": ["scripts/lemma-sync-active-registry-cache", "scripts/publish_corpus_snapshot.py"],
        "6": [
            "test_validator_does_not_submit_commitment_after_ipfs_publish_failure",
            "test_validator_does_not_submit_commitment_after_s3_publish_failure",
        ],
        "7": [
            "publish_corpus_snapshot.py --repo",
            "publish_chain_commitment.py --repo",
            "directory_digest",
        ],
        "8": ["bucket_reveals_consumed", "verified_count", "accepted_unique_count", "corpus_row_count"],
        "9": ["weight-submissions.jsonl", "latest_weight_payload_signature"],
        "10": [
            "commitment-submissions.jsonl",
            "latest_commitment_payload",
            "latest_commitment_readback=",
        ],
        "11": ["LEMMA_HISTORY_BLOCK", "LEMMA_CHAIN_COMMITMENT_CHECKPOINT_DIR", "historical_commitments="],
        "12": [
            "operator alerts --recent-runs",
            "list-units --state=failed",
            "journalctl -u lemma-validator-bucket.service",
        ],
        "13": ["docs/operator-registry-flow.md", "rg -n \"role flip"],
        "14": ["scripts/leak_check.py"],
        "15": ["run_count=", "duration_hours", "zero_accept_rows="],
        "16": ["workstream_audit.py --profile mainnet", "pre_mainnet_checklist.py --json"],
    }

    for item, markers in expected_markers.items():
        item_commands = _commands_by_item(payload, item=item)
        for marker in markers:
            assert any(marker in command for command in item_commands), f"item {item} missing marker {marker}"


def test_mainnet_readiness_evidence_skips_workstream_pip_audit_when_offline(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "offline-workstream-audit-evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr("scripts.mainnet_readiness_evidence._pypi_reachable", lambda: False)

    run_audit(repo_root=repo_root, output=output, execute=False)
    payload = json.loads(output.read_text(encoding="utf-8"))
    item16 = _commands_by_item(payload, item="16")

    assert any("--skip-pip-audit" in command for command in item16)
    assert not any(command.count("--skip-pip-audit") > 1 for command in item16)

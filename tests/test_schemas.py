"""Machine contract sanity checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport, OperatorRegistryInspectReport
from pydantic import ValidationError


def _schema(name: str) -> dict[str, object]:
    return json.loads(Path("spec", name).read_text(encoding="utf-8"))


def test_task_schema_requires_source_and_version() -> None:
    required = set(_schema("task.schema.json")["required"])
    props = set(_schema("task.schema.json")["properties"])

    assert {"task_version", "source_ref", "source_license", "source_stream"} <= required
    assert {"queue_position", "queue_depth", "frontier_depth", "triviality_status"} <= props


def test_submission_schema_requires_live_signature_fields() -> None:
    required = set(_schema("submission.schema.json")["required"])

    assert {"task_version", "signature", "signature_payload_sha256"} <= required


def test_corpus_schema_requires_identity_attribution_and_reward_status() -> None:
    required = set(_schema("corpus-row.schema.json")["required"])
    props = set(_schema("corpus-row.schema.json")["properties"])

    assert {
        "row_id",
        "task_version",
        "source_ref",
        "source_license",
        "validator_hotkey",
        "accepted_at",
        "rewarded",
        "proof_identity",
        "proof_identity_source",
    } <= required
    assert {"active_K", "queue_position", "queue_depth", "frontier_depth", "ema_solve_rate"} <= props


def test_verification_result_schema_captures_replay_identity() -> None:
    required = set(_schema("verification-result.schema.json")["required"])

    assert {
        "task_id",
        "task_version",
        "target_sha256",
        "solver_hotkey",
        "validator_hotkey",
        "passed",
        "reason",
        "proof_sha256",
        "proof_term_hash",
        "proof_identity_source",
        "verifier_version",
    } <= required


def test_score_event_schema_captures_v1_score_rule() -> None:
    required = set(_schema("score-event.schema.json")["required"])

    assert {
        "task_id",
        "task_version",
        "proof_identity",
        "proof_identity_source",
        "rewarded",
        "credit",
        "score",
        "active_K",
    } <= required


def test_operator_preflight_report_contract() -> None:
    schema = OperatorPreflightReport.model_json_schema()
    required = set(schema["required"])
    props = set(schema["properties"])
    check_schema = schema["$defs"]["OperatorPreflightCheck"]

    assert {"schema_version", "ok", "registry_sha256", "active_K", "frontier_depth", "checks"} <= required
    assert schema["additionalProperties"] is False
    assert {"schema_version", "registry_sha256", "active_K", "frontier_depth"} <= props
    assert {"name", "ok", "detail"} <= set(check_schema["required"])
    assert check_schema["additionalProperties"] is False


def test_operator_preflight_report_rejects_mismatched_ok() -> None:
    with pytest.raises(ValidationError):
        OperatorPreflightReport.model_validate(
            {
                "schema_version": 1,
                "ok": True,
                "registry_sha256": None,
                "active_K": 1,
                "frontier_depth": 0,
                "checks": [{"name": "registry_hash_pin", "ok": False, "detail": "missing"}],
            }
        )


def test_operator_diagnostics_report_contract() -> None:
    schema = OperatorDiagnosticsReport.model_json_schema()
    required = set(schema["required"])

    assert {
        "schema_version",
        "preflight",
        "registry_sha256",
        "active_K",
        "frontier_depth",
        "active_task_ids",
    } <= required
    assert schema["additionalProperties"] is False


def test_operator_registry_inspect_report_contract() -> None:
    schema = OperatorRegistryInspectReport.model_json_schema()
    required = set(schema["required"])

    assert {
        "schema_version",
        "registry_sha256",
        "total_task_count",
        "active_K",
        "frontier_depth",
        "active_task_count",
        "eligible_task_count",
        "waiting_task_count",
        "parked_task_count",
        "max_queue_depth",
        "queue_depth_counts",
    } <= required
    assert schema["additionalProperties"] is False

"""Machine contract sanity checks."""

from __future__ import annotations

import json
from pathlib import Path


def _schema(name: str) -> dict[str, object]:
    return json.loads(Path("spec", name).read_text(encoding="utf-8"))


def test_task_schema_requires_source_and_version() -> None:
    required = set(_schema("task.schema.json")["required"])

    assert {"task_version", "source_ref", "source_license", "source_stream"} <= required


def test_submission_schema_requires_live_signature_fields() -> None:
    required = set(_schema("submission.schema.json")["required"])

    assert {"task_version", "signature", "signature_payload_sha256"} <= required


def test_corpus_schema_requires_identity_attribution_and_reward_status() -> None:
    required = set(_schema("corpus-row.schema.json")["required"])

    assert {
        "row_id",
        "task_version",
        "source_ref",
        "source_license",
        "validator_hotkey",
        "accepted_at",
        "rewarded",
    } <= required

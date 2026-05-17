"""Wire payloads shared by miners and validators."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lemma.submissions import LemmaSubmission
from lemma.tasks import LemmaTask


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class TaskRequest(BaseModel):
    """Validator request for proofs over a bounded task batch."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    validator_hotkey: str
    epoch: int | None = None
    tasks: tuple[LemmaTask, ...]
    signature: str | None = None

    def signing_payload(self) -> str:
        return canonical_json(
            {
                "schema_version": self.schema_version,
                "validator_hotkey": self.validator_hotkey,
                "epoch": self.epoch,
                "task_ids": [task.id for task in self.tasks],
                "target_sha256": [task.target_sha256 for task in self.tasks],
            }
        )


class ProofResponse(BaseModel):
    """Miner response containing task-bound proof packages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    miner_hotkey: str
    submissions: tuple[LemmaSubmission, ...] = Field(default_factory=tuple)
    signature: str | None = None

    def signing_payload(self) -> str:
        return canonical_json(
            {
                "schema_version": self.schema_version,
                "miner_hotkey": self.miner_hotkey,
                "submission_payloads": [submission.signature_payload_sha256 for submission in self.submissions],
            }
        )

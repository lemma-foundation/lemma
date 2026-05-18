"""Commitment envelopes for future timelocked proof reveals."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field


class CommitmentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    task_version: int = Field(default=1, ge=1)
    target_sha256: str
    miner_hotkey: str
    drand_round: int = Field(ge=0)
    ciphertext_sha256: str
    commit_block: int = Field(ge=0)
    extrinsic_hash: str

    def rank_key(self, tie_break_seed: str) -> tuple[int, str]:
        digest = hashlib.sha256(f"{tie_break_seed}:{self.extrinsic_hash}".encode()).hexdigest()
        return self.commit_block, digest

    def signing_payload(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))


def ciphertext_sha256(ciphertext: bytes) -> str:
    return hashlib.sha256(ciphertext).hexdigest()

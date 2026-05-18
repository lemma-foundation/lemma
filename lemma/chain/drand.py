"""Drand timelock planning types.

This module deliberately does not perform network encryption/decryption. It
keeps the future chain interface typed without putting drand in the validator
critical path.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DrandRevealPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_round: int = Field(ge=0)
    reveal_round: int = Field(ge=0)
    chain_hash: str = ""

    @property
    def ready(self) -> bool:
        return self.current_round >= self.reveal_round

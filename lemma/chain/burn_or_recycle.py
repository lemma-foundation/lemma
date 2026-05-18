"""Unearned allocation policy rails."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lemma.scoring import UnearnedPolicy


class UnearnedAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: UnearnedPolicy = "burn"
    share: float = Field(ge=0.0, le=1.0)
    uid: int | None = Field(default=0, ge=0)

    @property
    def chain_label(self) -> str:
        if self.uid is None:
            return self.policy
        return f"{self.policy}_uid:{self.uid}"

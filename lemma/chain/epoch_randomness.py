"""Epoch randomness derived from Bittensor tempo-boundary block hashes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings


class _SubnetHyperparameters(Protocol):
    tempo: int


class _Subtensor(Protocol):
    def get_current_block(self) -> int: ...

    def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> object: ...

    def get_block_hash(self, block: int | None = None) -> str: ...


class EpochRandomness(BaseModel):
    """Public inputs every validator must agree on for one active epoch."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source: Literal["chain_block_hash"] = "chain_block_hash"
    netuid: int = Field(ge=0)
    tempo: int = Field(ge=0)
    tempo_length: int = Field(gt=0)
    anchor_block: int = Field(ge=0)
    anchor_block_hash: str

    def seed_material(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))


def resolve_chain_block_epoch_randomness(
    settings: LemmaSettings,
    *,
    tempo: int | None = None,
    subtensor: _Subtensor | None = None,
) -> EpochRandomness:
    """Resolve block-hash randomness for one active tempo."""
    if subtensor is None:
        from lemma.chain.subtensor import connect_subtensor

        subtensor = connect_subtensor(settings)

    current_block = int(subtensor.get_current_block())
    hyperparams = cast(
        _SubnetHyperparameters,
        subtensor.get_subnet_hyperparameters(settings.netuid, block=current_block),
    )
    tempo_length = int(hyperparams.tempo)
    if tempo_length <= 0:
        raise RuntimeError("chain tempo must be positive")
    active_tempo = current_block // tempo_length if tempo is None else tempo
    anchor_block = active_tempo * tempo_length
    if anchor_block > current_block:
        raise RuntimeError("cannot resolve future epoch randomness")

    anchor_block_hash = str(subtensor.get_block_hash(anchor_block))
    return EpochRandomness(
        netuid=settings.netuid,
        tempo=active_tempo,
        tempo_length=tempo_length,
        anchor_block=anchor_block,
        anchor_block_hash=anchor_block_hash,
    )

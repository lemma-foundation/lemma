"""Epoch randomness derived from chain tempo boundaries and Drand."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings


DRAND_QUICKNET_CHAIN_HASH = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
DRAND_QUICKNET_GENESIS_TIME = 1_692_803_367
DRAND_QUICKNET_PERIOD_SECONDS = 3


class _SubnetHyperparameters(Protocol):
    tempo: int


class _BlockInfo(Protocol):
    timestamp: int | None


class _Subtensor(Protocol):
    def get_current_block(self) -> int: ...

    def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> object: ...

    def get_block_hash(self, block: int | None = None) -> str: ...

    def get_block_info(self, block: int | None = None, block_hash: str | None = None) -> object | None: ...


class EpochRandomness(BaseModel):
    """Public inputs every validator must agree on for one active epoch."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source: Literal["chain_drand"] = "chain_drand"
    netuid: int = Field(ge=0)
    tempo: int = Field(ge=0)
    tempo_length: int = Field(gt=0)
    anchor_block: int = Field(ge=0)
    anchor_block_hash: str
    anchor_block_timestamp: int = Field(ge=0)
    drand_chain_hash: str = DRAND_QUICKNET_CHAIN_HASH
    drand_round: int = Field(ge=1)
    drand_signature: str

    def seed_material(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))


def drand_round_for_timestamp(timestamp: int) -> int:
    """Return the Drand Quicknet round first available at a Unix timestamp."""
    if timestamp < DRAND_QUICKNET_GENESIS_TIME:
        raise ValueError("timestamp predates Drand Quicknet genesis")
    return ((timestamp - DRAND_QUICKNET_GENESIS_TIME) // DRAND_QUICKNET_PERIOD_SECONDS) + 1


def resolve_chain_drand_epoch_randomness(
    settings: LemmaSettings,
    *,
    tempo: int | None = None,
    subtensor: _Subtensor | None = None,
    drand_signature_for_round: Callable[[int], str] | None = None,
) -> EpochRandomness:
    """Resolve the chain/drand randomness for one active tempo."""
    if subtensor is None:
        import bittensor as bt

        subtensor = bt.Subtensor(network=settings.bt_network or None)
    if drand_signature_for_round is None:
        import bittensor_drand

        drand_signature_for_round = bittensor_drand.get_signature_for_round

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
    block_info = cast(_BlockInfo | None, subtensor.get_block_info(block=anchor_block))
    if block_info is None or block_info.timestamp is None:
        raise RuntimeError("anchor block timestamp is unavailable")
    anchor_timestamp = int(block_info.timestamp)
    drand_round = drand_round_for_timestamp(anchor_timestamp)
    signature = drand_signature_for_round(drand_round)

    return EpochRandomness(
        netuid=settings.netuid,
        tempo=active_tempo,
        tempo_length=tempo_length,
        anchor_block=anchor_block,
        anchor_block_hash=anchor_block_hash,
        anchor_block_timestamp=anchor_timestamp,
        drand_round=drand_round,
        drand_signature=str(signature),
    )

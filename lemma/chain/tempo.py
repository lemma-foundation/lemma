"""Subnet tempo reads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings


class _SubnetHyperparameters(Protocol):
    tempo: int


class _TempoSubtensor(Protocol):
    def get_current_block(self) -> int: ...

    def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> object: ...


def current_chain_tempo_blocks(settings: LemmaSettings, *, subtensor: _TempoSubtensor | None = None) -> int:
    """Return the current subnet tempo length in blocks."""
    if subtensor is None:
        import bittensor as bt

        subtensor = bt.Subtensor(network=settings.bt_network or None)
    block = int(subtensor.get_current_block())
    hyperparams = cast(_SubnetHyperparameters, subtensor.get_subnet_hyperparameters(settings.netuid, block=block))
    tempo = int(hyperparams.tempo)
    if tempo <= 0:
        raise RuntimeError("chain tempo must be positive")
    return tempo

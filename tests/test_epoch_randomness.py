from __future__ import annotations

from lemma.chain.epoch_randomness import (
    DRAND_QUICKNET_GENESIS_TIME,
    drand_round_for_timestamp,
    resolve_chain_drand_epoch_randomness,
)
from lemma.common.config import LemmaSettings


def test_drand_round_for_timestamp_uses_quicknet_period() -> None:
    assert drand_round_for_timestamp(DRAND_QUICKNET_GENESIS_TIME) == 1
    assert drand_round_for_timestamp(DRAND_QUICKNET_GENESIS_TIME + 2) == 1
    assert drand_round_for_timestamp(DRAND_QUICKNET_GENESIS_TIME + 3) == 2


def test_chain_drand_epoch_randomness_anchors_to_tempo_boundary() -> None:
    class Hyperparams:
        tempo = 360

    class BlockInfo:
        timestamp = DRAND_QUICKNET_GENESIS_TIME + 30

    class Subtensor:
        def get_current_block(self) -> int:
            return 725

        def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> Hyperparams:
            assert netuid == 467
            assert block == 725
            return Hyperparams()

        def get_block_hash(self, block: int | None = None) -> str:
            assert block == 720
            return "0xanchor"

        def get_block_info(self, block: int | None = None, block_hash: str | None = None) -> BlockInfo:
            assert block == 720
            assert block_hash is None
            return BlockInfo()

    def signature(round_no: int) -> str:
        assert round_no == 11
        return "0xsig"

    randomness = resolve_chain_drand_epoch_randomness(
        LemmaSettings(_env_file=None, netuid=467),
        subtensor=Subtensor(),
        drand_signature_for_round=signature,
    )

    assert randomness.tempo == 2
    assert randomness.anchor_block == 720
    assert randomness.anchor_block_hash == "0xanchor"
    assert randomness.drand_round == 11
    assert randomness.drand_signature == "0xsig"
    assert randomness.seed_material() == randomness.seed_material()

from __future__ import annotations

from lemma.chain.epoch_randomness import resolve_chain_block_epoch_randomness
from lemma.common.config import LemmaSettings


def test_chain_block_epoch_randomness_anchors_to_tempo_boundary() -> None:
    class Hyperparams:
        tempo = 360

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

    randomness = resolve_chain_block_epoch_randomness(
        LemmaSettings(_env_file=None, netuid=467),
        subtensor=Subtensor(),
    )

    assert randomness.source == "chain_block_hash"
    assert randomness.tempo == 2
    assert randomness.anchor_block == 720
    assert randomness.anchor_block_hash == "0xanchor"
    assert randomness.seed_material() == randomness.seed_material()


def test_chain_block_epoch_randomness_rejects_future_tempo() -> None:
    class Hyperparams:
        tempo = 360

    class Subtensor:
        def get_current_block(self) -> int:
            return 725

        def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> Hyperparams:
            return Hyperparams()

        def get_block_hash(self, block: int | None = None) -> str:
            return "0xanchor"

    try:
        resolve_chain_block_epoch_randomness(LemmaSettings(_env_file=None), tempo=3, subtensor=Subtensor())
    except RuntimeError as e:
        assert "future epoch randomness" in str(e)
    else:
        raise AssertionError("future tempo should fail closed")

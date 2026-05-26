from __future__ import annotations

from lemma.chain.epoch_randomness import (
    DRAND_QUICKNET_CHAIN_HASH,
    DRAND_QUICKNET_GENESIS_TIME,
    drand_round_for_timestamp,
    fetch_drand_signature_for_round,
    resolve_chain_drand_epoch_randomness,
)
from lemma.common.config import LemmaSettings


def test_drand_round_for_timestamp_uses_quicknet_period() -> None:
    assert drand_round_for_timestamp(DRAND_QUICKNET_GENESIS_TIME) == 1
    assert drand_round_for_timestamp(DRAND_QUICKNET_GENESIS_TIME + 2) == 1
    assert drand_round_for_timestamp(DRAND_QUICKNET_GENESIS_TIME + 3) == 2


def test_fetch_drand_signature_for_round_uses_bounded_quicknet_http(monkeypatch) -> None:
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"round": 42, "signature": "abc123"}

    def fake_get(url: str, *, timeout: float) -> Response:
        captured["url"] = url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("lemma.chain.epoch_randomness.httpx.get", fake_get)

    assert fetch_drand_signature_for_round(42) == "abc123"
    assert captured == {
        "timeout": 10.0,
        "url": f"https://api.drand.sh/{DRAND_QUICKNET_CHAIN_HASH}/public/42",
    }


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


def test_chain_drand_epoch_randomness_uses_active_window_length() -> None:
    class Hyperparams:
        tempo = 360

    class BlockInfo:
        timestamp = DRAND_QUICKNET_GENESIS_TIME + 30

    class Subtensor:
        def get_current_block(self) -> int:
            return 1450

        def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> Hyperparams:
            return Hyperparams()

        def get_block_hash(self, block: int | None = None) -> str:
            assert block == 1440
            return "0xanchor"

        def get_block_info(self, block: int | None = None, block_hash: str | None = None) -> BlockInfo:
            assert block == 1440
            return BlockInfo()

    randomness = resolve_chain_drand_epoch_randomness(
        LemmaSettings(_env_file=None, netuid=467, active_window_blocks=1440),
        subtensor=Subtensor(),
        drand_signature_for_round=lambda _round_no: "0xsig",
    )

    assert randomness.tempo == 4
    assert randomness.tempo_length == 1440
    assert randomness.anchor_block == 1440


def test_chain_drand_epoch_randomness_accepts_millisecond_chain_timestamps() -> None:
    class Hyperparams:
        tempo = 360

    class BlockInfo:
        timestamp = (DRAND_QUICKNET_GENESIS_TIME + 30) * 1000

    class Subtensor:
        def get_current_block(self) -> int:
            return 725

        def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> Hyperparams:
            return Hyperparams()

        def get_block_hash(self, block: int | None = None) -> str:
            return "0xanchor"

        def get_block_info(self, block: int | None = None, block_hash: str | None = None) -> BlockInfo:
            return BlockInfo()

    def signature(round_no: int) -> str:
        assert round_no == 11
        return "0xsig"

    randomness = resolve_chain_drand_epoch_randomness(
        LemmaSettings(_env_file=None, netuid=467),
        subtensor=Subtensor(),
        drand_signature_for_round=signature,
    )

    assert randomness.anchor_block_timestamp == DRAND_QUICKNET_GENESIS_TIME + 30
    assert randomness.drand_round == 11

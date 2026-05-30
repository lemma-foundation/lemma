from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from lemma.chain.commitments import _commitment_checkpoint_path, read_all_commitments
from lemma.common.config import LemmaSettings


def _settings(tmp_path: Path) -> LemmaSettings:
    return LemmaSettings(
        _env_file=None,
        netuid=467,
        chain_commitment_checkpoint_dir=tmp_path / "commitment-checkpoints",
    )


def test_read_all_commitments_reads_checkpoint_when_historical_state_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StateDiscardedError(Exception): ...

    class Subtensor:
        def __init__(self, network: str | None = None) -> None:
            self.network = network

        def get_all_commitments(self, netuid: int, block: int | None = None) -> dict[str, str]:
            assert netuid == 467
            assert block == 123
            raise StateDiscardedError("state has been discarded")

    monkeypatch.setitem(sys.modules, "bittensor", type("FakeBittensor", (), {"Subtensor": Subtensor})())

    settings = _settings(tmp_path)
    path = _commitment_checkpoint_path(settings, 123)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"5Hb...": "checkpoint-payload"}) + "\n", encoding="utf-8")

    assert read_all_commitments(settings, block=123) == {"5Hb...": "checkpoint-payload"}


def test_read_all_commitments_rejects_invalid_checkpoint_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StateDiscardedError(Exception): ...

    class Subtensor:
        def __init__(self, network: str | None = None) -> None:
            self.network = network

        def get_all_commitments(self, netuid: int, block: int | None = None) -> dict[str, str]:
            raise StateDiscardedError("state has been discarded")

    monkeypatch.setitem(sys.modules, "bittensor", type("FakeBittensor", (), {"Subtensor": Subtensor})())
    settings = _settings(tmp_path)
    path = _commitment_checkpoint_path(settings, 123)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid commitment checkpoint format"):
        read_all_commitments(settings, block=123)


def test_read_all_commitments_raises_clear_error_without_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StateDiscardedError(Exception): ...

    class Subtensor:
        def __init__(self, network: str | None = None) -> None:
            self.network = network

        def get_all_commitments(self, netuid: int, block: int | None = None) -> dict[str, str]:
            assert netuid == 467
            assert block == 123
            raise StateDiscardedError("StateDiscardedError")

    monkeypatch.setitem(sys.modules, "bittensor", type("FakeBittensor", (), {"Subtensor": Subtensor})())
    settings = _settings(tmp_path)

    with pytest.raises(RuntimeError, match="archive-capable chain RPC"):
        read_all_commitments(settings, block=123)


def test_read_all_commitments_writes_checkpoint_for_successful_historical_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Subtensor:
        def __init__(self, network: str | None = None) -> None:
            self.network = network

        def get_all_commitments(self, netuid: int, block: int | None = None) -> dict[str, str]:
            assert netuid == 467
            assert block == 222
            return {"miner-a": "payload-A", "miner-b": "payload-B"}

    monkeypatch.setitem(sys.modules, "bittensor", type("FakeBittensor", (), {"Subtensor": Subtensor})())
    settings = _settings(tmp_path)

    result = read_all_commitments(settings, block=222)
    checkpoint = _commitment_checkpoint_path(settings, 222)

    assert result == {"miner-a": "payload-A", "miner-b": "payload-B"}
    assert checkpoint.is_file()
    assert json.loads(checkpoint.read_text(encoding="utf-8")) == result

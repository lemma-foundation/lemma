from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from lemma.chain.commitments import (
    compact_storage_commitment_payload,
    latest_storage_commitment_file,
    load_storage_commitment,
    storage_commitment_file,
    storage_commitment_payload,
)


def _commitment(root: Path, tempo: int, payload_suffix: str = "", *, legacy: bool = False) -> Path:
    accepted = "a" * 64
    directory = "b" * 64
    path = root / "canonical" / "sn467" / "commitments" / f"tempo-{tempo:06d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_payload = f"lemma-storage-v1:sn467:{tempo}:{directory}:{accepted}"
    payload = (
        legacy_payload
        if legacy
        else compact_storage_commitment_payload(
            netuid="sn467",
            tempo=tempo,
            tempo_directory_sha256=directory,
            accepted_merkle_root=accepted,
        )
    )
    payload += payload_suffix
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "accepted_merkle_root": accepted,
                "commitment_payload": payload,
                "netuid": "sn467",
                "resolver": "hippius-s3-arion",
                "tempo": tempo,
                "tempo_directory_cid": None,
                "tempo_directory_sha256": directory,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_latest_storage_commitment_file_selects_highest_tempo(tmp_path: Path) -> None:
    _commitment(tmp_path, 1)
    latest = _commitment(tmp_path, 42)
    _commitment(tmp_path, 5)

    assert latest_storage_commitment_file(tmp_path, "sn467") == latest
    assert storage_commitment_file(tmp_path, "sn467", 5).name == "tempo-000005.json"


def test_storage_commitment_payload_validates_expected_preimage(tmp_path: Path) -> None:
    path = _commitment(tmp_path, 7)

    assert load_storage_commitment(path)["tempo"] == 7
    assert storage_commitment_payload(path) == compact_storage_commitment_payload(
        netuid="sn467",
        tempo=7,
        tempo_directory_sha256="b" * 64,
        accepted_merkle_root="a" * 64,
    )
    assert len(storage_commitment_payload(path).encode("utf-8")) <= 128


def test_storage_commitment_payload_accepts_legacy_preimage_artifact(tmp_path: Path) -> None:
    path = _commitment(tmp_path, 7, legacy=True)

    assert storage_commitment_payload(path) == compact_storage_commitment_payload(
        netuid="sn467",
        tempo=7,
        tempo_directory_sha256="b" * 64,
        accepted_merkle_root="a" * 64,
    )


def test_storage_commitment_payload_rejects_drift(tmp_path: Path) -> None:
    path = _commitment(tmp_path, 7, payload_suffix="bad")

    with pytest.raises(ValueError, match="commitment_payload mismatch"):
        storage_commitment_payload(path)


def test_cli_readback_hotkey_skips_wallet_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _commitment(tmp_path, 7)
    hotkey = "5DvFMbph3has15zmHLd6WsZAKNhYN45ctmydJEQTWxA2U2No"
    payload = storage_commitment_payload(path)

    import scripts.publish_chain_commitment as cli

    readback_hotkeys: list[str | None] = []

    def fail_wallet_lookup(_settings: object) -> str:
        raise AssertionError("wallet lookup should be skipped")

    monkeypatch.setattr(cli, "wallet_hotkey_address", fail_wallet_lookup)
    monkeypatch.setattr(
        cli,
        "read_storage_commitment",
        lambda _settings, hotkey=None: readback_hotkeys.append(hotkey) or payload,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_chain_commitment.py",
            "--repo",
            str(tmp_path),
            "--netuid",
            "sn467",
            "--bt-netuid",
            "467",
            "--readback",
            "--hotkey",
            hotkey,
        ],
    )

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert readback_hotkeys == [hotkey]
    assert result["readback_hotkey_address"] == hotkey
    assert result["readback_matches"] is True

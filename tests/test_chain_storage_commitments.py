from __future__ import annotations

import json
from pathlib import Path

import pytest
from lemma.chain.commitments import (
    latest_storage_commitment_file,
    load_storage_commitment,
    storage_commitment_file,
    storage_commitment_payload,
)


def _commitment(root: Path, tempo: int, payload_suffix: str = "") -> Path:
    accepted = "a" * 64
    directory = "b" * 64
    path = root / "canonical" / "sn467" / "commitments" / f"tempo-{tempo:06d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"lemma-storage-v1:sn467:{tempo}:{directory}:{accepted}{payload_suffix}"
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
    assert storage_commitment_payload(path) == f"lemma-storage-v1:sn467:7:{'b' * 64}:{'a' * 64}"


def test_storage_commitment_payload_rejects_drift(tmp_path: Path) -> None:
    path = _commitment(tmp_path, 7, payload_suffix="bad")

    with pytest.raises(ValueError, match="commitment_payload mismatch"):
        storage_commitment_payload(path)

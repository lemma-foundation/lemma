from __future__ import annotations

import json
from pathlib import Path

from lemma.corpus.storage import build_storage_index, canonical_json_bytes, merkle_root, sha256_hex


def _write_epoch(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_build_storage_index_writes_per_epoch_commitment_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-corpus"
    rows = [
        {
            "row_id": "a" * 64,
            "task_id": "task.a",
            "proof_sha256": "b" * 64,
            "queue_position": 2,
            "solver_hotkey": "solver",
            "validator_hotkey": "validator",
        },
        {
            "row_id": "c" * 64,
            "task_id": "task.b",
            "proof_sha256": "d" * 64,
            "queue_position": 7,
            "solver_hotkey": "solver",
            "validator_hotkey": "validator",
        },
    ]
    _write_epoch(repo / "corpus" / "sn467" / "epoch-000042.jsonl", rows)

    index = build_storage_index(repo, "sn467", resolver="hippius-s3-arion")

    tempo_dir = repo / "canonical" / "sn467" / "tempos" / "tempo-000042"
    manifest = json.loads((tempo_dir / "manifest.json").read_text(encoding="utf-8"))
    commitment = json.loads(
        (repo / "canonical" / "sn467" / "commitments" / "tempo-000042.json").read_text(encoding="utf-8")
    )
    leaves = [sha256_hex(canonical_json_bytes(row)) for row in rows]
    expected_root = merkle_root(leaves)

    assert index["epochs"][0]["tempo"] == 42
    assert manifest["accepted_merkle_root"] == expected_root
    assert manifest["entries"][0]["file"] == "entries/slot-000002-aaaaaaaaaaaa.json"
    assert commitment["accepted_merkle_root"] == expected_root
    assert commitment["tempo_directory_cid"] is None
    assert commitment["commitment_payload"].startswith("lemma-storage-v1:sn467:42:")
    assert (repo / "canonical" / "sn467" / "storage-index.json").is_file()

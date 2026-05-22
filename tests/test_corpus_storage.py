from __future__ import annotations

import json
from pathlib import Path

from lemma.chain.commitments import compact_storage_commitment_payload, compact_tempo_commitment_payload
from lemma.corpus.storage import (
    build_active_pool_storage,
    build_epoch_storage_from_rows,
    build_storage_index,
    canonical_json_bytes,
    merkle_root,
    sha256_hex,
)
from lemma.task_supply import make_task


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
    assert commitment["commitment_payload"] == compact_storage_commitment_payload(
        netuid="sn467",
        tempo=42,
        tempo_directory_sha256=commitment["tempo_directory_sha256"],
        accepted_merkle_root=expected_root,
    )
    assert len(commitment["commitment_payload"].encode("utf-8")) <= 128
    assert (repo / "canonical" / "sn467" / "storage-index.json").is_file()


def test_active_pool_and_accepted_storage_share_tempo_commitment(tmp_path: Path) -> None:
    task = make_task(
        task_id="lemma.test.active",
        title="Active true",
        theorem_name="active_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    output_root = tmp_path / "canonical"

    active = build_active_pool_storage((task,), output_root, netuid="sn467", tempo=9, resolver="hippius-ipfs")
    accepted = build_epoch_storage_from_rows(
        [],
        output_root,
        netuid="sn467",
        tempo=9,
        resolver="hippius-ipfs",
        active_pool=active,
    )

    commitment = json.loads((output_root / "sn467" / "commitments" / "tempo-000009.json").read_text(encoding="utf-8"))
    expected = compact_tempo_commitment_payload(
        netuid="sn467",
        tempo=9,
        active_pool_directory_sha256=str(active["active_pool_directory_sha256"]),
        accepted_directory_sha256=str(accepted["tempo_directory_sha256"]),
        accepted_merkle_root=str(accepted["accepted_merkle_root"]),
    )

    assert (output_root / "sn467" / "active-pools" / "tempo-000009" / "manifest.json").is_file()
    assert (output_root / "sn467" / "tempos" / "tempo-000009" / "manifest.json").is_file()
    assert commitment["active_pool_directory_sha256"] == active["active_pool_directory_sha256"]
    assert commitment["commitment_payload"] == expected
    assert commitment["tempo_commitment_payload"] == expected

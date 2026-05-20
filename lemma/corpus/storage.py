"""Canonical storage artifacts for accepted corpus epochs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from lemma.chain.commitments import compact_storage_commitment_payload

EPOCH_RE = re.compile(r"epoch-(\d+)\.jsonl$")


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def merkle_root(hex_digests: list[str]) -> str:
    if not hex_digests:
        return sha256_hex(b"")
    level = [bytes.fromhex(item) for item in hex_digests]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [hashlib.sha256(level[index] + level[index + 1]).digest() for index in range(0, len(level), 2)]
    return level[0].hex()


def epoch_number(path: Path) -> int:
    match = EPOCH_RE.fullmatch(path.name)
    if not match:
        raise ValueError(f"not an epoch JSONL file: {path}")
    return int(match.group(1))


def _slot_index(row: dict[str, Any], fallback: int) -> int:
    value = row.get("queue_position")
    return value if isinstance(value, int) and value >= 0 else fallback


def _entry_name(row: dict[str, Any], fallback: int) -> str:
    slot = _slot_index(row, fallback)
    row_id = str(row.get("row_id") or sha256_hex(canonical_json_bytes(row)))
    return f"slot-{slot:06d}-{row_id[:12]}.json"


def directory_digest(directory: Path) -> str:
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def build_epoch_storage(epoch_file: Path, output_root: Path, *, netuid: str, resolver: str) -> dict[str, object]:
    tempo = epoch_number(epoch_file)
    tempo_dir = output_root / netuid / "tempos" / f"tempo-{tempo:06d}"
    entries_dir = tempo_dir / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    leaf_hashes: list[str] = []
    for line_number, line in enumerate(epoch_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{epoch_file}:{line_number}: expected JSON object")
        entry_bytes = canonical_json_bytes(row)
        entry_sha256 = sha256_hex(entry_bytes)
        entry_name = _entry_name(row, line_number - 1)
        (entries_dir / entry_name).write_bytes(entry_bytes)
        leaf_hashes.append(entry_sha256)
        entries.append(
            {
                "entry_sha256": entry_sha256,
                "file": f"entries/{entry_name}",
                "line_number": line_number,
                "proof_sha256": row.get("proof_sha256", ""),
                "row_id": row.get("row_id", ""),
                "slot_index": _slot_index(row, line_number - 1),
                "solver_hotkey": row.get("solver_hotkey", ""),
                "task_id": row.get("task_id", ""),
                "validator_hotkey": row.get("validator_hotkey", ""),
            }
        )

    accepted_merkle_root = merkle_root(leaf_hashes)
    manifest = {
        "schema_version": 1,
        "accepted_merkle_root": accepted_merkle_root,
        "entries": entries,
        "entry_count": len(entries),
        "netuid": netuid,
        "resolver": resolver,
        "source_epoch_file": f"corpus/{netuid}/{epoch_file.name}",
        "tempo": tempo,
    }
    manifest_path = tempo_dir / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    tempo_directory_sha256 = directory_digest(tempo_dir)
    commitment_path = output_root / netuid / "commitments" / f"tempo-{tempo:06d}.json"
    commitment_path.parent.mkdir(parents=True, exist_ok=True)
    commitment = {
        "schema_version": 1,
        "accepted_merkle_root": accepted_merkle_root,
        "commitment_payload": compact_storage_commitment_payload(
            netuid=netuid,
            tempo=tempo,
            tempo_directory_sha256=tempo_directory_sha256,
            accepted_merkle_root=accepted_merkle_root,
        ),
        "netuid": netuid,
        "resolver": resolver,
        "tempo": tempo,
        "tempo_directory_cid": None,
        "tempo_directory_sha256": tempo_directory_sha256,
    }
    commitment_path.write_bytes(canonical_json_bytes(commitment))
    return {
        "accepted_merkle_root": accepted_merkle_root,
        "commitment": commitment_path,
        "directory": tempo_dir,
        "entry_count": len(entries),
        "tempo": tempo,
        "tempo_directory_sha256": tempo_directory_sha256,
    }


def build_storage_index(repo: Path, netuid: str, *, resolver: str = "hippius-s3-arion") -> dict[str, object]:
    corpus_dir = repo / "corpus" / netuid
    output_root = repo / "canonical"
    if not corpus_dir.is_dir():
        raise SystemExit(f"missing corpus directory: {corpus_dir}")

    epochs = [
        build_epoch_storage(path, output_root, netuid=netuid, resolver=resolver)
        for path in sorted(corpus_dir.glob("epoch-*.jsonl"))
    ]
    index = {
        "schema_version": 1,
        "epochs": [
            {
                "accepted_merkle_root": item["accepted_merkle_root"],
                "entry_count": item["entry_count"],
                "tempo": item["tempo"],
                "tempo_directory": f"canonical/{netuid}/tempos/tempo-{item['tempo']:06d}/",
                "tempo_directory_sha256": item["tempo_directory_sha256"],
            }
            for item in epochs
        ],
        "netuid": netuid,
        "resolver": resolver,
    }
    index_path = output_root / netuid / "storage-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(canonical_json_bytes(index))
    index["path"] = index_path
    return index

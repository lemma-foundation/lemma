"""Canonical storage artifacts for accepted corpus epochs."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from lemma.chain.commitments import (
    compact_active_pool_commitment_payload,
    compact_storage_commitment_payload,
    compact_tempo_commitment_payload,
)
from lemma.corpus import CorpusRow
from lemma.supply.controller import CurriculumTempoRecord
from lemma.tasks import LemmaTask

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


def _task_slot_name(slot_index: int, task: LemmaTask) -> str:
    return f"slot-{slot_index:06d}-{task.id.replace('/', '_')[:64]}.json"


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


def build_active_pool_storage(
    active_tasks: Sequence[LemmaTask],
    output_root: Path,
    *,
    netuid: str,
    tempo: int,
    resolver: str,
) -> dict[str, object]:
    active_dir = output_root / netuid / "active-pools" / f"tempo-{tempo:06d}"
    slots_dir = active_dir / "slots"
    if active_dir.exists():
        shutil.rmtree(active_dir)
    slots_dir.mkdir(parents=True, exist_ok=True)

    slots: list[dict[str, object]] = []
    leaf_hashes: list[str] = []
    for slot_index, task in enumerate(active_tasks):
        payload = task.model_dump(mode="json", exclude_none=True)
        slot_bytes = canonical_json_bytes(payload)
        slot_sha256 = sha256_hex(slot_bytes)
        slot_name = _task_slot_name(slot_index, task)
        (slots_dir / slot_name).write_bytes(slot_bytes)
        leaf_hashes.append(slot_sha256)
        slots.append(
            {
                "file": f"slots/{slot_name}",
                "slot_index": slot_index,
                "slot_sha256": slot_sha256,
                "target_sha256": task.target_sha256,
                "task_id": task.id,
                "task_version": task.task_version,
            }
        )

    active_pool_merkle_root = merkle_root(leaf_hashes)
    manifest = {
        "schema_version": 1,
        "active_pool_merkle_root": active_pool_merkle_root,
        "kind": "active_pool",
        "netuid": netuid,
        "resolver": resolver,
        "slot_count": len(slots),
        "slots": slots,
        "tempo": tempo,
    }
    manifest_path = active_dir / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    active_pool_directory_sha256 = directory_digest(active_dir)
    commitment_payload = compact_active_pool_commitment_payload(
        netuid=netuid,
        tempo=tempo,
        active_pool_directory_sha256=active_pool_directory_sha256,
    )
    return {
        "active_pool_directory": active_dir,
        "active_pool_directory_cid": None,
        "active_pool_directory_sha256": active_pool_directory_sha256,
        "active_pool_merkle_root": active_pool_merkle_root,
        "active_pool_commitment_payload": commitment_payload,
        "slot_count": len(slots),
        "tempo": tempo,
    }


def build_epoch_storage_from_rows(
    rows: Iterable[CorpusRow | dict[str, Any]],
    output_root: Path,
    *,
    netuid: str,
    tempo: int,
    resolver: str,
    active_pool: dict[str, object] | None = None,
) -> dict[str, object]:
    tempo_dir = output_root / netuid / "tempos" / f"tempo-{tempo:06d}"
    entries_dir = tempo_dir / "entries"
    if tempo_dir.exists():
        shutil.rmtree(tempo_dir)
    entries_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    leaf_hashes: list[str] = []
    for line_number, raw_row in enumerate(rows, start=1):
        row = raw_row.model_dump(mode="json", exclude_none=True) if isinstance(raw_row, CorpusRow) else raw_row
        if not isinstance(row, dict):
            raise ValueError(f"accepted row {line_number}: expected JSON object")
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
        "tempo": tempo,
    }
    manifest_path = tempo_dir / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    tempo_directory_sha256 = directory_digest(tempo_dir)
    active_pool_directory_sha256 = str((active_pool or {}).get("active_pool_directory_sha256") or "")
    active_pool_directory_cid = (active_pool or {}).get("active_pool_directory_cid")
    commitment_path = output_root / netuid / "commitments" / f"tempo-{tempo:06d}.json"
    commitment_path.parent.mkdir(parents=True, exist_ok=True)
    accepted_payload = compact_storage_commitment_payload(
        netuid=netuid,
        tempo=tempo,
        tempo_directory_sha256=tempo_directory_sha256,
        accepted_merkle_root=accepted_merkle_root,
    )
    tempo_payload = (
        compact_tempo_commitment_payload(
            netuid=netuid,
            tempo=tempo,
            active_pool_directory_sha256=active_pool_directory_sha256,
            accepted_directory_sha256=tempo_directory_sha256,
            accepted_merkle_root=accepted_merkle_root,
        )
        if active_pool_directory_sha256
        else accepted_payload
    )
    commitment = {
        "schema_version": 1,
        "accepted_merkle_root": accepted_merkle_root,
        "active_pool_directory_cid": active_pool_directory_cid,
        "active_pool_directory_sha256": active_pool_directory_sha256 or None,
        "commitment_payload": tempo_payload,
        "netuid": netuid,
        "resolver": resolver,
        "tempo": tempo,
        "tempo_commitment_payload": tempo_payload,
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
        "tempo_commitment_payload": tempo_payload,
        "tempo_directory_sha256": tempo_directory_sha256,
    }


def _curriculum_record_payload(record: CurriculumTempoRecord, *, netuid: str, resolver: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "curriculum_tempo_state",
        "netuid": netuid,
        "resolver": resolver,
        **json.loads(record.to_json()),
    }


def build_curriculum_state_storage(
    records: Sequence[CurriculumTempoRecord],
    output_root: Path,
    *,
    netuid: str,
    resolver: str,
) -> dict[str, object]:
    curriculum_dir = output_root / netuid / "curriculum"
    if curriculum_dir.exists():
        shutil.rmtree(curriculum_dir)
    curriculum_dir.mkdir(parents=True, exist_ok=True)

    unique = {record.tempo: record for record in records}
    ordered = [unique[tempo] for tempo in sorted(unique)]
    entries: list[dict[str, object]] = []
    jsonl_lines: list[str] = []
    for record in ordered:
        payload = _curriculum_record_payload(record, netuid=netuid, resolver=resolver)
        record_bytes = canonical_json_bytes(payload)
        record_sha256 = sha256_hex(record_bytes)
        filename = f"tempo-{record.tempo:06d}.json"
        (curriculum_dir / filename).write_bytes(record_bytes)
        jsonl_lines.append(record_bytes.decode("utf-8").rstrip("\n"))
        entries.append(
            {
                "tempo": record.tempo,
                "file": filename,
                "record_sha256": record_sha256,
                "active_K": record.active_K,
                "frontier_depth": record.frontier_depth,
                "ema_solve_rate": record.ema_solve_rate,
            }
        )

    state_jsonl = curriculum_dir / "curriculum.jsonl"
    state_jsonl.write_text("\n".join(jsonl_lines) + ("\n" if jsonl_lines else ""), encoding="utf-8")
    latest = ordered[-1] if ordered else None
    manifest = {
        "schema_version": 1,
        "kind": "curriculum_state",
        "netuid": netuid,
        "resolver": resolver,
        "records": entries,
        "record_count": len(entries),
        "latest_tempo": latest.tempo if latest else None,
        "latest_active_K": latest.active_K if latest else None,
        "latest_frontier_depth": latest.frontier_depth if latest else None,
        "state_jsonl": "curriculum.jsonl",
    }
    (curriculum_dir / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    return {
        "curriculum_directory": curriculum_dir,
        "curriculum_directory_sha256": directory_digest(curriculum_dir),
        "curriculum_record_count": len(entries),
        "curriculum_latest_tempo": latest.tempo if latest else None,
        "curriculum_state_jsonl": state_jsonl,
    }


def _epoch_storage_tempo(epoch_file: Path, rows: Sequence[dict[str, Any]]) -> int:
    row_tempos: set[int] = set()
    missing_tempo = False
    for line_number, row in enumerate(rows, start=1):
        value = row.get("tempo")
        if value is None:
            missing_tempo = True
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{epoch_file}:{line_number}: tempo must be a non-negative integer")
        row_tempos.add(value)
    if not row_tempos:
        return epoch_number(epoch_file)
    if missing_tempo or len(row_tempos) != 1:
        raise ValueError(f"{epoch_file}: all rows must carry the same tempo")
    return next(iter(row_tempos))


def _rows_have_chain_tempo(rows: Sequence[dict[str, Any]]) -> bool:
    return any(row.get("tempo") is not None for row in rows)


def _existing_epoch_storage(
    rows: Sequence[dict[str, Any]], output_root: Path, *, netuid: str, tempo: int
) -> dict[str, object] | None:
    tempo_dir = output_root / netuid / "tempos" / f"tempo-{tempo:06d}"
    manifest_path = tempo_dir / "manifest.json"
    commitment_path = output_root / netuid / "commitments" / f"tempo-{tempo:06d}.json"
    if not manifest_path.exists() and not commitment_path.exists():
        return None
    if not manifest_path.is_file() or not commitment_path.is_file():
        raise ValueError(f"incomplete canonical storage for {netuid} tempo {tempo}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commitment = json.loads(commitment_path.read_text(encoding="utf-8"))
    accepted_merkle_root = merkle_root([sha256_hex(canonical_json_bytes(row)) for row in rows])
    entry_count = len(rows)
    if manifest.get("tempo") != tempo or commitment.get("tempo") != tempo:
        raise ValueError(f"canonical storage tempo mismatch for {netuid} tempo {tempo}")
    if manifest.get("entry_count") != entry_count:
        raise ValueError(f"canonical storage entry count mismatch for {netuid} tempo {tempo}")
    if manifest.get("accepted_merkle_root") != accepted_merkle_root:
        raise ValueError(f"canonical storage accepted root mismatch for {netuid} tempo {tempo}")
    if commitment.get("accepted_merkle_root") != accepted_merkle_root:
        raise ValueError(f"canonical commitment accepted root mismatch for {netuid} tempo {tempo}")

    tempo_directory_sha256 = directory_digest(tempo_dir)
    if commitment.get("tempo_directory_sha256") != tempo_directory_sha256:
        raise ValueError(f"canonical commitment directory hash mismatch for {netuid} tempo {tempo}")
    return {
        "accepted_merkle_root": accepted_merkle_root,
        "commitment": commitment_path,
        "directory": tempo_dir,
        "entry_count": entry_count,
        "tempo": tempo,
        "tempo_commitment_payload": commitment.get("tempo_commitment_payload") or commitment.get("commitment_payload"),
        "tempo_directory_sha256": tempo_directory_sha256,
    }


def _remove_legacy_epoch_storage(epoch_file: Path, output_root: Path, *, netuid: str, tempo: int) -> None:
    legacy_tempo = epoch_number(epoch_file)
    if legacy_tempo == tempo:
        return
    tempo_dir = output_root / netuid / "tempos" / f"tempo-{legacy_tempo:06d}"
    manifest_path = tempo_dir / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_epoch_file") != f"corpus/{netuid}/{epoch_file.name}":
        return
    commitment_path = output_root / netuid / "commitments" / f"tempo-{legacy_tempo:06d}.json"
    if commitment_path.is_file():
        commitment = json.loads(commitment_path.read_text(encoding="utf-8"))
        if commitment.get("active_pool_directory_sha256"):
            return
        commitment_path.unlink()
    shutil.rmtree(tempo_dir)


def _read_epoch_rows(epoch_file: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(epoch_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{epoch_file}:{line_number}: expected JSON object")
        rows.append(row)
    return rows


def build_epoch_storage(epoch_file: Path, output_root: Path, *, netuid: str, resolver: str) -> dict[str, object]:
    rows = _read_epoch_rows(epoch_file)
    tempo = _epoch_storage_tempo(epoch_file, rows)
    _remove_legacy_epoch_storage(epoch_file, output_root, netuid=netuid, tempo=tempo)
    if _rows_have_chain_tempo(rows):
        existing = _existing_epoch_storage(rows, output_root, netuid=netuid, tempo=tempo)
        if existing is not None:
            return existing
    result = build_epoch_storage_from_rows(rows, output_root, netuid=netuid, tempo=tempo, resolver=resolver)
    directory = result["directory"]
    commitment_path = result["commitment"]
    if not isinstance(directory, Path) or not isinstance(commitment_path, Path):
        raise TypeError("storage builder returned invalid paths")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_epoch_file"] = f"corpus/{netuid}/{epoch_file.name}"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    tempo_directory_sha256 = directory_digest(directory)
    commitment = json.loads(commitment_path.read_text(encoding="utf-8"))
    commitment["tempo_directory_sha256"] = tempo_directory_sha256
    commitment["commitment_payload"] = compact_storage_commitment_payload(
        netuid=netuid,
        tempo=tempo,
        tempo_directory_sha256=tempo_directory_sha256,
        accepted_merkle_root=str(commitment["accepted_merkle_root"]),
    )
    commitment["tempo_commitment_payload"] = commitment["commitment_payload"]
    commitment_path.write_bytes(canonical_json_bytes(commitment))
    return {
        **result,
        "tempo_directory_sha256": tempo_directory_sha256,
        "tempo_commitment_payload": commitment["commitment_payload"],
    }


def build_storage_index(repo: Path, netuid: str, *, resolver: str = "hippius-s3-arion") -> dict[str, object]:
    corpus_dir = repo / "corpus" / netuid
    output_root = repo / "canonical"
    if not corpus_dir.is_dir():
        raise SystemExit(f"missing corpus directory: {corpus_dir}")

    rows_by_tempo: dict[int, list[dict[str, Any]]] = {}
    files_by_tempo: dict[int, list[Path]] = {}
    seen_row_ids: dict[tuple[int, str], str] = {}
    for path in sorted(corpus_dir.glob("epoch-*.jsonl")):
        rows = _read_epoch_rows(path)
        tempo = _epoch_storage_tempo(path, rows)
        _remove_legacy_epoch_storage(path, output_root, netuid=netuid, tempo=tempo)
        for line_number, row in enumerate(rows, start=1):
            row_id = str(row.get("row_id") or sha256_hex(canonical_json_bytes(row)))
            location = f"{path.name}:{line_number}"
            key = (tempo, row_id)
            if previous := seen_row_ids.get(key):
                raise ValueError(f"duplicate corpus row_id {row_id} for tempo {tempo}: {previous} and {location}")
            seen_row_ids[key] = location
        rows_by_tempo.setdefault(tempo, []).extend(rows)
        files_by_tempo.setdefault(tempo, []).append(path)

    epochs: list[dict[str, object]] = []
    for tempo in sorted(rows_by_tempo):
        rows = rows_by_tempo[tempo]
        if _rows_have_chain_tempo(rows):
            existing = _existing_epoch_storage(rows, output_root, netuid=netuid, tempo=tempo)
            if existing is not None:
                epochs.append(existing)
                continue
        files = files_by_tempo[tempo]
        if len(files) == 1:
            epochs.append(build_epoch_storage(files[0], output_root, netuid=netuid, resolver=resolver))
        else:
            epochs.append(
                build_epoch_storage_from_rows(rows, output_root, netuid=netuid, tempo=tempo, resolver=resolver)
            )
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
    curriculum_dir = output_root / netuid / "curriculum"
    curriculum_manifest = curriculum_dir / "manifest.json"
    if curriculum_manifest.is_file():
        manifest = json.loads(curriculum_manifest.read_text(encoding="utf-8"))
        index["curriculum"] = {
            "directory": f"canonical/{netuid}/curriculum/",
            "directory_sha256": directory_digest(curriculum_dir),
            "latest_active_K": manifest.get("latest_active_K"),
            "latest_frontier_depth": manifest.get("latest_frontier_depth"),
            "latest_tempo": manifest.get("latest_tempo"),
            "record_count": manifest.get("record_count"),
        }
    index_path = output_root / netuid / "storage-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(canonical_json_bytes(index))
    index["path"] = index_path
    return index

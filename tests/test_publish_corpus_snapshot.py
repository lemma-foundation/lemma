from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from scripts.publish_corpus_snapshot import (
    github_release_command,
    hippius_commands,
    release_notes,
    snapshot_id,
    snapshot_label,
    write_manifest,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_write_manifest_hashes_public_paths_without_local_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-corpus"
    files = {
        "registries/sn467/registry.json": "{}\n",
        "corpus/sn467/epoch-000001.jsonl": '{"row": 1}\n',
        "indexes/sn467/corpus-index.json": '{"rows": 1}\n',
        "exports/sn467/lemma-proofs.jsonl": '{"proof": true}\n',
        "canonical/sn467/storage-index.json": '{"epochs": []}\n',
    }
    for relative, text in files.items():
        _write(repo / relative, text)

    manifest = write_manifest(repo, "sn467")
    lines = manifest.read_text(encoding="utf-8").splitlines()

    assert lines == [
        f"{hashlib.sha256(files[relative].encode()).hexdigest()}  {relative}"
        for relative in sorted(files)
    ]
    assert str(tmp_path) not in manifest.read_text(encoding="utf-8")


def test_snapshot_labels_are_release_safe() -> None:
    now = datetime(2026, 5, 20, 2, 32, 8, tzinfo=UTC)

    assert snapshot_id(now) == "2026-05-20T02-32-08Z"
    assert snapshot_label("2026-05-20T02-32-08Z") == "2026-05-20T02:32:08Z"


def test_hippius_commands_use_timestamped_snapshot_without_delete(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-corpus"
    manifest = repo / "MANIFEST.sha256"
    commands = hippius_commands(
        aws=["aws"],
        repo=repo,
        bucket="lemma-corpus-sn467",
        endpoint_url="https://s3.hippius.com",
        netuid="sn467",
        snapshot="2026-05-20T02-32-08Z",
        manifest_path=manifest,
    )

    flattened = [part for command in commands for part in command]
    assert "--delete" not in flattened
    assert "s3://lemma-corpus-sn467/snapshots/2026-05-20T02-32-08Z/sn467/corpus/" in flattened
    assert "s3://lemma-corpus-sn467/snapshots/2026-05-20T02-32-08Z/canonical/sn467/" in flattened
    assert "s3://lemma-corpus-sn467/snapshots/2026-05-20T02-32-08Z/MANIFEST.sha256" in flattened


def test_github_release_command_attaches_manifest_and_storage_index(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.sha256"
    storage_index = tmp_path / "storage-index.json"
    command = github_release_command(
        github_repo="lemma-foundation/lemma-corpus",
        manifest_path=manifest,
        storage_index_path=storage_index,
        netuid="sn467",
        snapshot="2026-05-20T02-32-08Z",
        bucket="lemma-corpus-sn467",
    )

    assert command[:4] == ["gh", "release", "create", "sn467-2026-05-20T02-32-08Z"]
    assert str(manifest) in command
    assert str(storage_index) in command
    assert "--target" in command
    assert "SN467 corpus snapshot 2026-05-20T02:32:08Z" in command
    assert "s3://lemma-corpus-sn467/snapshots/2026-05-20T02-32-08Z/" in release_notes(
        bucket="lemma-corpus-sn467",
        netuid="sn467",
        snapshot="2026-05-20T02-32-08Z",
    )

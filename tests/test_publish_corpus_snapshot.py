from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.publish_corpus_snapshot import (
    commit_repo_changes,
    github_release_command,
    hippius_commands,
    huggingface_commands,
    main,
    public_repo_paths,
    release_notes,
    snapshot_id,
    snapshot_label,
    sync_public_inputs,
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


def test_huggingface_commands_upload_append_only_snapshot_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-corpus"
    manifest = repo / "MANIFEST.sha256"
    storage_index = repo / "canonical" / "sn467" / "storage-index.json"

    commands = huggingface_commands(
        hf=["hf"],
        hf_repo_id="lemma-foundation/lean",
        repo=repo,
        netuid="sn467",
        snapshot="2026-05-20T02-32-08Z",
        manifest_path=manifest,
        storage_index_path=storage_index,
    )

    flattened = [part for command in commands for part in command]
    assert "lemma-foundation/lean" in flattened
    assert "snapshots/2026-05-20T02-32-08Z/canonical/sn467" in flattened
    assert "snapshots/2026-05-20T02-32-08Z/exports/sn467" in flattened
    assert "snapshots/2026-05-20T02-32-08Z/MANIFEST.sha256" in flattened
    assert "snapshots/2026-05-20T02-32-08Z/storage-index.json" in flattened
    assert "--repo-type" in flattened
    assert "dataset" in flattened


def test_commit_repo_changes_stages_only_public_corpus_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-corpus"
    _write(repo / "README.md", "- corpus rows: `0`\n")
    _write(repo / "DATASET_CARD.md", "dataset\n")
    for relative, text in {
        "registries/sn467/registry.json": "{}\n",
        "corpus/sn467/epoch-000001.jsonl": '{"row": 1}\n',
        "indexes/sn467/corpus-index.json": '{"rows": 1}\n',
        "exports/sn467/lemma-proofs.jsonl": '{"proof": true}\n',
        "canonical/sn467/storage-index.json": '{"epochs": []}\n',
        "MANIFEST.sha256": "hash  file\n",
    }.items():
        _write(repo / relative, text)
    _write(repo / "scratch.txt", "private local scratch\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", *public_repo_paths("sn467")], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    _write(repo / "corpus/sn467/epoch-000002.jsonl", '{"row": 2}\n')
    _write(repo / "scratch.txt", "updated scratch\n")

    committed = commit_repo_changes(repo, netuid="sn467", snapshot="2026-05-21T01-41-21Z", push=False, dry_run=False)

    assert committed is True
    changed = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    assert "corpus/sn467/epoch-000002.jsonl" in changed
    assert "scratch.txt" not in changed


def test_sync_public_inputs_copies_only_publishable_live_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-corpus"
    live = tmp_path / "live"
    registry_sha = "a" * 64
    _write(live / "corpus/epoch-000041.jsonl", '{"row": 41}\n')
    _write(live / "corpus/epoch-local.jsonl", '{"local": true}\n')
    _write(live / "canonical/tempos/tempo-019958/manifest.json", '{"tempo": 19958}\n')
    _write(
        live / "registries/tempo-19958.registry.json",
        f'{{"schema_version": 1, "sha256": "{registry_sha}", "tasks": []}}\n',
    )
    legacy_registry = '{"schema_version": 1, "tasks": []}\n'
    _write(live / "registries/tempo-19957.registry.json", legacy_registry)

    counts = sync_public_inputs(
        repo,
        "sn467",
        corpus_dir=live / "corpus",
        canonical_dir=live / "canonical",
        registry_cache_dir=live / "registries",
    )

    assert counts == {"corpus_files": 1, "canonical_files": 1, "registry_files": 2}
    assert (repo / "corpus/sn467/epoch-000041.jsonl").read_text(encoding="utf-8") == '{"row": 41}\n'
    assert not (repo / "corpus/sn467/epoch-local.jsonl").exists()
    assert (repo / "canonical/sn467/tempos/tempo-019958/manifest.json").exists()
    assert (repo / f"registries/sn467/{registry_sha}.json").exists()
    assert (repo / f"registries/sn467/{hashlib.sha256(legacy_registry.encode()).hexdigest()}.json").exists()
    index = json.loads((repo / "registries/sn467/index.json").read_text(encoding="utf-8"))
    assert index["registries"]["19958"] == {"path": f"{registry_sha}.json", "sha256": registry_sha}


def test_registry_cache_only_skips_snapshot_artifacts(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: ANN001
    repo = tmp_path / "lemma-corpus"
    live = tmp_path / "registries"
    registry_sha = "b" * 64
    _write(
        live / "tempo-19987.registry.json",
        f'{{"schema_version": 1, "sha256": "{registry_sha}", "tasks": []}}\n',
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_corpus_snapshot.py",
            "--repo",
            str(repo),
            "--netuid",
            "sn467",
            "--sync-registry-cache-dir",
            str(live),
            "--registry-cache-only",
        ],
    )

    assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["synced"] == {"corpus_files": 0, "canonical_files": 0, "registry_files": 1}
    assert output["repo_committed"] is False
    assert (repo / f"registries/sn467/{registry_sha}.json").exists()
    assert (repo / "registries/sn467/index.json").exists()
    assert not (repo / "MANIFEST.sha256").exists()
    assert not (repo / "canonical/sn467/storage-index.json").exists()

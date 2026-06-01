from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lemma.supply.ingredients import ingredient_manifest_bytes, ingredient_manifest_from_root
from scripts.publish_proof_atlas_snapshot import (
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
from tests.test_ingredient_supply import _write_selection_ingredient_repo


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_ingredient_task_bundle(path: Path, *, tempo: int = 19958, netuid: int = 467) -> dict[str, object]:
    artifacts = {
        "active_registry": "active-registry.json",
        "gate_receipt": "gate-receipt.json",
        "generation_receipt": "generation-receipt.json",
        "generation_receipt_envelope": "generation-receipt-envelope.json",
        "selection_receipt": "selection-receipt.json",
        "shortcut_receipt": "shortcut-receipt.json",
        "task": "task.json",
    }
    refs = {}
    for key, filename in artifacts.items():
        text = json.dumps({"artifact": key}, sort_keys=True, separators=(",", ":")) + "\n"
        _write(path / filename, text)
        refs[key] = {"path": filename, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    manifest = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": "a" * 64,
        "theorem_statement_sha256": "b" * 64,
        "selected_selector_id": "list_length_selector_v1",
        "selected_recipe_id": "list_length_v1",
        "selected_parameters_sha256": "c" * 64,
        "theorem_type_expr_sha256": "d" * 64,
        "novelty_family_hash": "e" * 64,
        "lemma_corpus_snapshot_sha256": "f" * 64,
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": "1" * 64,
        "netuid": netuid,
        "tempo": tempo,
        "epoch_seed_sha256": "2" * 64,
        "challenge_seed_sha256": "3" * 64,
        "difficulty_state_sha256": "4" * 64,
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": "5" * 64,
        "selection_receipt_sha256": "6" * 64,
        "gate_receipt_sha256": "7" * 64,
        "shortcut_receipt_sha256": "8" * 64,
        "generation_receipt_sha256": "9" * 64,
        "generation_receipt_envelope_sha256": "a" * 64,
        "artifacts": refs,
    }
    _write(path / "artifact-manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
    return manifest


def test_write_manifest_hashes_public_paths_without_local_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    files = {
        "tasks/sn467/registries/registry.json": "{}\n",
        "graph/sn467/roots/index.json": '{"graph_roots": {}}\n',
        "tasks/sn467/bundles/index.json": '{"task_bundles": {}}\n',
        "proofs/sn467/accepted/epoch-000001.jsonl": '{"row": 1}\n',
        "proofs/sn467/index.json": '{"rows": 1}\n',
        "graph/mathlib/facts.jsonl": '{"fact": true}\n',
        "generation/recipes/recipe_rules.json": '{"recipes": []}\n',
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
    repo = tmp_path / "lemma-proof-atlas"
    manifest = repo / "MANIFEST.sha256"
    commands = hippius_commands(
        aws=["aws"],
        repo=repo,
        bucket="lemma-proof-atlas-sn467",
        endpoint_url="https://s3.hippius.com",
        netuid="sn467",
        snapshot="2026-05-20T02-32-08Z",
        manifest_path=manifest,
    )

    flattened = [part for command in commands for part in command]
    assert "--delete" not in flattened
    assert "s3://lemma-proof-atlas-sn467/snapshots/2026-05-20T02-32-08Z/proofs/sn467/" in flattened
    assert "s3://lemma-proof-atlas-sn467/snapshots/2026-05-20T02-32-08Z/tasks/sn467/bundles/" in flattened
    assert "s3://lemma-proof-atlas-sn467/snapshots/2026-05-20T02-32-08Z/graph/mathlib/" in flattened
    assert "s3://lemma-proof-atlas-sn467/snapshots/2026-05-20T02-32-08Z/canonical/sn467/" in flattened
    assert "s3://lemma-proof-atlas-sn467/snapshots/2026-05-20T02-32-08Z/MANIFEST.sha256" in flattened


def test_github_release_command_attaches_manifest_and_storage_index(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.sha256"
    storage_index = tmp_path / "storage-index.json"
    command = github_release_command(
        github_repo="lemma-foundation/lemma-proof-atlas",
        manifest_path=manifest,
        storage_index_path=storage_index,
        netuid="sn467",
        snapshot="2026-05-20T02-32-08Z",
        bucket="lemma-proof-atlas-sn467",
    )

    assert command[:4] == ["gh", "release", "create", "sn467-2026-05-20T02-32-08Z"]
    assert str(manifest) in command
    assert str(storage_index) in command
    assert "--target" in command
    assert "SN467 Proof Atlas snapshot 2026-05-20T02:32:08Z" in command
    assert "s3://lemma-proof-atlas-sn467/snapshots/2026-05-20T02-32-08Z/" in release_notes(
        bucket="lemma-proof-atlas-sn467",
        netuid="sn467",
        snapshot="2026-05-20T02-32-08Z",
    )


def test_huggingface_commands_upload_append_only_snapshot_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
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
    assert str(repo / "canonical" / "sn467") not in flattened
    assert str(repo / "exports" / "sn467") not in flattened
    assert "snapshots/2026-05-20T02-32-08Z/exports/sn467/lemma-proofs.jsonl" in flattened
    assert "snapshots/2026-05-20T02-32-08Z/exports/sn467/benchmark-index.json" in flattened
    assert "snapshots/2026-05-20T02-32-08Z/MANIFEST.sha256" in flattened
    assert "snapshots/2026-05-20T02-32-08Z/storage-index.json" in flattened
    assert "--repo-type" in flattened
    assert "dataset" in flattened


def test_commit_repo_changes_stages_only_public_atlas_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    _write(repo / "README.md", "- accepted proof rows: `0`\n")
    _write(repo / "ATLAS_CARD.md", "dataset\n")
    for relative, text in {
        "tasks/sn467/registries/registry.json": "{}\n",
        "graph/sn467/roots/index.json": '{"graph_roots": {}}\n',
        "tasks/sn467/bundles/index.json": '{"task_bundles": {}}\n',
        "proofs/sn467/accepted/epoch-000001.jsonl": '{"row": 1}\n',
        "proofs/sn467/index.json": '{"rows": 1}\n',
        "graph/mathlib/facts.jsonl": '{"fact": true}\n',
        "generation/recipes/recipe_rules.json": '{"recipes": []}\n',
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
    _write(repo / "proofs/sn467/accepted/epoch-000002.jsonl", '{"row": 2}\n')
    _write(repo / "graph/sn467/roots/index.json", '{"graph_roots": {"abc": {}}}\n')
    _write(repo / "tasks/sn467/bundles/index.json", '{"task_bundles": {"2": {}}}\n')
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
    assert "proofs/sn467/accepted/epoch-000002.jsonl" in changed
    assert "graph/sn467/roots/index.json" in changed
    assert "tasks/sn467/bundles/index.json" in changed
    assert "scratch.txt" not in changed


def test_sync_public_inputs_copies_only_publishable_live_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    live = tmp_path / "live"
    existing_sha = "c" * 64
    _write(repo / f"tasks/sn467/registries/{existing_sha}.json", '{"schema_version": 1, "tasks": []}\n')
    _write(
        repo / "tasks/sn467/registries/index.json",
        json.dumps(
            {
                "schema_version": 1,
                "netuid": "sn467",
                "registries": {"19956": {"path": f"{existing_sha}.json", "sha256": existing_sha}},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    _write(live / "proofs/epoch-000041.jsonl", '{"row": 41}\n')
    _write(live / "proofs/epoch-local.jsonl", '{"local": true}\n')
    _write(live / "canonical/tempos/tempo-019958/manifest.json", '{"tempo": 19958}\n')
    conflicting_sha = "d" * 64
    _write(
        live / "registries/tempo-19956.registry.json",
        f'{{"schema_version": 1, "sha256": "{conflicting_sha}", "tasks": []}}\n',
    )
    embedded_sha = "a" * 64
    registry = f'{{"schema_version": 1, "sha256": "{embedded_sha}", "tasks": []}}\n'
    registry_sha = hashlib.sha256(registry.encode()).hexdigest()
    _write(
        live / "registries/tempo-19958.registry.json",
        registry,
    )
    legacy_registry = '{"schema_version": 1, "tasks": []}\n'
    _write(live / "registries/tempo-19957.registry.json", legacy_registry)

    counts = sync_public_inputs(
        repo,
        "sn467",
        proof_dir=live / "proofs",
        canonical_dir=live / "canonical",
        registry_cache_dir=live / "registries",
    )

    assert counts == {
        "proof_files": 1,
        "canonical_files": 1,
        "registry_files": 2,
        "graph_root_files": 0,
        "task_bundle_files": 0,
    }
    assert (repo / "proofs/sn467/accepted/epoch-000041.jsonl").read_text(encoding="utf-8") == '{"row": 41}\n'
    assert not (repo / "proofs/sn467/accepted/epoch-local.jsonl").exists()
    assert (repo / "canonical/sn467/tempos/tempo-019958/manifest.json").exists()
    assert not (repo / f"tasks/sn467/registries/{conflicting_sha}.json").exists()
    assert (repo / f"tasks/sn467/registries/{registry_sha}.json").exists()
    assert (repo / f"tasks/sn467/registries/{hashlib.sha256(legacy_registry.encode()).hexdigest()}.json").exists()
    index = json.loads((repo / "tasks/sn467/registries/index.json").read_text(encoding="utf-8"))
    assert index["registries"]["19956"] == {"path": f"{existing_sha}.json", "sha256": existing_sha}
    assert index["registries"]["19958"] == {"path": f"{registry_sha}.json", "sha256": registry_sha}


def test_sync_public_inputs_copies_graph_root_by_manifest_hash(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    root = tmp_path / "ingredients"
    _write_selection_ingredient_repo(root)
    manifest = ingredient_manifest_from_root(root, lemma_corpus_snapshot_sha256="f" * 64)
    (root / "manifest.json").write_bytes(ingredient_manifest_bytes(manifest))
    _write(root / "operator-note.txt", "private scratch\n")
    manifest_sha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()

    counts = sync_public_inputs(repo, "sn467", graph_root_dirs=(root,))

    assert counts == {
        "proof_files": 0,
        "canonical_files": 0,
        "registry_files": 0,
        "graph_root_files": 22,
        "task_bundle_files": 0,
    }
    target = repo / f"graph/sn467/roots/{manifest_sha}"
    assert (target / "manifest.json").exists()
    assert (target / "ingredients/definitions.jsonl").exists()
    assert (target / "recipes/soundness_templates/list_length.lean").exists()
    assert not (target / "operator-note.txt").exists()
    index = json.loads((repo / "graph/sn467/roots/index.json").read_text(encoding="utf-8"))
    row = index["graph_roots"][manifest_sha]
    assert row["ingredient_manifest_sha256"] == manifest_sha
    assert row["recipe_bundle_sha256"] == manifest.recipe_bundle_sha256
    assert row["path"] == f"{manifest_sha}/manifest.json"


def test_sync_public_inputs_rejects_noncanonical_graph_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    root = tmp_path / "ingredients"
    _write_selection_ingredient_repo(root)
    manifest = ingredient_manifest_from_root(root, lemma_corpus_snapshot_sha256="f" * 64)
    (root / "manifest.json").write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n")

    with pytest.raises(SystemExit, match="graph manifest noncanonical"):
        sync_public_inputs(repo, "sn467", graph_root_dirs=(root,))


def test_sync_public_inputs_copies_task_bundle_by_manifest_hash(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    live = tmp_path / "live"
    challenge = live / "challenge"
    _write_ingredient_task_bundle(challenge)
    _write(challenge / "operator-note.txt", "private scratch\n")
    manifest_sha = hashlib.sha256((challenge / "artifact-manifest.json").read_bytes()).hexdigest()

    counts = sync_public_inputs(repo, "sn467", task_bundle_dirs=(challenge,))

    assert counts == {
        "proof_files": 0,
        "canonical_files": 0,
        "registry_files": 0,
        "graph_root_files": 0,
        "task_bundle_files": 8,
    }
    target = repo / f"tasks/sn467/bundles/{manifest_sha}"
    assert (target / "artifact-manifest.json").exists()
    assert (target / "task.json").exists()
    assert not (target / "operator-note.txt").exists()
    index = json.loads((repo / "tasks/sn467/bundles/index.json").read_text(encoding="utf-8"))
    row = index["task_bundles"]["19958"]
    assert row["artifact_manifest_sha256"] == manifest_sha
    assert row["active_task_id"] == "lemma.ingredient.list_length"
    assert row["selected_recipe_id"] == "list_length_v1"
    assert row["path"] == f"{manifest_sha}/artifact-manifest.json"


def test_registry_cache_only_skips_snapshot_artifacts(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: ANN001
    repo = tmp_path / "lemma-proof-atlas"
    live = tmp_path / "registries"
    embedded_sha = "b" * 64
    registry = f'{{"schema_version": 1, "sha256": "{embedded_sha}", "tasks": []}}\n'
    registry_sha = hashlib.sha256(registry.encode()).hexdigest()
    _write(
        live / "tempo-19987.registry.json",
        registry,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_proof_atlas_snapshot.py",
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
    assert output["synced"] == {
        "proof_files": 0,
        "canonical_files": 0,
        "registry_files": 1,
        "graph_root_files": 0,
        "task_bundle_files": 0,
    }
    assert output["repo_committed"] is False
    assert (repo / f"tasks/sn467/registries/{registry_sha}.json").exists()
    index = repo / "tasks/sn467/registries/index.json"
    current_index = repo / "tasks/sn467/registries/current-index.json"
    assert index.exists()
    assert current_index.read_bytes() == index.read_bytes()
    assert not (repo / "MANIFEST.sha256").exists()
    assert not (repo / "canonical/sn467/storage-index.json").exists()


def test_publish_commits_only_after_canonical_publish_steps_succeed(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: ANN001
    repo = tmp_path / "lemma-proof-atlas"

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")

    run_calls: list[tuple[str, ...]] = []
    committed = []

    def fake_run(
        cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> None:  # noqa: ANN001
        run_calls.append(tuple(cmd))
        if cmd[0] == "aws":
            raise SystemExit(99)

    def fake_commit(*_args, **_kwargs) -> bool:
        committed.append(True)
        return True

    def fake_sync(*_args, **_kwargs):
        return {"proof_files": 1, "canonical_files": 1, "registry_files": 1}

    def fake_prepare(*_args, **_kwargs):
        return {"proof_rows": 1}

    def fake_build_storage_index(*_args, **_kwargs):
        return {"epochs": [], "path": str(repo / "canonical/sn467/storage-index.json")}

    def fake_write_manifest(*_args, **_kwargs):
        return repo / "MANIFEST.sha256"

    def fake_aws_command(_value: str | None) -> list[str]:
        return ["aws"]

    def fake_hf_command(_value: str | None) -> list[str]:
        return ["hf"]

    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.run", fake_run)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.commit_repo_changes", fake_commit)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.sync_public_inputs", fake_sync)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.prepare", fake_prepare)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.build_storage_index", fake_build_storage_index)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.write_manifest", fake_write_manifest)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.aws_command", fake_aws_command)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.hf_command", fake_hf_command)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_proof_atlas_snapshot.py",
            "--repo",
            str(repo),
            "--netuid",
            "sn467",
            "--commit-repo",
            "--skip-github",
            "--skip-huggingface",
        ],
    )

    with pytest.raises(SystemExit):
        main()

    assert committed == []
    assert any(command[0] == "aws" for command in run_calls)
    output = capsys.readouterr().out
    assert output


def test_publish_commits_after_successful_publish_steps(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: ANN001
    repo = tmp_path / "lemma-proof-atlas"

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")

    trace: list[str] = []

    def fake_run(
        cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> None:  # noqa: ANN001
        trace.append("run:" + cmd[0])

    def fake_commit(*_args, **_kwargs) -> bool:
        trace.append("commit")
        return True

    def fake_sync(*_args, **_kwargs):
        return {"proof_files": 1, "canonical_files": 1, "registry_files": 1}

    def fake_prepare(*_args, **_kwargs):
        return {"proof_rows": 1}

    def fake_build_storage_index(*_args, **_kwargs):
        return {"epochs": [], "path": str(repo / "canonical/sn467/storage-index.json")}

    def fake_write_manifest(*_args, **_kwargs):
        return repo / "MANIFEST.sha256"

    def fake_aws_command(_value: str | None) -> list[str]:
        return ["aws"]

    def fake_hf_command(_value: str | None) -> list[str]:
        return ["hf"]

    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.run", fake_run)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.commit_repo_changes", fake_commit)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.sync_public_inputs", fake_sync)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.prepare", fake_prepare)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.build_storage_index", fake_build_storage_index)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.write_manifest", fake_write_manifest)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.aws_command", fake_aws_command)
    monkeypatch.setattr("scripts.publish_proof_atlas_snapshot.hf_command", fake_hf_command)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_proof_atlas_snapshot.py",
            "--repo",
            str(repo),
            "--netuid",
            "sn467",
            "--commit-repo",
            "--skip-github",
            "--skip-huggingface",
        ],
    )

    assert main() == 0

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["repo_committed"] is True
    assert "run:aws" in trace
    assert trace[-1] == "commit"

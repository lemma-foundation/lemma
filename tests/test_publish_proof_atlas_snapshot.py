from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from lemma.corpus import build_corpus_row, write_jsonl
from lemma.lean.sandbox import VerifyResult
from lemma.submissions import build_submission
from lemma.task_supply import make_task
from lemma.tasks import SourceRef
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


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _proof() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem smoke_true : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def _write_accepted_proof_row(repo: Path) -> None:
    task = make_task(
        task_id="lemma.sn467.true_test",
        title="Smoke true",
        theorem_name="smoke_true",
        type_expr="True",
        source_stream="sorrydb",
        source_name="pytest",
        source_license="Apache-2.0",
    ).model_copy(
        update={
            "source_ref": SourceRef(
                kind="sorrydb",
                name="pytest",
                url="https://example.test/repo",
                commit="abc123",
                path="Smoke.lean",
            )
        }
    )
    submission = build_submission(task, solver_hotkey="miner-test", proof_script=_proof())
    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok", proof_term_hash="c" * 64),
        validator_hotkey="validator-test",
        rewarded=True,
    )
    write_jsonl([row], repo / "proofs" / "sn467" / "accepted" / "epoch-000001.jsonl")


def _manifest_files() -> dict[str, str]:
    return {
        "tasks/sn467/registries/registry.json": "{}\n",
        "proofs/sn467/accepted/epoch-000001.jsonl": '{"row": 1}\n',
        "proofs/sn467/index.json": '{"rows": 1}\n',
        "exports/sn467/lemma-proofs.jsonl": '{"proof": true}\n',
        "canonical/sn467/storage-index.json": '{"epochs": []}\n',
    }


def test_write_manifest_hashes_public_paths_without_local_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    files = _manifest_files()
    for relative, text in files.items():
        _write(repo / relative, text)

    manifest = write_manifest(repo, "sn467")
    lines = manifest.read_text(encoding="utf-8").splitlines()

    assert lines == [
        f"{hashlib.sha256(files[relative].encode()).hexdigest()}  {relative}"
        for relative in sorted(files)
    ]
    assert str(tmp_path) not in manifest.read_text(encoding="utf-8")


def test_publish_dry_run_prepares_accepted_proof_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    repo = tmp_path / "lemma-proof-atlas"
    _write(repo / "README.md", "- accepted proof rows: `0`\n")
    _write(
        repo / "ATLAS_CARD.md",
        "The checked-in artifact set contains 0 accepted Lean proof rows,\n"
        "The validator accepted all 0 proofs with the pinned Lean verifier.\n",
    )
    _write_accepted_proof_row(repo)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_proof_atlas_snapshot.py",
            "--repo",
            str(repo),
            "--netuid",
            "sn467",
            "--snapshot",
            "2026-06-02T00-00-00Z",
            "--dry-run",
            "--skip-hippius",
            "--skip-github",
            "--skip-huggingface",
        ],
    )

    assert main() == 0

    payload = json.loads(capsys.readouterr().out)
    manifest = (repo / "MANIFEST.sha256").read_text(encoding="utf-8")
    proof_row = json.loads((repo / "proofs/sn467/accepted/epoch-000001.jsonl").read_text(encoding="utf-8"))
    assert payload["proof_rows"] == 1
    assert payload["storage_epochs"] == 1
    assert proof_row["rewarded"] is True
    assert proof_row["source_ref"]["kind"] == "sorrydb"
    assert "proofs/sn467/accepted/epoch-000001.jsonl" in manifest
    assert (repo / "canonical/sn467/storage-index.json").exists()


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
    assert "s3://lemma-proof-atlas-sn467/snapshots/2026-05-20T02-32-08Z/tasks/sn467/registries/" in flattened
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


def test_commit_repo_changes_stages_only_public_atlas_paths(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-proof-atlas"
    _write(repo / "README.md", "- accepted proof rows: `0`\n")
    _write(repo / "ATLAS_CARD.md", "dataset\n")
    for relative, text in {**_manifest_files(), "MANIFEST.sha256": "hash  file\n"}.items():
        _write(repo / relative, text)
    _write(repo / "scratch.txt", "private local scratch\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", *public_repo_paths("sn467")], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    _write(repo / "proofs/sn467/accepted/epoch-000002.jsonl", '{"row": 2}\n')
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
    embedded_sha = "a" * 64
    registry = f'{{"schema_version": 1, "sha256": "{embedded_sha}", "tasks": []}}\n'
    registry_sha = hashlib.sha256(registry.encode()).hexdigest()
    _write(live / "registries/tempo-19958.registry.json", registry)

    counts = sync_public_inputs(
        repo,
        "sn467",
        proof_dir=live / "proofs",
        canonical_dir=live / "canonical",
        registry_cache_dir=live / "registries",
    )

    assert counts == {"proof_files": 1, "canonical_files": 1, "registry_files": 1}
    assert (repo / "proofs/sn467/accepted/epoch-000041.jsonl").read_text(encoding="utf-8") == '{"row": 41}\n'
    assert not (repo / "proofs/sn467/accepted/epoch-local.jsonl").exists()
    assert (repo / "canonical/sn467/tempos/tempo-019958/manifest.json").exists()
    assert (repo / f"tasks/sn467/registries/{registry_sha}.json").exists()
    index = json.loads((repo / "tasks/sn467/registries/index.json").read_text(encoding="utf-8"))
    assert index["registries"]["19956"] == {"path": f"{existing_sha}.json", "sha256": existing_sha}
    assert index["registries"]["19958"] == {"path": f"{registry_sha}.json", "sha256": registry_sha}


def test_registry_cache_only_skips_snapshot_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    repo = tmp_path / "lemma-proof-atlas"
    live = tmp_path / "registries"
    embedded_sha = "b" * 64
    registry = f'{{"schema_version": 1, "sha256": "{embedded_sha}", "tasks": []}}\n'
    registry_sha = hashlib.sha256(registry.encode()).hexdigest()
    _write(live / "tempo-19987.registry.json", registry)
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
    assert output["synced"] == {"proof_files": 0, "canonical_files": 0, "registry_files": 1}
    assert output["repo_committed"] is False
    assert (repo / f"tasks/sn467/registries/{registry_sha}.json").exists()
    index = repo / "tasks/sn467/registries/index.json"
    current_index = repo / "tasks/sn467/registries/current-index.json"
    assert index.exists()
    assert current_index.read_bytes() == index.read_bytes()
    assert not (repo / "MANIFEST.sha256").exists()

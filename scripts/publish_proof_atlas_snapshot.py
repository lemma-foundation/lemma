#!/usr/bin/env python3
"""Publish a Lemma Proof Atlas snapshot to Hippius and GitHub Releases."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lemma.corpus.storage import build_storage_index  # noqa: E402
from lemma.supply.ingredients import (  # noqa: E402
    INGREDIENT_MANIFEST_COMPONENT_PATHS,
    INGREDIENT_RECIPE_ARTIFACT_PATHS,
    INGREDIENT_REPOSITORY_REPORT_PATHS,
    IngredientManifest,
    IngredientTaskArtifactManifest,
    ingredient_manifest_bytes,
    ingredient_manifest_component_hashes,
    ingredient_manifest_component_schema_counts,
    ingredient_recipe_artifact_hashes,
    ingredient_repository_report_hashes,
    ingredient_root_mathlib_commit,
)
from lemma.tasks import load_task_registry  # noqa: E402
from scripts.prepare_proof_atlas_publish import prepare  # noqa: E402

DEFAULT_BUCKET = "lemma-proof-atlas-sn467"
DEFAULT_ENDPOINT = "https://s3.hippius.com"
DEFAULT_GITHUB_REPO = "lemma-foundation/lemma-proof-atlas"
DEFAULT_REGION = "decentralized"
LEAK_PATTERN = re.compile(
    "AGENT" + r"[_ ]STATE|Agent " + "State|\\." + "env|" + "/" + "Users/|root" + "@|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|BEGIN (?:RSA|OPENSSH|PRIVATE)|"
    "api[_-]?key|to" + "ken|mne" + "monic|sec" + "ret|s" + "sh",
    re.IGNORECASE,
)
EPOCH_FILE_RE = re.compile(r"epoch-\d{6}\.jsonl$")
NETUID_RE = re.compile(r"(?:sn)?(\d+)")


def snapshot_id(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return current.strftime("%Y-%m-%dT%H-%M-%SZ")


def snapshot_label(snapshot: str) -> str:
    parsed = datetime.strptime(snapshot, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def public_dirs(repo: Path, netuid: str) -> tuple[tuple[str, Path], ...]:
    return (
        (f"proofs/{netuid}", repo / "proofs" / netuid),
        (f"tasks/{netuid}/registries", repo / "tasks" / netuid / "registries"),
        (f"tasks/{netuid}/bundles", repo / "tasks" / netuid / "bundles"),
        (f"graph/{netuid}/roots", repo / "graph" / netuid / "roots"),
        ("graph/mathlib", repo / "graph" / "mathlib"),
        ("generation", repo / "generation"),
        (f"exports/{netuid}", repo / "exports" / netuid),
        (f"canonical/{netuid}", repo / "canonical" / netuid),
    )


def write_manifest(repo: Path, netuid: str, manifest_path: Path | None = None) -> Path:
    target = manifest_path or repo / "MANIFEST.sha256"
    paths: list[Path] = []
    for _name, directory in public_dirs(repo, netuid):
        if not directory.is_dir():
            raise SystemExit(f"missing public Proof Atlas directory: {directory}")
        paths.extend(item for item in directory.rglob("*") if item.is_file())
    lines: list[str] = []
    for path in sorted(paths, key=lambda item: item.relative_to(repo).as_posix()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(repo).as_posix()
        lines.append(f"{digest}  {relative}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _copy_tree_contents(source: Path, target: Path) -> int:
    copied = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied += 1
    return copied


def _netuid_number(netuid: str) -> int | None:
    match = NETUID_RE.fullmatch(netuid)
    return int(match.group(1)) if match else None


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"{label} path invalid: {path}")


def _copy_task_bundle(source: Path, target: Path, netuid: str) -> tuple[int, dict[str, object]]:
    if source.is_symlink() or not source.is_dir():
        raise SystemExit(f"task bundle directory invalid: {source}")
    manifest_path = source / "artifact-manifest.json"
    _regular_file(manifest_path, "task artifact manifest")
    raw_manifest = manifest_path.read_bytes()
    manifest = IngredientTaskArtifactManifest.model_validate_json(raw_manifest)
    expected_netuid = _netuid_number(netuid)
    if expected_netuid is not None and manifest.netuid != expected_netuid:
        raise SystemExit("task bundle netuid mismatch")

    artifact_manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    bundle_target = target / artifact_manifest_sha256
    bundle_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, bundle_target / "artifact-manifest.json")
    copied = 1
    for ref in manifest.artifacts.model_dump(mode="json").values():
        path = source / ref["path"]
        _regular_file(path, "task bundle artifact")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise SystemExit(f"task bundle artifact sha256 mismatch: {ref['path']}")
        shutil.copy2(path, bundle_target / ref["path"])
        copied += 1

    row = {
        "active_registry_sha256": manifest.artifacts.active_registry.sha256,
        "active_task_id": manifest.active_task_id,
        "artifact_manifest_sha256": artifact_manifest_sha256,
        "bundle_path": f"{artifact_manifest_sha256}/",
        "challenge_seed_sha256": manifest.challenge_seed_sha256,
        "difficulty_lane": manifest.difficulty_lane,
        "generation_receipt_sha256": manifest.generation_receipt_sha256,
        "ingredient_manifest_sha256": manifest.ingredient_manifest_sha256,
        "path": f"{artifact_manifest_sha256}/artifact-manifest.json",
        "selected_recipe_id": manifest.selected_recipe_id,
        "tempo": manifest.tempo,
    }
    return copied, row


def _copy_public_relative_file(source: Path, target: Path, relative_path: str) -> int:
    path = source / relative_path
    _regular_file(path, "graph root artifact")
    destination = target / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return 1


def _copy_graph_root(source: Path, target: Path) -> tuple[int, dict[str, object]]:
    if source.is_symlink() or not source.is_dir():
        raise SystemExit(f"graph root directory invalid: {source}")
    manifest_path = source / "manifest.json"
    _regular_file(manifest_path, "graph manifest")
    raw_manifest = manifest_path.read_bytes()
    manifest = IngredientManifest.model_validate_json(raw_manifest)
    if raw_manifest != ingredient_manifest_bytes(manifest):
        raise SystemExit("graph manifest noncanonical")
    if ingredient_root_mathlib_commit(source) != manifest.mathlib_commit:
        raise SystemExit("graph root mathlib commit mismatch")
    component_hashes = ingredient_manifest_component_hashes(source)
    for field, expected in manifest.model_dump(mode="json").items():
        if field.endswith("_sha256") and field in component_hashes and component_hashes[field] != expected:
            raise SystemExit(f"graph manifest component sha256 mismatch: {field}")
    counts = ingredient_manifest_component_schema_counts(source, mathlib_commit=manifest.mathlib_commit)
    ingredient_repository_report_hashes(
        source,
        component_schema_counts=counts,
        mathlib_commit=manifest.mathlib_commit,
    )
    ingredient_recipe_artifact_hashes(source)

    ingredient_manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    root_target = target / ingredient_manifest_sha256
    root_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, root_target / "manifest.json")
    copied = 1
    copied += _copy_public_relative_file(source, root_target, "mathlib_commit.txt")
    for relative_path in INGREDIENT_MANIFEST_COMPONENT_PATHS.values():
        copied += _copy_public_relative_file(source, root_target, relative_path)
    for relative_path in INGREDIENT_REPOSITORY_REPORT_PATHS.values():
        copied += _copy_public_relative_file(source, root_target, relative_path)
    for relative_path in INGREDIENT_RECIPE_ARTIFACT_PATHS.values():
        copied += _copy_public_relative_file(source, root_target, relative_path)
    template_dir = source / "recipes" / "soundness_templates"
    for path in sorted(template_dir.glob("*.lean")):
        _regular_file(path, "graph soundness template")
        relative_path = path.relative_to(source).as_posix()
        copied += _copy_public_relative_file(source, root_target, relative_path)

    row = {
        "ingredient_manifest_sha256": ingredient_manifest_sha256,
        "lemma_corpus_snapshot_sha256": manifest.lemma_corpus_snapshot_sha256,
        "mathlib_commit": manifest.mathlib_commit,
        "path": f"{ingredient_manifest_sha256}/manifest.json",
        "recipe_bundle_sha256": manifest.recipe_bundle_sha256,
    }
    return copied, row


def sync_public_inputs(
    repo: Path,
    netuid: str,
    *,
    proof_dir: Path | None = None,
    canonical_dir: Path | None = None,
    registry_cache_dir: Path | None = None,
    graph_root_dirs: tuple[Path, ...] = (),
    task_bundle_dirs: tuple[Path, ...] = (),
) -> dict[str, int]:
    for _name, directory in public_dirs(repo, netuid):
        directory.mkdir(parents=True, exist_ok=True)
    counts = {
        "proof_files": 0,
        "canonical_files": 0,
        "registry_files": 0,
        "graph_root_files": 0,
        "task_bundle_files": 0,
    }
    if proof_dir is not None:
        target = repo / "proofs" / netuid / "accepted"
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(proof_dir.glob("epoch-*.jsonl")):
            if path.is_file() and EPOCH_FILE_RE.fullmatch(path.name):
                shutil.copy2(path, target / path.name)
                counts["proof_files"] += 1
    if canonical_dir is not None:
        target = repo / "canonical" / netuid
        target.mkdir(parents=True, exist_ok=True)
        counts["canonical_files"] = _copy_tree_contents(canonical_dir, target)
    if registry_cache_dir is not None:
        target = repo / "tasks" / netuid / "registries"
        target.mkdir(parents=True, exist_ok=True)
        registries: dict[str, object] = {}
        index_path = target / "index.json"
        if index_path.is_file():
            existing_index = json.loads(index_path.read_text(encoding="utf-8"))
            existing_registries = existing_index.get("registries") if isinstance(existing_index, dict) else None
            if isinstance(existing_registries, dict):
                registries.update(existing_registries)
        registry_index: dict[str, object] = {"schema_version": 1, "netuid": netuid, "registries": registries}
        for path in sorted(registry_cache_dir.glob("tempo-*.registry.json")):
            if not path.is_file():
                continue
            raw = path.read_bytes()
            sha256 = load_task_registry(raw).sha256
            filename = f"{sha256}.json"
            if match := re.fullmatch(r"tempo-(\d+)\.registry\.json", path.name):
                tempo = match.group(1)
                existing = registries.get(tempo)
                if isinstance(existing, dict) and existing.get("sha256") != sha256:
                    continue
                registries[tempo] = {"sha256": sha256, "path": filename}
            shutil.copy2(path, target / filename)
            counts["registry_files"] += 1
        index_path.write_text(
            json.dumps(registry_index, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    if graph_root_dirs:
        target = repo / "graph" / netuid / "roots"
        index_path = target / "index.json"
        graph_roots: dict[str, object] = {}
        if index_path.is_file():
            existing_index = json.loads(index_path.read_text(encoding="utf-8"))
            existing_roots = existing_index.get("graph_roots") if isinstance(existing_index, dict) else None
            if isinstance(existing_roots, dict):
                graph_roots.update(existing_roots)
        for graph_root in graph_root_dirs:
            copied, row = _copy_graph_root(graph_root, target)
            ingredient_manifest_sha256 = str(row["ingredient_manifest_sha256"])
            graph_roots.setdefault(ingredient_manifest_sha256, row)
            counts["graph_root_files"] += copied
        index_path.write_text(
            json.dumps(
                {"schema_version": 1, "netuid": netuid, "graph_roots": graph_roots},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    if task_bundle_dirs:
        target = repo / "tasks" / netuid / "bundles"
        index_path = target / "index.json"
        bundles: dict[str, object] = {}
        if index_path.is_file():
            existing_index = json.loads(index_path.read_text(encoding="utf-8"))
            existing_bundles = existing_index.get("task_bundles") if isinstance(existing_index, dict) else None
            if isinstance(existing_bundles, dict):
                bundles.update(existing_bundles)
        for bundle_dir in task_bundle_dirs:
            copied, row = _copy_task_bundle(bundle_dir, target, netuid)
            tempo = str(row["tempo"])
            existing = bundles.get(tempo)
            if (
                isinstance(existing, dict)
                and existing.get("artifact_manifest_sha256") != row["artifact_manifest_sha256"]
            ):
                continue
            bundles[tempo] = row
            counts["task_bundle_files"] += copied
        index_path.write_text(
            json.dumps(
                {"schema_version": 1, "netuid": netuid, "task_bundles": bundles},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    return counts


def aws_command(value: str | None) -> list[str]:
    if value:
        return shlex.split(value)
    if aws := shutil.which("aws"):
        return [aws]
    if uvx := shutil.which("uvx"):
        return [uvx, "--from", "awscli", "aws"]
    raise SystemExit("missing aws CLI; install awscli or uv, or pass --aws-command")


def hf_command(value: str | None) -> list[str]:
    if value:
        return shlex.split(value)
    if hf := shutil.which("hf"):
        return [hf]
    if huggingface_cli := shutil.which("huggingface-cli"):
        return [huggingface_cli]
    if uvx := shutil.which("uvx"):
        return [uvx, "--from", "huggingface_hub", "hf"]
    raise SystemExit("missing Hugging Face CLI; install huggingface_hub or uv, or pass --hf-command")


def hippius_commands(
    *,
    aws: list[str],
    repo: Path,
    bucket: str,
    endpoint_url: str,
    netuid: str,
    snapshot: str,
    manifest_path: Path,
) -> list[list[str]]:
    base_uri = f"s3://{bucket}/snapshots/{snapshot}"
    commands: list[list[str]] = []
    for remote_prefix, directory in public_dirs(repo, netuid):
        commands.append(
            [
                *aws,
                "s3",
                "sync",
                str(directory),
                f"{base_uri}/{remote_prefix}/",
                "--endpoint-url",
                endpoint_url,
                "--only-show-errors",
            ]
        )
    commands.append(
        [
            *aws,
            "s3",
            "cp",
            str(manifest_path),
            f"{base_uri}/MANIFEST.sha256",
            "--endpoint-url",
            endpoint_url,
            "--only-show-errors",
        ]
    )
    return commands


def huggingface_commands(
    *,
    hf: list[str],
    hf_repo_id: str,
    repo: Path,
    netuid: str,
    snapshot: str,
    manifest_path: Path,
    storage_index_path: Path,
) -> list[list[str]]:
    prefix = f"snapshots/{snapshot}"
    message = f"Publish {netuid} Proof Atlas snapshot {snapshot}"
    return [
        [
            *hf,
            "upload",
            hf_repo_id,
            str(repo / "canonical" / netuid),
            f"{prefix}/canonical/{netuid}",
            "--repo-type",
            "dataset",
            "--commit-message",
            message,
        ],
        [
            *hf,
            "upload",
            hf_repo_id,
            str(repo / "exports" / netuid),
            f"{prefix}/exports/{netuid}",
            "--repo-type",
            "dataset",
            "--commit-message",
            message,
        ],
        [
            *hf,
            "upload",
            hf_repo_id,
            str(manifest_path),
            f"{prefix}/MANIFEST.sha256",
            "--repo-type",
            "dataset",
            "--commit-message",
            message,
        ],
        [
            *hf,
            "upload",
            hf_repo_id,
            str(storage_index_path),
            f"{prefix}/storage-index.json",
            "--repo-type",
            "dataset",
            "--commit-message",
            message,
        ],
    ]


def release_notes(*, bucket: str, netuid: str, snapshot: str) -> str:
    return "\n".join(
        [
            f"{netuid.upper()} Proof Atlas snapshot published to Hippius.",
            "",
            "Canonical Hippius bucket:",
            "",
            f"`s3://{bucket}/snapshots/{snapshot}/`",
            "",
            "Contents:",
            "",
            f"- `proofs/{netuid}/`",
            f"- `tasks/{netuid}/registries/`",
            f"- `tasks/{netuid}/bundles/`",
            f"- `graph/{netuid}/roots/`",
            "- `graph/mathlib/`",
            "- `generation/`",
            f"- `exports/{netuid}/`",
            f"- `canonical/{netuid}/`",
            "- `MANIFEST.sha256`",
            "",
            "This GitHub release is an immutable public mirror. Hippius is the canonical storage location.",
        ]
    )


def github_release_command(
    *,
    github_repo: str,
    manifest_path: Path,
    storage_index_path: Path,
    netuid: str,
    snapshot: str,
    bucket: str,
) -> list[str]:
    tag = f"{netuid}-{snapshot}"
    title = f"{netuid.upper()} Proof Atlas snapshot {snapshot_label(snapshot)}"
    return [
        "gh",
        "release",
        "create",
        tag,
        str(manifest_path),
        str(storage_index_path),
        "--repo",
        github_repo,
        "--target",
        "main",
        "--title",
        title,
        "--notes",
        release_notes(bucket=bucket, netuid=netuid, snapshot=snapshot),
    ]


def public_repo_paths(netuid: str) -> tuple[str, ...]:
    return (
        "README.md",
        "ATLAS_CARD.md",
        "MANIFEST.sha256",
        f"proofs/{netuid}",
        f"tasks/{netuid}",
        f"graph/{netuid}",
        "graph/mathlib",
        "generation",
        f"exports/{netuid}",
        f"canonical/{netuid}",
    )


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        capture_output=True,
    )


def _git_push(repo: Path) -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        _git(repo, "push")
        return
    env = os.environ.copy()
    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {header}",
        }
    )
    subprocess.run(["git", "push"], cwd=repo, check=True, env=env)  # noqa: S603, S607


def _assert_public_staged_diff(repo: Path) -> None:
    diff = _git(repo, "diff", "--cached").stdout
    if match := LEAK_PATTERN.search(diff):
        raise SystemExit(f"staged Proof Atlas diff matched leak pattern: {match.group(0)}")


def commit_repo_changes(
    repo: Path,
    *,
    netuid: str,
    snapshot: str,
    push: bool,
    dry_run: bool,
) -> bool:
    paths = public_repo_paths(netuid)
    if dry_run:
        print("$ " + shlex.join(["git", "-C", str(repo), "add", "--", *paths]))
        commit_command = ["git", "-C", str(repo), "commit", "-m", f"Publish {netuid} Proof Atlas snapshot {snapshot}"]
        print("$ " + shlex.join(commit_command))
        if push:
            print("$ " + shlex.join(["git", "-C", str(repo), "push"]))
        return False

    staged_before = _git(repo, "diff", "--cached", "--name-only").stdout.splitlines()
    if staged_before:
        raise SystemExit(f"Proof Atlas repo already has staged changes: {', '.join(staged_before)}")

    _git(repo, "add", "--", *paths)
    staged = _git(repo, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        return False
    _assert_public_staged_diff(repo)
    _git(repo, "commit", "-m", f"Publish {netuid} Proof Atlas snapshot {snapshot}")
    if push:
        _git_push(repo)
    return True


def run(cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    if dry_run:
        return
    subprocess.run(cmd, check=True, env=env, cwd=cwd)  # noqa: S603


def require_env(names: tuple[str, ...]) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"missing required environment variable(s): {joined}")


def execute(cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    print("$ " + shlex.join(cmd))
    run(cmd, dry_run=dry_run, env=env, cwd=cwd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Proof Atlas checkout to publish")
    parser.add_argument("--netuid", default="sn467", help="Proof Atlas namespace, for example sn467")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Hippius S3 bucket")
    parser.add_argument("--endpoint-url", default=DEFAULT_ENDPOINT, help="Hippius S3 endpoint URL")
    parser.add_argument("--region", default=DEFAULT_REGION, help="S3 region value")
    parser.add_argument("--resolver", default="hippius-s3-arion", help="canonical resolver label")
    parser.add_argument("--snapshot", default=snapshot_id(), help="snapshot id, default is current UTC time")
    parser.add_argument("--github-repo", default=DEFAULT_GITHUB_REPO, help="GitHub repo for the immutable mirror")
    parser.add_argument("--aws-command", help='AWS CLI command, default: "aws" or "uvx --from awscli aws"')
    parser.add_argument(
        "--hf-command",
        help='Hugging Face CLI command, default: "hf" or "uvx --from huggingface_hub hf"',
    )
    parser.add_argument("--hf-repo-id", default=os.environ.get("HF_REPO_ID"), help="Hugging Face dataset repo id")
    parser.add_argument("--pull", action="store_true", help="run git pull --ff-only in the Proof Atlas repo first")
    parser.add_argument("--sync-proof-dir", type=Path, help="copy public accepted proof JSONL files into the atlas")
    parser.add_argument("--sync-canonical-dir", type=Path, help="copy public canonical artifacts into the atlas")
    parser.add_argument(
        "--sync-registry-cache-dir",
        type=Path,
        help="copy tempo registry cache files into the atlas by registry hash",
    )
    parser.add_argument(
        "--sync-task-bundle-dir",
        type=Path,
        action="append",
        default=[],
        help="copy a built task bundle into the atlas",
    )
    parser.add_argument(
        "--sync-graph-root-dir",
        type=Path,
        action="append",
        default=[],
        help="copy a public generated graph root into the atlas",
    )
    parser.add_argument(
        "--registry-cache-only",
        action="store_true",
        help="publish only synced registry cache files and their public index",
    )
    parser.add_argument("--skip-hippius", action="store_true", help="prepare files but do not upload to Hippius")
    parser.add_argument("--skip-github", action="store_true", help="prepare files but do not create the GitHub release")
    parser.add_argument(
        "--skip-huggingface",
        action="store_true",
        help="prepare files but do not upload the Hugging Face mirror",
    )
    parser.add_argument("--dry-run", action="store_true", help="print commands without running upload/release steps")
    parser.add_argument("--commit-repo", action="store_true", help="Commit prepared Proof Atlas files")
    parser.add_argument("--push-repo", action="store_true", help="Push prepared Proof Atlas changes after committing")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    if args.pull:
        execute(["git", "-C", str(repo), "pull", "--ff-only"], dry_run=args.dry_run)

    if args.registry_cache_only:
        if args.sync_registry_cache_dir is None:
            raise SystemExit("--registry-cache-only requires --sync-registry-cache-dir")
        synced = sync_public_inputs(
            repo,
            args.netuid,
            registry_cache_dir=args.sync_registry_cache_dir.resolve(),
        )
        committed_repo = (
            commit_repo_changes(
                repo,
                netuid=args.netuid,
                snapshot=args.snapshot,
                push=args.push_repo,
                dry_run=args.dry_run,
            )
            if args.commit_repo or args.push_repo
            else False
        )
        print(
            json.dumps(
                {
                    "netuid": args.netuid,
                    "repo_committed": committed_repo,
                    "repo_pushed": bool(committed_repo and args.push_repo),
                    "synced": synced,
                },
                sort_keys=True,
            )
        )
        return 0

    synced = sync_public_inputs(
        repo,
        args.netuid,
        proof_dir=args.sync_proof_dir.resolve() if args.sync_proof_dir else None,
        canonical_dir=args.sync_canonical_dir.resolve() if args.sync_canonical_dir else None,
        registry_cache_dir=args.sync_registry_cache_dir.resolve() if args.sync_registry_cache_dir else None,
        graph_root_dirs=tuple(path.resolve() for path in args.sync_graph_root_dir),
        task_bundle_dirs=tuple(path.resolve() for path in args.sync_task_bundle_dir),
    )
    summary = prepare(repo, args.netuid)
    storage_index = build_storage_index(repo, args.netuid, resolver=args.resolver)
    manifest_path = write_manifest(repo, args.netuid)
    storage_index_path = Path(storage_index["path"])

    if not args.dry_run and not args.skip_hippius:
        require_env(("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))
    if not args.dry_run and not args.skip_github and not shutil.which("gh"):
        raise SystemExit("missing gh CLI; install GitHub CLI or pass --skip-github")
    if not args.dry_run and not args.skip_huggingface:
        require_env(("HF_TOKEN",))
        if not args.hf_repo_id:
            raise SystemExit(
                "missing Hugging Face repo id; set HF_REPO_ID, pass --hf-repo-id, or pass --skip-huggingface"
            )

    env = os.environ.copy()
    env.setdefault("AWS_DEFAULT_REGION", args.region)

    if not args.skip_hippius:
        for command in hippius_commands(
            aws=aws_command(args.aws_command),
            repo=repo,
            bucket=args.bucket,
            endpoint_url=args.endpoint_url,
            netuid=args.netuid,
            snapshot=args.snapshot,
            manifest_path=manifest_path,
        ):
            execute(command, dry_run=args.dry_run, env=env)

    if not args.skip_github:
        execute(
            github_release_command(
                github_repo=args.github_repo,
                manifest_path=manifest_path,
                storage_index_path=storage_index_path,
                netuid=args.netuid,
                snapshot=args.snapshot,
                bucket=args.bucket,
            ),
            dry_run=args.dry_run,
        )

    if not args.skip_huggingface:
        for command in huggingface_commands(
            hf=hf_command(args.hf_command),
            hf_repo_id=args.hf_repo_id,
            repo=repo,
            netuid=args.netuid,
            snapshot=args.snapshot,
            manifest_path=manifest_path,
            storage_index_path=storage_index_path,
        ):
            execute(command, dry_run=args.dry_run, env=env)

    committed_repo = commit_repo_changes(
        repo,
        netuid=args.netuid,
        snapshot=args.snapshot,
        push=args.push_repo,
        dry_run=args.dry_run,
    ) if args.commit_repo or args.push_repo else False

    print(
        json.dumps(
            {
                **summary,
                "github_tag": f"{args.netuid}-{args.snapshot}",
                "huggingface_repo": args.hf_repo_id,
                "hippius_uri": f"s3://{args.bucket}/snapshots/{args.snapshot}/",
                "manifest": str(manifest_path),
                "repo_committed": committed_repo,
                "repo_pushed": bool(args.push_repo and committed_repo),
                "snapshot": args.snapshot,
                "storage_epochs": len(storage_index["epochs"]),
                "storage_index": str(storage_index_path),
                "synced": synced,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Publish a lemma-corpus snapshot to Hippius and GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lemma.corpus.storage import build_storage_index  # noqa: E402
from scripts.prepare_corpus_publish import prepare  # noqa: E402

DEFAULT_BUCKET = "lemma-corpus-sn467"
DEFAULT_ENDPOINT = "https://s3.hippius.com"
DEFAULT_GITHUB_REPO = "lemma-foundation/lemma-corpus"
DEFAULT_REGION = "decentralized"


def snapshot_id(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return current.strftime("%Y-%m-%dT%H-%M-%SZ")


def snapshot_label(snapshot: str) -> str:
    parsed = datetime.strptime(snapshot, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def public_dirs(repo: Path, netuid: str) -> tuple[tuple[str, Path], ...]:
    return (
        (f"{netuid}/registries", repo / "registries" / netuid),
        (f"{netuid}/corpus", repo / "corpus" / netuid),
        (f"{netuid}/indexes", repo / "indexes" / netuid),
        (f"{netuid}/exports", repo / "exports" / netuid),
        (f"canonical/{netuid}", repo / "canonical" / netuid),
    )


def write_manifest(repo: Path, netuid: str, manifest_path: Path | None = None) -> Path:
    target = manifest_path or repo / "MANIFEST.sha256"
    paths: list[Path] = []
    for _name, directory in public_dirs(repo, netuid):
        if not directory.is_dir():
            raise SystemExit(f"missing public corpus directory: {directory}")
        paths.extend(item for item in directory.rglob("*") if item.is_file())
    lines: list[str] = []
    for path in sorted(paths, key=lambda item: item.relative_to(repo).as_posix()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(repo).as_posix()
        lines.append(f"{digest}  {relative}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


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
    message = f"Publish {netuid} corpus snapshot {snapshot}"
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
            f"{netuid.upper()} corpus snapshot published to Hippius.",
            "",
            "Canonical Hippius bucket:",
            "",
            f"`s3://{bucket}/snapshots/{snapshot}/`",
            "",
            "Contents:",
            "",
            f"- `{netuid}/corpus/`",
            f"- `{netuid}/indexes/`",
            f"- `{netuid}/exports/`",
            f"- `{netuid}/registries/`",
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
    title = f"{netuid.upper()} corpus snapshot {snapshot_label(snapshot)}"
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


def run(cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    print("$ " + shlex.join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True, env=env, cwd=cwd)  # noqa: S603


def require_env(names: tuple[str, ...]) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"missing required environment variable(s): {joined}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="lemma-corpus checkout to publish")
    parser.add_argument("--netuid", default="sn467", help="corpus namespace, for example sn467")
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
    parser.add_argument("--pull", action="store_true", help="run git pull --ff-only in the corpus repo first")
    parser.add_argument("--skip-hippius", action="store_true", help="prepare files but do not upload to Hippius")
    parser.add_argument("--skip-github", action="store_true", help="prepare files but do not create the GitHub release")
    parser.add_argument(
        "--skip-huggingface",
        action="store_true",
        help="prepare files but do not upload the Hugging Face mirror",
    )
    parser.add_argument("--dry-run", action="store_true", help="print commands without running upload/release steps")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    if args.pull:
        run(["git", "-C", str(repo), "pull", "--ff-only"], dry_run=args.dry_run)

    summary = prepare(repo, args.netuid)
    storage_index = build_storage_index(repo, args.netuid, resolver=args.resolver)
    manifest_path = write_manifest(repo, args.netuid)
    storage_index_path = Path(storage_index["path"])
    env = os.environ.copy()
    env.setdefault("AWS_DEFAULT_REGION", args.region)

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
            run(command, dry_run=args.dry_run, env=env)

    if not args.skip_github:
        run(
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
        if not args.hf_repo_id:
            raise SystemExit(
                "missing Hugging Face repo id; set HF_REPO_ID, pass --hf-repo-id, or pass --skip-huggingface"
            )
        for command in huggingface_commands(
            hf=hf_command(args.hf_command),
            hf_repo_id=args.hf_repo_id,
            repo=repo,
            netuid=args.netuid,
            snapshot=args.snapshot,
            manifest_path=manifest_path,
            storage_index_path=storage_index_path,
        ):
            run(command, dry_run=args.dry_run, env=env)

    print(
        json.dumps(
            {
                **summary,
                "github_tag": f"{args.netuid}-{args.snapshot}",
                "huggingface_repo": args.hf_repo_id,
                "hippius_uri": f"s3://{args.bucket}/snapshots/{args.snapshot}/",
                "manifest": str(manifest_path),
                "snapshot": args.snapshot,
                "storage_epochs": len(storage_index["epochs"]),
                "storage_index": str(storage_index_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

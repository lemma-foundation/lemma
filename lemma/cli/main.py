"""Formal-math CLI for Lemma."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import click

from lemma import __version__
from lemma.cli.style import colors_enabled, rich_help_text, stylize
from lemma.common.config import LemmaSettings
from lemma.common.logging import setup_logging

_ROOT_COMMAND_ORDER = (
    "setup",
    "status",
    "mine",
    "preflight",
    "validate",
)


class LemmaCommand(click.Command):
    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rich_help = rich_help_text(self, ctx)
        if rich_help is None:
            super().format_help(ctx, formatter)
            return
        formatter.write(rich_help)


class LemmaGroup(click.Group):
    command_class = LemmaCommand
    group_class = type

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rich_help = rich_help_text(self, ctx)
        if rich_help is None:
            super().format_help(ctx, formatter)
            return
        formatter.write(rich_help)

    def list_commands(self, ctx: click.Context) -> list[str]:
        if ctx.parent is None:
            ordered = [name for name in _ROOT_COMMAND_ORDER if name in self.commands]
            return ordered + sorted(name for name in self.commands if name not in ordered)
        return sorted(self.commands)


@click.group(
    name="lemma",
    cls=LemmaGroup,
    invoke_without_command=True,
    context_settings={"max_content_width": 100},
)
@click.pass_context
@click.version_option(version=__version__)
def main(ctx: click.Context) -> None:
    """Reference client for Lemma's proof protocol.

    The CLI is the smallest correct path for setup, status, reference mining,
    and validation. Competitive miners can replace it and submit valid protocol
    outputs through their own infrastructure.

    Examples: lemma setup; lemma status; lemma mine --once; lemma validate
    --once --no-set-weights.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help(), color=colors_enabled())


def _env_path(env_path: Path | None) -> Path:
    return env_path or Path.cwd() / ".env"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_str_mapping(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise click.ClickException(f"{path}: expected JSON object")
    out: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise click.ClickException(f"{path}: expected string keys and values")
        out[key] = value
    return out


def _read_int_mapping(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise click.ClickException(f"{path}: expected JSON object")
    out: dict[str, int] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
            raise click.ClickException(f"{path}: expected string keys and integer values")
        out[key] = value
    return out


def _idle_validation_payload(
    settings: LemmaSettings,
    *,
    reason: str,
    spool_paths: tuple[Path, ...] = (),
    bucket_rejections: list[str] | None = None,
    bucket_tempo: int | None = None,
    active_tempo: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "verified": 0,
        "accepted_unique": 0,
        "credits": {},
        "scores": {},
        "submission_files_consumed": len(spool_paths),
        "bucket_reveals_consumed": 0,
        "bucket_reveals_rejected": len(bucket_rejections or ()),
        "reason": reason,
        "weights": {},
        "corpus_rows": 0,
        "unearned_policy": settings.unearned_allocation_policy,
        "unearned_share": 0.0,
        "weights_set": False,
        "chain_commitment_set": False,
        "tempo_commitment_payload": "",
    }
    if bucket_tempo is not None:
        payload["bucket_tempo"] = bucket_tempo
    if active_tempo is not None:
        payload["active_tempo"] = active_tempo
    return payload


def _load_registry():
    from lemma.tasks import TaskError, fetch_task_registry

    settings = LemmaSettings()
    try:
        return fetch_task_registry(settings)
    except (TaskError, OSError) as e:
        raise click.ClickException(str(e)) from e


def _task_or_die(task_id: str):
    from lemma.tasks import TaskError

    registry = _load_registry()
    try:
        return registry, registry.get(task_id)
    except TaskError as e:
        raise click.ClickException(str(e)) from e


def _print_task_summary(registry) -> None:
    click.echo(stylize("Lemma proof tasks", fg="cyan", bold=True))
    click.echo(stylize(f"registry_sha256={registry.sha256}", dim=True))
    for task in registry.tasks:
        title = f"  {stylize(task.id, fg='green', bold=True)}  {task.title or task.theorem_name}"
        click.echo(title)


def _print_task_detail(registry, task) -> None:
    click.echo(stylize(task.title or task.id, fg="cyan", bold=True))
    click.echo(stylize("  id              ", dim=True) + task.id)
    click.echo(stylize("  registry_sha256 ", dim=True) + registry.sha256)
    click.echo(stylize("  target_sha256   ", dim=True) + task.target_sha256)
    click.echo(stylize("  source_stream   ", dim=True) + task.source_stream)
    click.echo(stylize("  theorem_name    ", dim=True) + task.theorem_name)
    click.echo(stylize("  policy          ", dim=True) + task.policy)
    click.echo("")
    click.echo(stylize("Submission stub", fg="cyan", bold=True))
    click.echo(task.submission_stub.rstrip())


def _show_task(task_id: str) -> None:
    registry, task = _task_or_die(task_id)
    _print_task_detail(registry, task)


@main.command("setup")
@click.option("--env-file", "env_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--task-registry-url", default=None, help="Task registry JSON URL or path.")
@click.option("--task-registry-sha256", default=None, help="Optional task registry SHA256 pin.")
@click.option("--corpus-output-dir", default=None, help="Local directory for corpus JSONL deltas.")
@click.option("--operator-data-dir", default=None, help="Local directory for validator receipts.")
@click.option("--submission-spool-dir", default=None, help="Validator inbox for pending submission files.")
@click.option("--active-k", type=int, default=None, help="Paid active task slots.")
@click.option("--frontier-depth", type=int, default=None, help="Maximum active queue depth.")
@click.option("--active-queue-seed", default=None, help="Deterministic active-window seed.")
@click.option("--prover-command", default=None, help="Local prover command for miners.")
@click.option("--wallet-cold", default=None, help="Bittensor cold wallet name.")
@click.option("--wallet-hot", default=None, help="Bittensor hotkey name.")
@click.option("--netuid", type=int, default=None, help="Bittensor netuid.")
@click.option(
    "--unearned-policy",
    type=click.Choice(["burn", "recycle", "hold"]),
    default=None,
    help="Policy for unsolved-slot value.",
)
@click.option("--unearned-uid", type=int, default=None, help="UID used for unearned allocation rails.")
def setup_cmd(
    env_path: Path | None,
    task_registry_url: str | None,
    task_registry_sha256: str | None,
    corpus_output_dir: str | None,
    operator_data_dir: str | None,
    submission_spool_dir: str | None,
    active_k: int | None,
    frontier_depth: int | None,
    active_queue_seed: str | None,
    prover_command: str | None,
    wallet_cold: str | None,
    wallet_hot: str | None,
    netuid: int | None,
    unearned_policy: str | None,
    unearned_uid: int | None,
) -> None:
    """Write local settings for the reference client.

    \b
    Example:

      lemma setup --prover-command "python prover.py"
    """
    from lemma.cli.env_file import merge_dotenv

    updates = {
        "LEMMA_TASK_REGISTRY_URL": task_registry_url or LemmaSettings.model_fields["task_registry_url"].default,
        "LEMMA_CORPUS_OUTPUT_DIR": corpus_output_dir or str(LemmaSettings.model_fields["corpus_output_dir"].default),
        "LEMMA_OPERATOR_DATA_DIR": operator_data_dir or str(LemmaSettings.model_fields["operator_data_dir"].default),
        "LEMMA_ACTIVE_K": active_k if active_k is not None else LemmaSettings.model_fields["active_task_count"].default,
        "LEMMA_FRONTIER_DEPTH": frontier_depth
        if frontier_depth is not None
        else LemmaSettings.model_fields["frontier_depth"].default,
        "LEMMA_ACTIVE_QUEUE_SEED": active_queue_seed or LemmaSettings.model_fields["active_queue_seed"].default,
        "BT_WALLET_COLD": wallet_cold or LemmaSettings.model_fields["wallet_cold"].default,
        "BT_WALLET_HOT": wallet_hot or LemmaSettings.model_fields["wallet_hot"].default,
        "BT_NETUID": netuid if netuid is not None else LemmaSettings.model_fields["netuid"].default,
        "LEMMA_UNEARNED_ALLOCATION_POLICY": unearned_policy
        or LemmaSettings.model_fields["unearned_allocation_policy"].default,
        "LEMMA_UNEARNED_UID": unearned_uid
        if unearned_uid is not None
        else LemmaSettings.model_fields["unearned_uid"].default,
    }
    if task_registry_sha256:
        updates["LEMMA_TASK_REGISTRY_SHA256_EXPECTED"] = task_registry_sha256
    if submission_spool_dir:
        updates["LEMMA_SUBMISSION_SPOOL_DIR"] = submission_spool_dir
    if prover_command:
        updates["LEMMA_PROVER_COMMAND"] = prover_command
    path = _env_path(env_path)
    merge_dotenv(path, {key: str(value) for key, value in updates.items()})
    click.echo(stylize(f"Wrote {path}", fg="green", bold=True))


@main.command("status")
def status_cmd() -> None:
    """Show protocol, verifier, wallet, and prover status.

    \b
    Example:

      lemma status
    """
    settings = LemmaSettings()
    click.echo(stylize("Lemma formal-math status", fg="cyan", bold=True))
    click.echo(stylize("  wallet_cold       ", dim=True) + settings.wallet_cold)
    click.echo(stylize("  wallet_hot        ", dim=True) + settings.wallet_hot)
    click.echo(stylize("  netuid            ", dim=True) + str(settings.netuid))
    click.echo(stylize("  task_registry_url ", dim=True) + settings.task_registry_url)
    click.echo(stylize("  corpus_index_url  ", dim=True) + (settings.corpus_index_url or "(local)"))
    click.echo(stylize("  corpus_output_dir ", dim=True) + str(settings.corpus_output_dir))
    click.echo(stylize("  schema_version    ", dim=True) + settings.schema_version)
    click.echo(stylize("  enabled_domains   ", dim=True) + ",".join(settings.enabled_domains))
    spool = str(settings.submission_spool_dir) if settings.submission_spool_dir else "(not configured)"
    click.echo(stylize("  submission_spool  ", dim=True) + spool)
    click.echo(stylize("  prover_command    ", dim=True) + (settings.prover_command or "(not configured)"))
    click.echo(stylize("  lean_sandbox_image ", dim=True) + settings.lean_sandbox_image)
    click.echo(stylize("  lean_use_docker    ", dim=True) + str(settings.lean_use_docker))
    click.echo("")
    _print_task_summary(_load_registry())


@main.command("mine")
@click.option("--once", is_flag=True, help="Run one local proof-search iteration.")
@click.option("--task-id", default=None, help="Solve one task id.")
@click.option(
    "--registry",
    "registry_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Use a prebuilt task registry cache.",
)
@click.option("--prover-command", default=None, help="Override LEMMA_PROVER_COMMAND.")
@click.option("--solver-hotkey", default=None, help="Override solver attribution.")
@click.option("--sign", "sign_submission", is_flag=True, help="Sign the submission with the configured hotkey wallet.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
def mine_cmd(
    once: bool,
    task_id: str | None,
    registry_path: Path | None,
    prover_command: str | None,
    solver_hotkey: str | None,
    sign_submission: bool,
    output_path: Path | None,
) -> None:
    """Run the reference miner path and build a verified submission.

    \b
    Examples:

      lemma mine --once
      lemma mine --once --task-id lemma.sample.true_intro --output submission.json
    """
    from lemma.miner import ProverError, mine_once
    from lemma.tasks import TaskError, load_task_registry

    settings = LemmaSettings()
    try:
        registry = load_task_registry(registry_path.read_bytes()) if registry_path is not None else None
    except (OSError, TaskError) as e:
        raise click.ClickException(str(e)) from e
    if not once:
        click.echo(stylize("Running one miner iteration. Use a process supervisor to repeat it.", dim=True))
    try:
        result = mine_once(
            settings,
            task_id=task_id,
            prover_command=prover_command,
            registry=registry,
            solver_hotkey=solver_hotkey,
            sign=sign_submission,
        )
    except ProverError as e:
        raise click.ClickException(str(e)) from e
    text = result.submission.model_dump_json(indent=2, exclude_none=True)
    if output_path:
        output_path.write_text(text + "\n", encoding="utf-8")
        click.echo(stylize(f"Wrote {output_path}", fg="green", bold=True))
    else:
        click.echo(text)


@main.group("miner", hidden=True)
def miner_cmd() -> None:
    """Advanced miner protocol tools."""


@miner_cmd.group("bucket")
def miner_bucket_cmd() -> None:
    """Build and publish commitment-anchored miner buckets."""


def _read_submission_packages(paths: tuple[Path, ...]):
    from lemma.submissions import LemmaSubmission

    submissions: list[LemmaSubmission] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            for no, line in enumerate(text.splitlines(), start=1):
                if line.strip():
                    try:
                        submissions.append(LemmaSubmission.model_validate_json(line))
                    except ValueError as e:
                        raise click.ClickException(f"{path}:{no}: invalid submission: {e}") from e
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"{path}: invalid JSON: {e}") from e
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            try:
                submissions.append(LemmaSubmission.model_validate(row))
            except ValueError as e:
                raise click.ClickException(f"{path}: invalid submission: {e}") from e
    return tuple(submissions)


def _s3_object_uri(s3_uri: str, key: str) -> str:
    return s3_uri.rstrip("/") + "/" + key.lstrip("/")


def _bucket_url_from_s3_uri(s3_uri: str, endpoint_url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise click.ClickException("--s3-uri must look like s3://bucket/optional-prefix")
    prefix = parsed.path.strip("/")
    base = endpoint_url.rstrip("/") + "/" + parsed.netloc
    return base + (f"/{prefix}" if prefix else "")


def _aws_command(value: str | None) -> list[str]:
    import shlex
    import shutil

    if value:
        return shlex.split(value)
    if aws := shutil.which("aws"):
        return [aws]
    if uvx := shutil.which("uvx"):
        return [uvx, "--from", "awscli", "aws"]
    raise click.ClickException("missing aws CLI; install awscli or uv, or pass --aws-command")


def _run_external_command(command: list[str], *, dry_run: bool, capture_output: bool = False) -> bytes:
    import shlex
    import subprocess

    click.echo("$ " + shlex.join(command), err=True)
    if dry_run:
        return b""
    completed = subprocess.run(command, check=True, capture_output=capture_output)  # noqa: S603
    return completed.stdout if capture_output else b""


@miner_bucket_cmd.command("publish")
@click.option(
    "--submission",
    "submission_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Submission JSON or JSONL file to include if its task is in the active window.",
)
@click.option("--tempo", type=int, default=None, help="Tempo to publish; defaults to the current active tempo.")
@click.option("--drand-round", type=int, required=True, help="Drand round that opens the bucket.")
@click.option("--miner-hotkey", required=True, help="Miner hotkey SS58 address used for the chain commitment.")
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--bucket-url", default="", help="Public bucket URL validators poll.")
@click.option("--s3-uri", default="", help="Optional Hippius/AWS S3 destination, e.g. s3://bucket/miner-a.")
@click.option("--endpoint-url", default="https://s3.hippius.com", show_default=True)
@click.option("--aws-command", default=None, help='AWS CLI command, default: "aws" or "uvx --from awscli aws".')
@click.option("--verify-upload", is_flag=True, help="Read uploaded objects back and compare ciphertext hashes.")
@click.option("--submit-commitment", is_flag=True, help="Submit the Merkle-root commitment with the configured wallet.")
@click.option("--readback-commitment", is_flag=True, help="Read back the chain commitment after publishing.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Write local bucket files and print external commands without running them.",
)
def miner_bucket_publish_cmd(
    submission_paths: tuple[Path, ...],
    tempo: int | None,
    drand_round: int,
    miner_hotkey: str,
    output_dir: Path,
    bucket_url: str,
    s3_uri: str,
    endpoint_url: str,
    aws_command: str | None,
    verify_upload: bool,
    submit_commitment: bool,
    readback_commitment: bool,
    dry_run: bool,
) -> None:
    """Prepare and optionally upload a timelocked miner bucket."""
    from lemma.chain.commitments import read_storage_commitment, submit_storage_commitment
    from lemma.chain.miner_buckets import prepare_miner_bucket_publication, write_miner_bucket_publication
    from lemma.validator import active_tasks_for_validation, current_active_tempo, task_registry_for_validation

    settings = LemmaSettings()
    active_tempo = current_active_tempo(settings) if tempo is None else tempo
    registry = task_registry_for_validation(settings, tempo=active_tempo)
    active_tasks = active_tasks_for_validation(registry, settings, tempo=active_tempo)
    public_bucket_url = bucket_url or (_bucket_url_from_s3_uri(s3_uri, endpoint_url) if s3_uri else "")
    publication = prepare_miner_bucket_publication(
        submissions=_read_submission_packages(submission_paths),
        active_tasks=active_tasks,
        tempo=active_tempo,
        miner_hotkey=miner_hotkey,
        drand_round=drand_round,
        bucket_url=public_bucket_url,
    )
    write_miner_bucket_publication(publication, output_dir)

    uploaded = False
    if s3_uri:
        aws = _aws_command(aws_command)
        for blob in publication.blobs:
            _run_external_command(
                [
                    *aws,
                    "s3",
                    "cp",
                    str(output_dir / blob.key),
                    _s3_object_uri(s3_uri, blob.key),
                    "--endpoint-url",
                    endpoint_url,
                    "--only-show-errors",
                ],
                dry_run=dry_run,
            )
        uploaded = not dry_run
        if verify_upload:
            from lemma.chain.commitments import ciphertext_sha256

            for blob in publication.blobs:
                body = _run_external_command(
                    [
                        *aws,
                        "s3",
                        "cp",
                        _s3_object_uri(s3_uri, blob.key),
                        "-",
                        "--endpoint-url",
                        endpoint_url,
                        "--only-show-errors",
                    ],
                    dry_run=dry_run,
                    capture_output=True,
                )
                if not dry_run and ciphertext_sha256(body) != blob.ciphertext_sha256:
                    raise click.ClickException(f"uploaded object hash mismatch: {blob.key}")

    commitment_submission: dict[str, object] | None = None
    readback_payload = ""
    readback_matches: bool | None = None
    readback_hotkey = miner_hotkey
    if submit_commitment:
        if dry_run:
            click.echo(f"# would submit commitment: {publication.commitment_payload}", err=True)
        else:
            submitted = submit_storage_commitment(settings, publication.commitment_payload)
            readback_hotkey = submitted.hotkey
            commitment_submission = {
                "block_hash": submitted.block_hash,
                "block_number": submitted.block_number,
                "extrinsic_fee_rao": submitted.extrinsic_fee_rao,
                "extrinsic_function": submitted.extrinsic_function,
                "extrinsic_hash": submitted.extrinsic_hash,
                "message": submitted.message,
                "success": submitted.success,
            }
            if not submitted.success:
                raise click.ClickException(submitted.message or "commitment submission failed")
    if (readback_commitment or submit_commitment) and not dry_run:
        readback_payload = read_storage_commitment(settings, hotkey=readback_hotkey)
        readback_matches = readback_payload == publication.commitment_payload

    click.echo(
        json.dumps(
            {
                "bucket_url": publication.bucket_url,
                "commitment_payload": publication.commitment_payload,
                "commitment_readback_matches": readback_matches,
                "commitment_submission": commitment_submission,
                "drand_round": publication.drand_round,
                "dry_run": dry_run,
                "merkle_root": publication.merkle_root,
                "objects": [
                    {
                        "ciphertext_sha256": blob.ciphertext_sha256,
                        "key": blob.key,
                        "slot_index": blob.slot_index,
                        "task_id": blob.task_id,
                    }
                    for blob in publication.blobs
                ],
                "output_dir": str(output_dir),
                "readback_payload": readback_payload,
                "s3_uri": s3_uri,
                "tempo": publication.tempo,
                "uploaded": uploaded,
            },
            indent=2,
            sort_keys=True,
        )
    )


@main.group("tasks", cls=LemmaGroup, hidden=True)
def tasks_cmd() -> None:
    """List, pull, and show Lean theorem tasks."""


@tasks_cmd.command("list")
def tasks_list_cmd() -> None:
    """List active proof tasks.

    \b
    Example:

      lemma tasks list
    """
    _print_task_summary(_load_registry())


@tasks_cmd.command("pull")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
def tasks_pull_cmd(output_path: Path) -> None:
    """Write active tasks as JSONL.

    \b
    Example:

      lemma tasks pull --output active-tasks.jsonl
    """
    registry = _load_registry()
    output_path.write_text(
        "".join(task.model_dump_json(exclude_none=True) + "\n" for task in registry.tasks),
        encoding="utf-8",
    )
    click.echo(stylize(f"Wrote {len(registry.tasks)} tasks to {output_path}", fg="green", bold=True))


@tasks_cmd.command("checkout-path", hidden=True)
@click.argument("task_id")
@click.option(
    "--root",
    "root_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Source checkout root. Defaults to LEMMA_SOURCE_CHECKOUT_ROOT.",
)
def tasks_checkout_path_cmd(task_id: str, root_path: Path | None) -> None:
    """Print the expected local source checkout path for a patch task."""
    from lemma.source_checkouts import source_checkout_path

    _, task = _task_or_die(task_id)
    settings = LemmaSettings()
    root = root_path or settings.source_checkout_root
    if root is None:
        raise click.ClickException("LEMMA_SOURCE_CHECKOUT_ROOT is not configured")
    path = source_checkout_path(root, task.source_ref)
    if path is None:
        raise click.ClickException("task source_ref.commit is missing")
    click.echo(str(path))


@tasks_cmd.command("materialize-checkout", hidden=True)
@click.argument("task_id")
@click.option(
    "--root",
    "root_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Source checkout root. Defaults to LEMMA_SOURCE_CHECKOUT_ROOT.",
)
@click.option("--timeout", "timeout_s", type=click.IntRange(min=1), default=300, show_default=True)
def tasks_materialize_checkout_cmd(task_id: str, root_path: Path | None, timeout_s: int) -> None:
    """Clone/fetch a task source checkout at its deterministic cache path."""
    from lemma.source_checkouts import materialize_source_checkout

    _, task = _task_or_die(task_id)
    settings = LemmaSettings()
    root = root_path or settings.source_checkout_root
    if root is None:
        raise click.ClickException("LEMMA_SOURCE_CHECKOUT_ROOT is not configured")
    try:
        result = materialize_source_checkout(root, task.source_ref, timeout_s=timeout_s)
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(
        json.dumps(
            {
                "action": result.action,
                "commit": result.commit,
                "path": str(result.path),
                "task_id": task.id,
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("materialize-checkouts", hidden=True)
@click.option(
    "--root",
    "root_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Source checkout root. Defaults to LEMMA_SOURCE_CHECKOUT_ROOT.",
)
@click.option("--timeout", "timeout_s", type=click.IntRange(min=1), default=300, show_default=True)
def tasks_materialize_checkouts_cmd(root_path: Path | None, timeout_s: int) -> None:
    """Clone/fetch all patch-task source checkouts in the configured registry."""
    from lemma.source_checkouts import materialize_source_checkouts

    registry = _load_registry()
    settings = LemmaSettings()
    root = root_path or settings.source_checkout_root
    if root is None:
        raise click.ClickException("LEMMA_SOURCE_CHECKOUT_ROOT is not configured")
    try:
        results = materialize_source_checkouts(root, registry.tasks, timeout_s=timeout_s)
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(
        json.dumps(
            {
                "patch_task_count": len(results),
                "registry_sha256": registry.sha256,
                "results": [
                    {
                        "action": result.action,
                        "commit": result.commit,
                        "path": str(result.path),
                        "task_id": task.id,
                    }
                    for task, result in results
                ],
                "task_count": len(registry.tasks),
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("sign-registry")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Unsigned task registry JSON.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--key-uri", default=None, help="Development signer URI, for example //Alice.")
@click.option("--wallet-cold", default=None, help="Bittensor cold wallet name for registry-cache signing.")
@click.option("--wallet-hot", default=None, help="Bittensor hotkey name for registry-cache signing.")
def tasks_sign_registry_cmd(
    input_path: Path,
    output_path: Path,
    key_uri: str | None,
    wallet_cold: str | None,
    wallet_hot: str | None,
) -> None:
    """Attach a registry-cache signature and print the final SHA256 pin."""
    from lemma.tasks import registry_signing_payload

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise click.ClickException("registry must be a JSON object")
    payload.pop("signed_by", None)
    payload.pop("signature", None)
    if key_uri:
        from bittensor_wallet import Keypair

        keypair = Keypair.create_from_uri(key_uri)
    else:
        import bittensor as bt

        settings = LemmaSettings()
        keypair = bt.Wallet(name=wallet_cold or settings.wallet_cold, hotkey=wallet_hot or settings.wallet_hot).hotkey
    signature = keypair.sign(registry_signing_payload(payload))
    signature_hex = "0x" + signature.hex() if isinstance(signature, bytes) else str(signature)
    payload["signed_by"] = str(keypair.ss58_address)
    payload["signature"] = signature_hex if signature_hex.startswith("0x") else "0x" + signature_hex
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "signed_by": payload["signed_by"],
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("import-sorrydb", hidden=True)
@click.option(
    "--sorry-json",
    "sorry_json_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="One SorryDB row, a row array, or a SorryDB dataset object with sorries.",
)
@click.option(
    "--source-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Local checkout of the row's pinned repository commit.",
)
@click.option(
    "--source-checkout-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Root containing deterministic source checkouts for each SorryDB row.",
)
@click.option("--task-id", default=None)
@click.option("--theorem-name", default=None, help="Default theorem name. Required when a row lacks theorem_name.")
@click.option("--type-expr", default=None, help="Default target type. Required when a row lacks type_expr.")
@click.option("--source-license", required=True)
@click.option("--mathlib-rev", required=True)
@click.option("--lean-toolchain", default=None)
@click.option("--reproduction-command", default="lake build", show_default=True)
@click.option("--allow-extra-holes", is_flag=True, help="Allow source files with more than one sorry/admit hole.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
def tasks_import_sorrydb_cmd(
    sorry_json_path: Path,
    source_root: Path | None,
    source_checkout_root: Path | None,
    task_id: str | None,
    theorem_name: str | None,
    type_expr: str | None,
    source_license: str,
    mathlib_rev: str,
    lean_toolchain: str | None,
    reproduction_command: str,
    allow_extra_holes: bool,
    output_path: Path,
) -> None:
    """Create a patch registry from pinned SorryDB row data."""
    from lemma.source_checkouts import source_checkout_path
    from lemma.source_sorries import build_patch_task_from_sorrydb_record, source_ref_from_sorrydb_record
    from lemma.task_supply import write_registry

    payload = json.loads(sorry_json_path.read_text(encoding="utf-8"))
    rows = _sorrydb_rows(payload)
    if not rows:
        raise click.ClickException("sorry-json must contain at least one SorryDB row")
    if task_id is not None and len(rows) != 1:
        raise click.ClickException("--task-id can only be used with one SorryDB row")
    if (source_root is None) == (source_checkout_root is None):
        raise click.ClickException("pass exactly one of --source-root or --source-checkout-root")

    tasks = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("repo"), dict):
            raise click.ClickException("sorry-json must contain SorryDB row object(s)")
        row_theorem_name = _row_string(row, "theorem_name", theorem_name)
        row_type_expr = _row_string(row, "type_expr", type_expr)
        if row_theorem_name is None:
            raise click.ClickException("theorem_name is required for every SorryDB row")
        if row_type_expr is None:
            raise click.ClickException("type_expr is required for every SorryDB row")
        row_source_root = source_root
        if row_source_root is None:
            assert source_checkout_root is not None
            row_source_root = source_checkout_path(source_checkout_root, source_ref_from_sorrydb_record(row))
            if row_source_root is None:
                raise click.ClickException("source_ref.commit is required for every SorryDB row")
            if not row_source_root.is_dir():
                raise click.ClickException(f"source checkout missing: {row_source_root}")
        try:
            task = build_patch_task_from_sorrydb_record(
                row,
                source_root=row_source_root,
                theorem_name=row_theorem_name,
                type_expr=row_type_expr,
                source_license=source_license,
                mathlib_rev=mathlib_rev,
                task_id=_row_string(row, "task_id", task_id),
                lean_toolchain=lean_toolchain,
                reproduction_command=reproduction_command,
                allow_extra_holes=allow_extra_holes,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        tasks.append(task.model_copy(update={"queue_position": index}))

    write_registry(tasks, output_path)
    response = {
        "output": str(output_path),
        "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "task_count": len(tasks),
        "task_ids": [task.id for task in tasks],
    }
    if len(tasks) == 1:
        response["task_id"] = tasks[0].id
        response["target_sha256"] = tasks[0].target_sha256
    click.echo(
        json.dumps(
            response,
            indent=2,
            sort_keys=True,
        )
    )


def _sorrydb_rows(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("sorries"), list):
        return list(payload["sorries"])
    return [payload]


def _row_string(row: dict[str, object], field: str, default: str | None) -> str | None:
    value = row.get(field)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise click.ClickException(f"{field} must be a non-empty string")
    return value.strip()


@tasks_cmd.command("ingest-sorrydb", hidden=True)
@click.option(
    "--sorry-json",
    "sorry_json_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="One SorryDB row, a row array, or a SorryDB dataset object with sorries.",
)
@click.option(
    "--source-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Single local checkout shared by every row (use for one-repo batches).",
)
@click.option(
    "--source-checkout-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Root holding deterministic per-row checkouts (<kind>/<name>/<commit>).",
)
@click.option("--theorem-name", default=None, help="Default theorem name for rows lacking one.")
@click.option("--type-expr", default=None, help="Default target type for rows lacking one.")
@click.option("--source-license", required=True)
@click.option("--mathlib-rev", required=True)
@click.option("--lean-toolchain", default=None)
@click.option("--reproduction-command", default="lake build", show_default=True)
@click.option("--allow-extra-holes", is_flag=True, help="Allow source files with more than one sorry/admit hole.")
@click.option("--allow-unstable-toolchain", is_flag=True, help="Accept nightly/unpinned Lean toolchains.")
@click.option("--run-baseline", is_flag=True, help="Screen out tasks a baseline tactic closes (needs Lean).")
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Registry path for accepted candidate tasks.",
)
@click.option(
    "--report",
    "report_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional path to write the full ingestion report JSON.",
)
def tasks_ingest_sorrydb_cmd(
    sorry_json_path: Path,
    source_root: Path | None,
    source_checkout_root: Path | None,
    theorem_name: str | None,
    type_expr: str | None,
    source_license: str,
    mathlib_rev: str,
    lean_toolchain: str | None,
    reproduction_command: str,
    allow_extra_holes: bool,
    allow_unstable_toolchain: bool,
    run_baseline: bool,
    output_path: Path,
    report_path: Path | None,
) -> None:
    """Filter SorryDB rows into a task registry + a quarantine/env report.

    Unlike import-sorrydb, this never crashes on a bad row: each row is either
    accepted as a candidate or quarantined with a structured reason.
    """
    from typing import Any

    from lemma.ingest.sorrydb import ingest_sorrydb_rows
    from lemma.source_checkouts import source_checkout_path
    from lemma.source_sorries import source_ref_from_sorrydb_record
    from lemma.task_supply import write_registry

    if (source_root is None) == (source_checkout_root is None):
        raise click.ClickException("pass exactly one of --source-root or --source-checkout-root")

    payload = json.loads(sorry_json_path.read_text(encoding="utf-8"))
    rows = _sorrydb_rows(payload)
    if not rows:
        raise click.ClickException("sorry-json must contain at least one SorryDB row")

    def resolve_source_root(row: dict[str, Any]) -> Path | None:
        if source_root is not None:
            return source_root
        assert source_checkout_root is not None
        try:
            return source_checkout_path(source_checkout_root, source_ref_from_sorrydb_record(row))
        except ValueError:
            return None

    baseline_prober = None
    if run_baseline:
        from lemma.ingest.baseline import PreflightBaselineProber

        baseline_prober = PreflightBaselineProber()

    result = ingest_sorrydb_rows(
        rows,
        resolve_source_root=resolve_source_root,
        source_license=source_license,
        mathlib_rev=mathlib_rev,
        default_theorem_name=theorem_name,
        default_type_expr=type_expr,
        lean_toolchain=lean_toolchain,
        reproduction_command=reproduction_command,
        allow_extra_holes=allow_extra_holes,
        allow_unstable_toolchain=allow_unstable_toolchain,
        baseline_prober=baseline_prober,
    )

    write_registry(result.tasks, output_path)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result.report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    response: dict[str, object] = {
        "output": str(output_path),
        "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        **result.report.summary(),
        "task_ids": [task.id for task in result.tasks],
    }
    if report_path is not None:
        response["report"] = str(report_path)
    click.echo(json.dumps(response, indent=2, sort_keys=True))


@tasks_cmd.command("ingest-formal-conjectures", hidden=True)
@click.option(
    "--records-json",
    "records_json_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="One pre-extracted Formal Conjectures record, a record array, or {\"records\": [...]}.",
)
@click.option("--source-license", required=True)
@click.option("--mathlib-rev", required=True)
@click.option("--lean-toolchain", default=None, help="Default toolchain for records lacking one.")
@click.option("--allow-unstable-toolchain", is_flag=True, help="Accept nightly/unpinned Lean toolchains.")
@click.option(
    "--paid-default",
    is_flag=True,
    help="Treat records as paid unless they say otherwise (default: benchmark).",
)
@click.option("--run-baseline", is_flag=True, help="Screen out tasks a baseline tactic closes (needs Lean).")
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Registry path for accepted candidate tasks.",
)
@click.option(
    "--report",
    "report_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional path to write the full ingestion report JSON.",
)
def tasks_ingest_formal_conjectures_cmd(
    records_json_path: Path,
    source_license: str,
    mathlib_rev: str,
    lean_toolchain: str | None,
    allow_unstable_toolchain: bool,
    paid_default: bool,
    run_baseline: bool,
    output_path: Path,
    report_path: Path | None,
) -> None:
    """Filter Formal Conjectures records into a safely-restated task registry.

    New candidates default to held-out benchmark framing; pass --paid-default or
    set ``"paid": true`` per record to opt into paid work.
    """
    from lemma.ingest.formal_conjectures import ingest_formal_conjecture_records
    from lemma.task_supply import write_registry

    payload = json.loads(records_json_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        records = list(payload["records"])
    elif isinstance(payload, list):
        records = payload
    else:
        records = [payload]
    if not records:
        raise click.ClickException("records-json must contain at least one record")

    baseline_prober = None
    if run_baseline:
        from lemma.ingest.baseline import IsolatedProofBaselineProber

        baseline_prober = IsolatedProofBaselineProber()

    result = ingest_formal_conjecture_records(
        records,
        source_license=source_license,
        mathlib_rev=mathlib_rev,
        default_toolchain=lean_toolchain,
        default_paid=paid_default,
        allow_unstable_toolchain=allow_unstable_toolchain,
        baseline_prober=baseline_prober,
    )

    write_registry(result.tasks, output_path)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result.report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    response: dict[str, object] = {
        "output": str(output_path),
        "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        **result.report.summary(),
        "task_ids": [task.id for task in result.tasks],
    }
    if report_path is not None:
        response["report"] = str(report_path)
    click.echo(json.dumps(response, indent=2, sort_keys=True))


@tasks_cmd.command("show")
@click.argument("task_id")
def tasks_show_cmd(task_id: str) -> None:
    """Show one task and its submission stub.

    \b
    Example:

      lemma tasks show lemma.sample.true_intro
    """
    _show_task(task_id)


@tasks_cmd.command("inspect", hidden=True)
@click.argument("task_id")
def tasks_inspect_cmd(task_id: str) -> None:
    """Backward-compatible alias for `lemma tasks show`."""
    _show_task(task_id)


@main.group("task", cls=LemmaGroup, hidden=True)
def task_cmd() -> None:
    """Show one Lean theorem task."""


@task_cmd.command("show")
@click.argument("task_id")
def task_show_cmd(task_id: str) -> None:
    """Show one task and its submission stub.

    \b
    Example:

      lemma task show lemma.sample.true_intro
    """
    _show_task(task_id)


@main.command("verify", hidden=True)
@click.argument("task_id")
@click.option(
    "--submission",
    "submission_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option("--host-lean", "host_lean", is_flag=True, default=False)
def verify_cmd(task_id: str, submission_path: Path, host_lean: bool) -> None:
    """Verify a proof against one exact task.

    \b
    Example:

      lemma verify lemma.sample.true_intro --submission Submission.lean
    """
    from lemma.lean.verify_runner import run_lean_verify

    _, task = _task_or_die(task_id)
    settings = LemmaSettings()
    if host_lean and not settings.allow_host_lean:
        raise click.ClickException(
            "Host Lean is disabled. Use Docker (default), or set LEMMA_ALLOW_HOST_LEAN=1 for local debugging."
        )
    effective = settings.model_copy(update={"lean_use_docker": (not host_lean and settings.lean_use_docker)})
    result = run_lean_verify(
        effective,
        verify_timeout_s=settings.lean_verify_timeout_s,
        problem=task.to_problem(),
        proof_script=_read_text(submission_path),
        submission_policy=task.policy,
    )
    click.echo(result.model_dump_json(indent=2))
    if not result.passed:
        raise SystemExit(1)


@main.command("verify-patch", hidden=True)
@click.argument("task_id")
@click.option(
    "--source-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Clean source checkout root for the patch task.",
)
@click.option(
    "--patch",
    "patch_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Unified diff patch to validate.",
)
@click.option("--no-run-reproduction", is_flag=True, help="Apply static patch gates without running the command.")
@click.option("--timeout", "timeout_s", type=click.IntRange(min=1), default=120, show_default=True)
def verify_patch_cmd(
    task_id: str,
    source_root: Path,
    patch_path: Path,
    no_run_reproduction: bool,
    timeout_s: int,
) -> None:
    """Verify a patch against one source-pinned patch task.

    \b
    Example:

      lemma verify-patch lemma.task --source-root repo --patch solution.patch
    """
    from dataclasses import asdict

    from lemma.lean.patch_task import validate_patch_task

    _, task = _task_or_die(task_id)
    result = validate_patch_task(
        task,
        source_root=source_root,
        patch_text=_read_text(patch_path),
        run_reproduction=not no_run_reproduction,
        timeout_s=timeout_s,
    )
    click.echo(json.dumps(asdict(result), indent=2, sort_keys=True))
    if not result.accepted:
        raise SystemExit(1)


@main.command("preflight")
@click.argument("task_id")
@click.option(
    "--submission",
    "submission_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Isolated-proof Submission.lean to check.",
)
@click.option(
    "--patch",
    "patch_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Unified diff to check for a patch task.",
)
@click.option(
    "--source-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Clean source checkout root for a patch task (defaults to the configured checkout).",
)
@click.option(
    "--solved-tasks",
    "solved_tasks_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public JSON array of already-solved task ids (Proof Atlas).",
)
@click.option(
    "--solved-hashes",
    "solved_hashes_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public JSON array of already-accepted proof/patch sha256 hashes (Proof Atlas).",
)
@click.option("--no-run-reproduction", is_flag=True, help="Run static gates without the pinned build command.")
@click.option("--check-project-builds", is_flag=True, help="Confirm the unpatched base builds first (patch tasks).")
@click.option("--timeout", "timeout_s", type=click.IntRange(min=1), default=None, help="Override verify timeout.")
def preflight_cmd(
    task_id: str,
    submission_path: Path | None,
    patch_path: Path | None,
    source_root: Path | None,
    solved_tasks_path: Path | None,
    solved_hashes_path: Path | None,
    no_run_reproduction: bool,
    check_project_builds: bool,
    timeout_s: int | None,
) -> None:
    """Return the same accept/reject verdict a validator would, for one task.

    The verdict uses the canonical rejection vocabulary and is reproducible from
    public inputs, so a local pass matches what a validator records.

    \b
    Examples:

      lemma preflight lemma.sample.true_intro --submission Submission.lean
      lemma preflight lemma.sorrydb.task --patch fix.patch --source-root repo
    """
    from dataclasses import asdict

    from lemma.preflight import preflight_submission
    from lemma.submissions import build_patch_submission, build_submission

    if bool(submission_path) == bool(patch_path):
        raise click.ClickException("pass exactly one of --submission or --patch")

    _, task = _task_or_die(task_id)
    settings = LemmaSettings()
    if patch_path is not None:
        submission = build_patch_submission(task, solver_hotkey="preflight", patch_text=_read_text(patch_path))
    else:
        assert submission_path is not None
        submission = build_submission(task, solver_hotkey="preflight", proof_script=_read_text(submission_path))

    verdict = preflight_submission(
        task,
        submission,
        settings=settings,
        source_root=source_root,
        solved_task_ids=frozenset(_read_str_list(solved_tasks_path)),
        solved_proof_hashes=frozenset(_read_str_list(solved_hashes_path)),
        run_reproduction=not no_run_reproduction,
        check_project_builds=check_project_builds,
        timeout_s=timeout_s,
    )
    click.echo(json.dumps(asdict(verdict), indent=2, sort_keys=True))
    if not verdict.accepted:
        raise SystemExit(1)


def _read_str_list(path: Path | None) -> list[str]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise click.ClickException(f"{path}: expected a JSON array of strings")
    return payload


@main.command("submit", hidden=True)
@click.argument("task_id")
@click.option(
    "--submission",
    "submission_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option("--solver-hotkey", required=True, help="Solver hotkey or public identifier for attribution.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
def submit_cmd(task_id: str, submission_path: Path, solver_hotkey: str, output_path: Path | None) -> None:
    """Build a task-bound local submission package.

    \b
    Example:

      lemma submit lemma.sample.true_intro --submission Submission.lean --solver-hotkey hk --output submission.json
    """
    from lemma.submissions import build_submission

    _, task = _task_or_die(task_id)
    package = build_submission(task, solver_hotkey=solver_hotkey, proof_script=_read_text(submission_path))
    text = package.model_dump_json(indent=2, exclude_none=True)
    if output_path:
        output_path.write_text(text + "\n", encoding="utf-8")
        click.echo(stylize(f"Wrote {output_path}", fg="green", bold=True))
    else:
        click.echo(text)


@main.group("corpus", cls=LemmaGroup, hidden=True)
def corpus_cmd() -> None:
    """Validate, replay, and export accepted proof JSONL files."""


@corpus_cmd.command("validate")
@click.argument("corpus_jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def corpus_validate_cmd(corpus_jsonl: Path) -> None:
    """Validate corpus JSONL rows.

    \b
    Example:

      lemma corpus validate corpus/epoch-1.jsonl
    """
    from lemma.corpus import validate_jsonl

    try:
        count = validate_jsonl(corpus_jsonl)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(stylize(f"VALID: {count} accepted proof rows", fg="green", bold=True))


@corpus_cmd.command("replay")
@click.argument("corpus_jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def corpus_replay_cmd(corpus_jsonl: Path) -> None:
    """Replay corpus proofs through the Lean verifier.

    \b
    Example:

      lemma corpus replay corpus/epoch-1.jsonl
    """
    from lemma.corpus import replay_jsonl

    settings = LemmaSettings()
    results = replay_jsonl(settings, corpus_jsonl)
    passed = sum(1 for result in results if result.passed)
    click.echo(json.dumps([result.model_dump() for result in results], indent=2))
    if passed != len(results):
        raise SystemExit(1)


@corpus_cmd.command("export")
@click.option("--input", "input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
def corpus_export_cmd(input_dir: Path, output_path: Path) -> None:
    """Export a small corpus index JSON file.

    \b
    Example:

      lemma corpus export --input corpus --output corpus/corpus-index.json
    """
    from lemma.corpus import write_corpus_index

    write_corpus_index(input_dir, output_path)
    click.echo(stylize(f"Wrote {output_path}", fg="green", bold=True))


@corpus_cmd.command("benchmark-export")
@click.option("--input", "input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--index", "index_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--rewarded-only", is_flag=True, help="Export only proofs that received credit.")
@click.option("--useful-only", is_flag=True, help="Export only rows that passed useful-row gates.")
@click.option("--license", "license_filter", default=None, help="Filter by license state or use commercial-safe.")
@click.option("--exclude-near-duplicates", is_flag=True, help="Drop rows with near_duplicate_score >= 0.9.")
@click.option("--limit", type=click.IntRange(min=1), default=None)
def corpus_benchmark_export_cmd(
    input_dir: Path,
    output_path: Path,
    index_path: Path | None,
    rewarded_only: bool,
    useful_only: bool,
    license_filter: str | None,
    exclude_near_duplicates: bool,
    limit: int | None,
) -> None:
    """Export accepted proofs as compact benchmark/training JSONL.

    \b
    Example:

      lemma corpus benchmark-export --input corpus --output exports/lemma-proofs.jsonl --index exports/index.json
    """
    from lemma.corpus import write_benchmark_export

    index = write_benchmark_export(
        input_dir,
        output_path,
        index_path=index_path,
        rewarded_only=rewarded_only,
        useful_only=useful_only,
        license_filter=license_filter,
        exclude_near_duplicates=exclude_near_duplicates,
        limit=limit,
    )
    click.echo(json.dumps(index, indent=2, sort_keys=True))


@corpus_cmd.command("index", hidden=True)
@click.option("--input", "input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
def corpus_index_cmd(input_dir: Path, output_path: Path) -> None:
    """Backward-compatible alias for `lemma corpus export`."""
    corpus_export_cmd(input_dir, output_path)


@main.group("atlas", cls=LemmaGroup, hidden=True)
def atlas_cmd() -> None:
    """Build the public real-task Proof Atlas artifacts."""


@atlas_cmd.command("snapshot")
@click.option(
    "--registry",
    "registry_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Pinned task registry JSON with the real task bundles.",
)
@click.option(
    "--accepted",
    "accepted_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Accepted proof JSONL file or accepted/ directory.",
)
@click.option(
    "--source-report",
    "source_report_paths",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    help="Source ingest report JSON (repeatable).",
)
@click.option("--netuid", default="sn467", show_default=True, help="Proof Atlas namespace.")
@click.option(
    "--repo",
    "repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write the artifacts into this Proof Atlas checkout. Omit for a dry run.",
)
def atlas_snapshot_cmd(
    registry_path: Path,
    accepted_path: Path,
    source_report_paths: tuple[Path, ...],
    netuid: str,
    repo: Path | None,
) -> None:
    """Build the real-task Atlas layer from public inputs.

    By default this is a dry run: it prints a snapshot manifest (every file it
    would write plus its SHA256) without touching any repo or network. Pass
    --repo to write the artifacts into a Proof Atlas checkout.

    \b
    Example:

      lemma atlas snapshot --registry registry.json --accepted proofs/sn467/accepted
    """
    from lemma.atlas import build_snapshot_from_paths, write_real_task_snapshot
    from lemma.tasks import load_task_registry

    if repo is None:
        manifest, _payloads = build_snapshot_from_paths(
            netuid=netuid,
            registry_path=registry_path,
            accepted_path=accepted_path,
            source_report_paths=list(source_report_paths),
        )
    else:
        from lemma.atlas.realtask import _load_source_reports, read_accepted_rows

        registry = load_task_registry(registry_path.read_bytes())
        manifest = write_real_task_snapshot(
            repo,
            netuid=netuid,
            registry=registry,
            accepted_rows=read_accepted_rows(accepted_path),
            source_reports=_load_source_reports(list(source_report_paths)),
        )
    click.echo(json.dumps(manifest.model_dump(), indent=2, sort_keys=True))


@main.command("export-corpus", hidden=True)
@click.option("--domain", default="lean", show_default=True, help="Domain to export.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["jsonl", "parquet", "hf"]),
    default="jsonl",
    show_default=True,
    help="Export format.",
)
@click.option("--input", "input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option("--out", "output_path", type=click.Path(path_type=Path), required=True)
@click.option("--useful-only", is_flag=True, help="Export only rows that passed useful-row gates.")
@click.option("--license", "license_filter", default=None, help="Filter by license state or use commercial-safe.")
@click.option("--exclude-near-duplicates", is_flag=True, help="Drop rows with near_duplicate_score >= 0.9.")
def export_corpus_cmd(
    domain: str,
    fmt: str,
    input_dir: Path | None,
    output_path: Path,
    useful_only: bool,
    license_filter: str | None,
    exclude_near_duplicates: bool,
) -> None:
    """Export accepted Lean proofs as a reusable proof corpus.

    \b
    Example:

      lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
    """
    from lemma.corpus.export import ExportFormat, export_rows, rows_v2_from_legacy_dir

    settings = LemmaSettings()
    rows = rows_v2_from_legacy_dir(
        input_dir or settings.corpus_output_dir,
        domain=domain,
        useful_only=useful_only,
        license_filter=license_filter,
        exclude_near_duplicates=exclude_near_duplicates,
    )
    metadata = export_rows(rows, output=output_path, fmt=cast(ExportFormat, fmt))
    click.echo(stylize(f"Wrote {metadata['num_rows']} {domain} rows to {output_path}", fg="green", bold=True))


@main.group("site", cls=LemmaGroup, hidden=True)
def site_cmd() -> None:
    """Render the static real-task board and solved-proof explorer."""


@site_cmd.command("build")
@click.option(
    "--atlas",
    "atlas_repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Proof Atlas checkout containing the real-task artifacts.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Output directory for the static preview pages.",
)
@click.option("--netuid", default="sn467", show_default=True, help="Proof Atlas namespace.")
@click.option("--atlas-base-url", default=None, help="Public base URL for Proof Atlas links.")
@click.option("--hippius-url", default=None, help="Optional Hippius mirror URL for solved proofs.")
@click.option("--huggingface-url", default=None, help="Optional Hugging Face mirror URL for solved proofs.")
def site_build_cmd(
    atlas_repo: Path,
    out_dir: Path,
    netuid: str,
    atlas_base_url: str | None,
    hippius_url: str | None,
    huggingface_url: str | None,
) -> None:
    """Render the task board and solved-proof explorer from Atlas artifacts.

    The output is plain static HTML with no build step and no client-side data
    fetch, suitable for the lemmasub.net static deployment.

    \b
    Example:

      lemma site build --atlas ~/lemma-proof-atlas --out ~/lemma-proof-atlas/site
    """
    from lemma.site import SiteConfig, build_site_from_atlas

    defaults = SiteConfig(netuid=netuid)
    config = SiteConfig(
        netuid=netuid,
        atlas_base_url=atlas_base_url or defaults.atlas_base_url,
        hippius_url=hippius_url,
        huggingface_url=huggingface_url,
    )
    manifest = build_site_from_atlas(atlas_repo, out_dir, config=config)
    click.echo(json.dumps(manifest, indent=2, sort_keys=True))


@main.command("launch-check", hidden=True)
@click.option(
    "--registry",
    "registry_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Pinned task registry JSON to assess.",
)
@click.option("--min-tasks", type=click.IntRange(min=0), default=100, show_default=True, help="Minimum packaged tasks.")
@click.option("--epochs", type=click.IntRange(min=1), default=3, show_default=True, help="Local epochs to simulate.")
@click.option("--active-k", type=click.IntRange(min=1), default=8, show_default=True, help="Active tasks per epoch.")
def launch_check_cmd(registry_path: Path, min_tasks: int, epochs: int, active_k: int) -> None:
    """Run the end-to-end launch-readiness assessment and print a report.

    Exits non-zero when any automatically-checkable launch criterion fails.

    \b
    Example:

      lemma launch-check --registry registry.json --min-tasks 100
    """
    from lemma.launch import assess_launch_readiness
    from lemma.tasks import load_task_registry

    registry = load_task_registry(registry_path.read_bytes())
    report = assess_launch_readiness(registry, min_tasks=min_tasks, epochs=epochs, active_K=active_k)
    click.echo(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    if not report.ready:
        raise SystemExit(1)


@main.group("operator", hidden=True)
def operator_cmd() -> None:
    """Operator preflight and registry tools."""


@operator_cmd.command("preflight")
@click.pass_context
def operator_preflight_cmd(ctx: click.Context) -> None:
    """Check validator operator readiness without running a scoring pass.

    \b
    Example:

      lemma operator preflight
    """
    from lemma.operator import build_operator_preflight

    settings = LemmaSettings()
    setup_logging(settings.log_level)
    report = build_operator_preflight(settings)
    click.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    if not report.ok:
        ctx.exit(1)


@operator_cmd.command("diagnostics")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.pass_context
def operator_diagnostics_cmd(ctx: click.Context, output_path: Path) -> None:
    """Write a public-safe operator diagnostics JSON report.

    \b
    Example:

      lemma operator diagnostics --output operator-diagnostics.json
    """
    from lemma.operator import build_operator_diagnostics

    settings = LemmaSettings()
    setup_logging(settings.log_level)
    report = build_operator_diagnostics(settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "ok": report.preflight.ok,
        "registry_sha256": report.registry_sha256,
        "active_task_count": len(report.active_task_ids),
        "validator_run_count": report.artifacts.validator_run_count,
        "verification_record_count": report.artifacts.verification_record_count,
        "score_event_count": report.artifacts.score_event_count,
        "corpus_row_count": report.artifacts.corpus_row_count,
        "curriculum_can_increase_K": report.curriculum.can_increase_K,
        "curriculum_latest_tempo": report.curriculum.latest_tempo,
        "validator_capacity": report.curriculum.validator_capacity,
    }
    if report.registry_inspect is not None:
        summary.update(
            {
                "eligible_task_count": report.registry_inspect.eligible_task_count,
                "parked_task_count": report.registry_inspect.parked_task_count,
                "waiting_task_count": report.registry_inspect.waiting_task_count,
            }
        )
    click.echo(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
    )
    if not report.preflight.ok:
        ctx.exit(1)


@operator_cmd.command("write-active-registry-cache", hidden=True)
@click.option("--tempo", type=int, default=None, help="Tempo to cache; defaults to the current active tempo.")
@click.option(
    "--output-dir",
    "cache_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Active registry cache dir. Defaults to LEMMA_ACTIVE_REGISTRY_CACHE_DIR.",
)
def operator_write_active_registry_cache_cmd(tempo: int | None, cache_dir: Path | None) -> None:
    """Write the deterministic active registry cache file for one tempo."""
    from lemma.task_supply import write_registry
    from lemma.tasks import fetch_task_registry, load_task_registry
    from lemma.validator import active_registry_cache_path, active_tasks_for_validation, current_active_tempo

    settings = LemmaSettings()
    active_tempo = current_active_tempo(settings) if tempo is None else tempo
    cache_root = cache_dir or settings.active_registry_cache_dir
    if cache_root is None:
        raise click.ClickException("LEMMA_ACTIVE_REGISTRY_CACHE_DIR is not configured")
    cache_settings = settings.model_copy(update={"active_registry_cache_dir": cache_root, "active_registry_json": None})
    path = active_registry_cache_path(cache_settings, tempo=active_tempo)
    if path is None:
        raise click.ClickException("active registry cache path is not configured")

    registry = fetch_task_registry(settings, verify_signature=settings.verify_registry_signatures)
    active_tasks = active_tasks_for_validation(registry, settings, tempo=active_tempo)
    if len(active_tasks) != settings.active_task_count:
        raise click.ClickException(
            f"active window has {len(active_tasks)} tasks, expected LEMMA_ACTIVE_K={settings.active_task_count}"
        )
    write_registry(active_tasks, path)
    active_registry = load_task_registry(path.read_bytes())
    click.echo(
        json.dumps(
            {
                "active_task_count": len(active_tasks),
                "path": str(path),
                "registry_sha256": active_registry.sha256,
                "source_registry_sha256": registry.sha256,
                "tempo": active_tempo,
            },
            indent=2,
            sort_keys=True,
        )
    )


@operator_cmd.command("alerts")
@click.option("--recent-runs", type=int, default=5, show_default=True)
@click.option("--recent-failures", type=int, default=3, show_default=True)
@click.pass_context
def operator_alerts_cmd(ctx: click.Context, recent_runs: int, recent_failures: int) -> None:
    """Write machine-safe operator health alerts from recent run artifacts.

    Example:

      lemma operator alerts --recent-runs 8 --recent-failures 3
    """
    from lemma.operator import build_operator_alerts

    settings = LemmaSettings()
    setup_logging(settings.log_level)
    report = build_operator_alerts(
        settings,
        recent_runs=max(1, recent_runs),
        recent_failures=max(1, recent_failures),
    )
    click.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    if report.critical_count or report.warning_count:
        ctx.exit(1)


@operator_cmd.command("registry-inspect")
def operator_registry_inspect_cmd() -> None:
    """Inspect active and parked supply in the configured registry.

    \b
    Example:

      lemma operator registry-inspect
    """
    from lemma.operator import build_operator_registry_inspect
    from lemma.tasks import TaskError

    settings = LemmaSettings()
    setup_logging(settings.log_level)
    try:
        report = build_operator_registry_inspect(settings)
    except (TaskError, OSError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))


@main.command("validate")
@click.option("--once", is_flag=True, help="Run one validator scoring iteration.")
@click.option("--set-weights", is_flag=True, help="Submit computed weights to Bittensor.")
@click.option("--set-commitment", is_flag=True, help="Submit the tempo active/accepted artifact commitment.")
@click.option("--no-set-weights", is_flag=True, help="Compute weights without submitting them.")
@click.option("--submissions-jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--submission-spool", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--bucket-reveals-jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--bucket-reveals-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--miner-buckets-json", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--bucket-drand-round", type=int, default=None)
@click.option("--bucket-drand-signature", default="")
@click.option("--bucket-commit-blocks-json", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--verify-chain-commitments", is_flag=True, help="Read chain commitments for bucket reveals.")
@click.option("--verify-drand-reveals", is_flag=True, help="Decrypt bucket ciphertexts and match revealed proofs.")
@click.option("--validator-hotkey", default=None, help="Override validator attribution.")
@click.option("--require-signatures", is_flag=True, help="Require signed live miner submissions.")
@click.option("--require-commit-reveal", is_flag=True, help="Require revealed submissions to carry commit metadata.")
def validate_cmd(
    once: bool,
    set_weights: bool,
    set_commitment: bool,
    no_set_weights: bool,
    submissions_jsonl: Path | None,
    submission_spool: Path | None,
    bucket_reveals_jsonl: Path | None,
    bucket_reveals_dir: Path | None,
    miner_buckets_json: Path | None,
    bucket_drand_round: int | None,
    bucket_drand_signature: str,
    bucket_commit_blocks_json: Path | None,
    verify_chain_commitments: bool,
    verify_drand_reveals: bool,
    validator_hotkey: str | None,
    require_signatures: bool,
    require_commit_reveal: bool,
) -> None:
    """Run the validator proof-checking, scoring, and corpus-writing workflow.

    \b
    Example:

      lemma validate --once --bucket-reveals-jsonl bucket-reveals.jsonl --no-set-weights
    """
    from lemma.validator import (
        active_tasks_for_validation,
        archive_submission_spool,
        read_submission_spool,
        read_submissions_jsonl,
        validate_once,
    )

    settings = LemmaSettings()
    setup_logging(settings.log_level)
    if set_weights and no_set_weights:
        raise click.ClickException("choose either --set-weights or --no-set-weights")
    if set_weights and not settings.enable_set_weights:
        raise click.ClickException("set LEMMA_ENABLE_SET_WEIGHTS=1 before using --set-weights")
    if set_commitment and not settings.enable_set_commitment:
        raise click.ClickException("set LEMMA_ENABLE_SET_COMMITMENT=1 before using --set-commitment")
    if not set_commitment:
        settings = settings.model_copy(update={"enable_set_commitment": False})
    spool_dir = submission_spool or settings.submission_spool_dir
    if settings.protocol_mode == "production" and (submissions_jsonl is not None or spool_dir is not None):
        raise click.ClickException(
            "production validation requires --bucket-reveals-jsonl; direct JSON/spool intake is dev-only"
        )
    registry = None
    validation_tempo: int | None = None
    bucket_reveal_batch = None
    chain_authenticated_keys: frozenset[tuple[str, str, str]] = frozenset()
    bucket_reveal_count = 0
    bucket_rejections: list[str] = []
    submissions = read_submissions_jsonl(submissions_jsonl) if submissions_jsonl else []
    spool_paths: tuple[Path, ...] = ()
    if spool_dir is not None:
        spool_submissions, spool_paths = read_submission_spool(spool_dir)
        submissions.extend(spool_submissions)
    if miner_buckets_json is not None:
        if bucket_drand_round is None:
            raise click.ClickException("--miner-buckets-json requires --bucket-drand-round")
        if not bucket_drand_signature.strip():
            raise click.ClickException("--miner-buckets-json requires --bucket-drand-signature")
        from lemma.chain.commitments import read_all_commitments
        from lemma.chain.miner_buckets import poll_bucket_reveals, submissions_from_bucket_reveals
        from lemma.validator import current_active_tempo, task_registry_for_validation

        miner_bucket_urls = _read_str_mapping(miner_buckets_json)
        commit_blocks = _read_int_mapping(bucket_commit_blocks_json) if bucket_commit_blocks_json is not None else {}
        if settings.protocol_mode == "production":
            missing_blocks = sorted(set(miner_bucket_urls) - set(commit_blocks))
            nonpositive_blocks = sorted(
                miner for miner, block in commit_blocks.items() if miner in miner_bucket_urls and block <= 0
            )
            if missing_blocks or nonpositive_blocks:
                raise click.ClickException(
                    "production --miner-buckets-json requires positive --bucket-commit-blocks-json entries"
                )
        chain_active_tempo = current_active_tempo(settings)
        active_tempo = chain_active_tempo - 1 if settings.protocol_mode == "production" else chain_active_tempo
        if settings.protocol_mode == "production" and active_tempo < 0:
            click.echo(
                json.dumps(
                    _idle_validation_payload(
                        settings,
                        reason="bucket reveal tempo is not complete",
                        spool_paths=spool_paths,
                        bucket_rejections=bucket_rejections,
                        bucket_tempo=chain_active_tempo,
                        active_tempo=chain_active_tempo,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        validation_tempo = active_tempo
        registry = task_registry_for_validation(settings, tempo=active_tempo)
        chain_commitments_by_block = None
        if settings.protocol_mode == "production":
            chain_commitments_by_block = {
                block: read_all_commitments(settings, block=block)
                for block in sorted({commit_blocks[miner] for miner in miner_bucket_urls})
            }
            chain_commitments = {
                miner: chain_commitments_by_block[commit_blocks[miner]].get(miner, "")
                for miner in miner_bucket_urls
            }
        else:
            chain_commitments = read_all_commitments(settings)
        reveals = poll_bucket_reveals(
            miner_bucket_urls=miner_bucket_urls,
            chain_commitments=chain_commitments,
            commit_blocks=commit_blocks,
            active_tasks=active_tasks_for_validation(registry, settings, tempo=active_tempo),
            tempo=active_tempo,
            drand_round=bucket_drand_round,
            drand_signature=bucket_drand_signature,
            rejection_log=bucket_rejections.append,
        )
        bucket_reveal_count += len(reveals)
        bucket_submissions, bucket_authenticated = submissions_from_bucket_reveals(
            reveals,
            active_tasks_for_validation(registry, settings, tempo=active_tempo),
            verify_drand=verify_drand_reveals or settings.protocol_mode == "production",
            chain_commitments=chain_commitments,
            chain_commitments_by_block=chain_commitments_by_block,
            strict=False,
            rejection_log=bucket_rejections.append,
        )
        chain_authenticated_keys = frozenset({*chain_authenticated_keys, *bucket_authenticated})
        submissions.extend(bucket_submissions)
    if bucket_reveals_dir is not None:
        from lemma.chain.commitments import read_all_commitments
        from lemma.chain.miner_buckets import latest_bucket_reveal_batch, submissions_from_bucket_reveals
        from lemma.validator import current_active_tempo, task_registry_for_validation

        chain_active_tempo = current_active_tempo(settings)
        bucket_reveal_batch = latest_bucket_reveal_batch(
            bucket_reveals_dir,
            before_tempo=chain_active_tempo if settings.protocol_mode == "production" else None,
        )
        bucket_rejections.extend(bucket_reveal_batch.rejections)
        if not bucket_reveal_batch.reveals:
            from lemma.chain.miner_buckets import archive_bucket_reveal_batch

            archive_bucket_reveal_batch(bucket_reveal_batch)
            reveal_tempo = bucket_reveal_batch.tempo
            if (
                settings.protocol_mode == "production"
                and reveal_tempo is not None
                and reveal_tempo >= chain_active_tempo
            ):
                click.echo(
                    json.dumps(
                        _idle_validation_payload(
                            settings,
                            reason="bucket reveal tempo is not complete",
                            spool_paths=spool_paths,
                            bucket_rejections=bucket_rejections,
                            bucket_tempo=reveal_tempo,
                            active_tempo=chain_active_tempo,
                        ),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            click.echo(
                json.dumps(
                    {
                        "verified": 0,
                        "accepted_unique": 0,
                        "credits": {},
                        "scores": {},
                        "submission_files_consumed": len(spool_paths),
                        "bucket_reveals_consumed": 0,
                        "bucket_reveals_rejected": len(bucket_rejections),
                        "weights": {},
                        "corpus_rows": 0,
                        "unearned_policy": settings.unearned_allocation_policy,
                        "unearned_share": 0.0,
                        "weights_set": False,
                        "chain_commitment_set": False,
                        "tempo_commitment_payload": "",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        reveal_tempo = bucket_reveal_batch.tempo
        if reveal_tempo is None:
            raise click.ClickException("bucket reveal directory did not resolve a tempo")
        if settings.protocol_mode == "production" and reveal_tempo >= chain_active_tempo:
            click.echo(
                json.dumps(
                    _idle_validation_payload(
                        settings,
                        reason="bucket reveal tempo is not complete",
                        spool_paths=spool_paths,
                        bucket_rejections=bucket_rejections,
                        bucket_tempo=reveal_tempo,
                        active_tempo=chain_active_tempo,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        if validation_tempo is not None and validation_tempo != reveal_tempo:
            raise click.ClickException("live miner intake sources must target the same tempo")
        validation_tempo = reveal_tempo
        registry = task_registry_for_validation(settings, tempo=reveal_tempo)
        bucket_reveal_count += len(bucket_reveal_batch.reveals)
        directory_chain_commitments: dict[str, str] | None = None
        directory_chain_commitments_by_block: dict[int, dict[str, str]] | None = None
        if verify_chain_commitments or settings.protocol_mode == "production":
            reveal_commit_blocks = {reveal.commit_block for reveal in bucket_reveal_batch.reveals}
            if reveal_commit_blocks and all(block > 0 for block in reveal_commit_blocks):
                directory_chain_commitments_by_block = {
                    block: read_all_commitments(settings, block=block) for block in sorted(reveal_commit_blocks)
                }
            else:
                directory_chain_commitments = read_all_commitments(settings)
        bucket_submissions, bucket_authenticated = submissions_from_bucket_reveals(
            bucket_reveal_batch.reveals,
            active_tasks_for_validation(registry, settings, tempo=reveal_tempo),
            verify_drand=verify_drand_reveals or settings.protocol_mode == "production",
            chain_commitments=directory_chain_commitments,
            chain_commitments_by_block=directory_chain_commitments_by_block,
            strict=False,
            rejection_log=bucket_rejections.append,
        )
        chain_authenticated_keys = frozenset({*chain_authenticated_keys, *bucket_authenticated})
        submissions.extend(bucket_submissions)
    if bucket_reveals_jsonl is not None:
        from lemma.chain.commitments import read_all_commitments
        from lemma.chain.miner_buckets import read_bucket_reveals_jsonl, submissions_from_bucket_reveals
        from lemma.validator import current_active_tempo, task_registry_for_validation

        reveals = read_bucket_reveals_jsonl(bucket_reveals_jsonl)
        reveal_tempos = {reveal.tempo for reveal in reveals}
        if len(reveal_tempos) > 1:
            raise click.ClickException("bucket reveal JSONL must contain exactly one tempo")
        chain_active_tempo = current_active_tempo(settings)
        active_tempo = next(iter(reveal_tempos), chain_active_tempo)
        if settings.protocol_mode == "production" and active_tempo >= chain_active_tempo:
            click.echo(
                json.dumps(
                    _idle_validation_payload(
                        settings,
                        reason="bucket reveal tempo is not complete",
                        spool_paths=spool_paths,
                        bucket_rejections=bucket_rejections,
                        bucket_tempo=active_tempo,
                        active_tempo=chain_active_tempo,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        if validation_tempo is not None and validation_tempo != active_tempo:
            raise click.ClickException("live miner intake sources must target the same tempo")
        validation_tempo = active_tempo
        registry = task_registry_for_validation(settings, tempo=active_tempo)
        bucket_reveal_count += len(reveals)
        jsonl_chain_commitments: dict[str, str] | None = None
        jsonl_chain_commitments_by_block: dict[int, dict[str, str]] | None = None
        if verify_chain_commitments or settings.protocol_mode == "production":
            reveal_commit_blocks = {reveal.commit_block for reveal in reveals}
            if reveal_commit_blocks and all(block > 0 for block in reveal_commit_blocks):
                jsonl_chain_commitments_by_block = {
                    block: read_all_commitments(settings, block=block) for block in sorted(reveal_commit_blocks)
                }
            else:
                jsonl_chain_commitments = read_all_commitments(settings)
        bucket_submissions, bucket_authenticated = submissions_from_bucket_reveals(
            reveals,
            active_tasks_for_validation(registry, settings, tempo=active_tempo),
            verify_drand=verify_drand_reveals or settings.protocol_mode == "production",
            chain_commitments=jsonl_chain_commitments,
            chain_commitments_by_block=jsonl_chain_commitments_by_block,
            strict=False,
            rejection_log=bucket_rejections.append,
        )
        chain_authenticated_keys = frozenset({*chain_authenticated_keys, *bucket_authenticated})
        submissions.extend(bucket_submissions)
    if (
        not once
        and submissions_jsonl is None
        and spool_dir is None
        and bucket_reveals_jsonl is None
        and bucket_reveals_dir is None
        and miner_buckets_json is None
    ):
        click.echo(stylize("No live miner intake configured; running a local dry validator iteration.", dim=True))
    result = validate_once(
        settings,
        submissions,
        tempo=validation_tempo,
        registry=registry,
        validator_hotkey=validator_hotkey,
        no_set_weights=(not set_weights) or no_set_weights or not once,
        require_signatures=require_signatures,
        require_commit_reveal=require_commit_reveal,
        chain_authenticated_keys=chain_authenticated_keys,
    )
    if spool_paths and spool_dir is not None:
        archive_submission_spool(spool_paths, spool_dir)
    if bucket_reveal_batch is not None:
        from lemma.chain.miner_buckets import archive_bucket_reveal_batch

        archive_bucket_reveal_batch(bucket_reveal_batch)
    output = {
        "verified": len(result.verification_records),
        "accepted_unique": len(result.score.valid_unique_proofs),
        "credits": result.score.credits,
        "scores": result.score.scores,
        "submission_files_consumed": len(spool_paths),
        "bucket_reveals_consumed": bucket_reveal_count,
        "bucket_reveals_rejected": len(bucket_rejections),
        "weights": result.score.weights,
        "corpus_rows": len(result.corpus_rows),
        "unearned_policy": result.summary.unearned_policy,
        "unearned_share": result.summary.unearned_share,
        "weights_set": result.weights_set,
        "chain_commitment_set": result.summary.chain_commitment_set,
        "tempo_commitment_payload": result.summary.tempo_commitment_payload,
    }
    if result.weight_submission:
        output["chain_weight_uids"] = list(result.weight_submission.uids)
        output["chain_weight_values"] = list(result.weight_submission.weights)
    click.echo(json.dumps(output, indent=2, sort_keys=True))


@main.command("worker", hidden=True)
@click.option("--check", is_flag=True, help="Check task registry and verifier configuration.")
@click.option("--serve", is_flag=True, help="Run the Lean verification HTTP worker.")
@click.option("--host", default="localhost", show_default=True, help="Worker bind host.")
@click.option("--port", default=8787, type=int, show_default=True, help="Worker bind port.")
def worker_cmd(check: bool, serve: bool, host: str, port: int) -> None:
    """Check or serve the Lean verifier worker.

    \b
    Examples:

      lemma worker --check
      lemma worker --serve --host localhost --port 8787
    """
    settings = LemmaSettings()
    setup_logging(settings.log_level)
    if serve:
        from lemma.lean.worker_http import serve_forever

        serve_forever(host, port, settings)
        return

    registry = _load_registry()
    click.echo(stylize("Lemma worker", fg="cyan", bold=True))
    click.echo(stylize("  registry_sha256 ", dim=True) + registry.sha256)
    click.echo(stylize("  tasks           ", dim=True) + str(len(registry.tasks)))
    if check:
        click.echo(stylize("READY: task registry and Lean verifier settings are present.", fg="green"))
        return
    click.echo(stylize("Use --check for preflight or --serve to serve /verify.", dim=True))

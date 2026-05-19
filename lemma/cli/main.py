"""Training-data-first CLI for Lemma."""

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
    "validate",
    "operator",
    "tasks",
    "task",
    "verify",
    "submit",
    "corpus",
    "export-corpus",
    "worker",
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
    """Verified Reasoning Network.

    Examples: lemma setup; lemma status; lemma tasks list; lemma task show
    lemma.sample.true_intro; lemma mine --once; lemma validate --once
    --no-set-weights.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help(), color=colors_enabled())


def _env_path(env_path: Path | None) -> Path:
    return env_path or Path.cwd() / ".env"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_registry():
    from lemma.tasks import TaskError, fetch_task_registry

    try:
        return fetch_task_registry(LemmaSettings())
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
    """Write local operator and miner settings.

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
    """Show task registry, verifier, wallet, and prover status.

    \b
    Example:

      lemma status
    """
    settings = LemmaSettings()
    click.echo(stylize("Lemma verifier-data status", fg="cyan", bold=True))
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
@click.option("--prover-command", default=None, help="Override LEMMA_PROVER_COMMAND.")
@click.option("--solver-hotkey", default=None, help="Override solver attribution.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
def mine_cmd(
    once: bool,
    task_id: str | None,
    prover_command: str | None,
    solver_hotkey: str | None,
    output_path: Path | None,
) -> None:
    """Search for Lean proofs and build verified submissions.

    \b
    Examples:

      lemma mine --once
      lemma mine --once --task-id lemma.sample.true_intro --output submission.json
    """
    from lemma.miner import ProverError, mine_once

    settings = LemmaSettings()
    if not once:
        click.echo(stylize("Running one miner iteration. Use a process supervisor to repeat it.", dim=True))
    try:
        result = mine_once(settings, task_id=task_id, prover_command=prover_command, solver_hotkey=solver_hotkey)
    except ProverError as e:
        raise click.ClickException(str(e)) from e
    text = result.submission.model_dump_json(indent=2, exclude_none=True)
    if output_path:
        output_path.write_text(text + "\n", encoding="utf-8")
        click.echo(stylize(f"Wrote {output_path}", fg="green", bold=True))
    else:
        click.echo(text)


@main.group("tasks", cls=LemmaGroup)
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


@tasks_cmd.command("build-mathlib-snapshot")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Mathlib snapshot JSONL manifest.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--seed", default="lemma-mathlib-snapshot-v1", show_default=True)
@click.option("--frontier-depth", type=click.IntRange(min=0), default=None)
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.option("--signed-by", default=None, help="Attach external signer metadata; this command does not sign.")
@click.option("--signature", default=None, help="Attach external signature metadata; this command does not verify.")
def tasks_build_mathlib_snapshot_cmd(
    input_path: Path,
    output_path: Path,
    seed: str,
    frontier_depth: int | None,
    limit: int | None,
    signed_by: str | None,
    signature: str | None,
) -> None:
    """Build a deterministic task registry from proof-erased Mathlib rows.

    \b
    Example:

      lemma tasks build-mathlib-snapshot --input snapshot.jsonl --output tasks/registry.json
    """
    from lemma.supply.mathlib_snapshot import candidates_from_jsonl
    from lemma.supply.types import registry_tasks_from_candidates
    from lemma.task_supply import write_registry

    if (signed_by is None) != (signature is None):
        raise click.ClickException("--signed-by and --signature must be provided together")
    try:
        candidates = candidates_from_jsonl(input_path, limit=limit)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    tasks = registry_tasks_from_candidates(candidates, seed=seed, frontier_depth=frontier_depth)
    write_registry(tasks, output_path, signed_by=signed_by, signature=signature)
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    click.echo(
        json.dumps(
            {"output": str(output_path), "registry_sha256": digest, "tasks": len(tasks)},
            indent=2,
            sort_keys=True,
        )
    )


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


@main.group("task", cls=LemmaGroup)
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


@main.command("verify")
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


@main.command("submit")
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


@main.group("corpus", cls=LemmaGroup)
def corpus_cmd() -> None:
    """Validate, replay, and export Lemma Corpus JSONL files."""


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
    click.echo(stylize(f"VALID: {count} corpus rows", fg="green", bold=True))


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


@main.command("export-corpus")
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
    """Export accepted artifacts as a domain-neutral dataset.

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


@main.group("operator")
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
@click.option("--no-set-weights", is_flag=True, help="Chain-write guard; current build computes weights only.")
@click.option("--submissions-jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--submission-spool", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--validator-hotkey", default=None, help="Override validator attribution.")
@click.option("--require-signatures", is_flag=True, help="Require signed live miner submissions.")
def validate_cmd(
    once: bool,
    no_set_weights: bool,
    submissions_jsonl: Path | None,
    submission_spool: Path | None,
    validator_hotkey: str | None,
    require_signatures: bool,
) -> None:
    """Run the validator proof-checking, scoring, and corpus-writing workflow.

    \b
    Example:

      lemma validate --once --submissions-jsonl submissions.jsonl --no-set-weights
    """
    from lemma.validator import archive_submission_spool, read_submission_spool, read_submissions_jsonl, validate_once

    settings = LemmaSettings()
    setup_logging(settings.log_level)
    submissions = read_submissions_jsonl(submissions_jsonl) if submissions_jsonl else []
    spool_dir = submission_spool or settings.submission_spool_dir
    spool_paths: tuple[Path, ...] = ()
    if spool_dir is not None:
        spool_submissions, spool_paths = read_submission_spool(spool_dir)
        submissions.extend(spool_submissions)
    if not once and submissions_jsonl is None and spool_dir is None:
        click.echo(stylize("No live miner intake configured; running a local dry validator iteration.", dim=True))
    result = validate_once(
        settings,
        submissions,
        validator_hotkey=validator_hotkey,
        no_set_weights=no_set_weights or not once,
        require_signatures=require_signatures,
    )
    if spool_paths and spool_dir is not None:
        archive_submission_spool(spool_paths, spool_dir)
    click.echo(
        json.dumps(
            {
                "verified": len(result.verification_records),
                "accepted_unique": len(result.score.valid_unique_proofs),
                "credits": result.score.credits,
                "scores": result.score.scores,
                "submission_files_consumed": len(spool_paths),
                "weights": result.score.weights,
                "corpus_rows": len(result.corpus_rows),
                "unearned_policy": result.summary.unearned_policy,
                "unearned_share": result.summary.unearned_share,
                "weights_set": result.weights_set,
            },
            indent=2,
            sort_keys=True,
        )
    )


@main.command("worker")
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

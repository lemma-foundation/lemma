"""Training-data-first CLI for Lemma."""

from __future__ import annotations

import json
from pathlib import Path

import click

from lemma import __version__
from lemma.cli.style import colors_enabled, rich_help_text, stylize
from lemma.common.config import LemmaSettings
from lemma.common.logging import setup_logging

_ROOT_COMMAND_ORDER = ("setup", "status", "tasks", "verify", "submit", "corpus", "validate")


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
    """Lean-verified proof data subnet."""
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


@main.command("setup")
@click.option("--env-file", "env_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--task-registry-url", default=None, help="Task registry JSON URL or path.")
@click.option("--task-registry-sha256", default=None, help="Optional task registry SHA256 pin.")
@click.option("--wallet-cold", default=None, help="Bittensor cold wallet name.")
@click.option("--wallet-hot", default=None, help="Bittensor hotkey name.")
def setup_cmd(
    env_path: Path | None,
    task_registry_url: str | None,
    task_registry_sha256: str | None,
    wallet_cold: str | None,
    wallet_hot: str | None,
) -> None:
    """Write local task and wallet settings."""
    from lemma.cli.env_file import merge_dotenv

    updates = {
        "LEMMA_TASK_REGISTRY_URL": task_registry_url or LemmaSettings.model_fields["task_registry_url"].default,
        "BT_WALLET_COLD": wallet_cold or LemmaSettings.model_fields["wallet_cold"].default,
        "BT_WALLET_HOT": wallet_hot or LemmaSettings.model_fields["wallet_hot"].default,
    }
    if task_registry_sha256:
        updates["LEMMA_TASK_REGISTRY_SHA256_EXPECTED"] = task_registry_sha256
    path = _env_path(env_path)
    merge_dotenv(path, {key: str(value) for key, value in updates.items()})
    click.echo(stylize(f"Wrote {path}", fg="green", bold=True))


@main.command("status")
def status_cmd() -> None:
    """Show task registry and verifier status."""
    settings = LemmaSettings()
    click.echo(stylize("Lemma proof-data status", fg="cyan", bold=True))
    click.echo(stylize("  task_registry_url ", dim=True) + settings.task_registry_url)
    click.echo(stylize("  lean_sandbox_image ", dim=True) + settings.lean_sandbox_image)
    click.echo(stylize("  lean_use_docker    ", dim=True) + str(settings.lean_use_docker))
    click.echo("")
    _print_task_summary(_load_registry())


@main.group("tasks", cls=LemmaGroup)
def tasks_cmd() -> None:
    """List, pull, and inspect Lean theorem tasks."""


@tasks_cmd.command("list")
def tasks_list_cmd() -> None:
    """List active proof tasks."""
    _print_task_summary(_load_registry())


@tasks_cmd.command("pull")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
def tasks_pull_cmd(output_path: Path) -> None:
    """Write active tasks as JSONL."""
    registry = _load_registry()
    output_path.write_text(
        "".join(task.model_dump_json(exclude_none=True) + "\n" for task in registry.tasks),
        encoding="utf-8",
    )
    click.echo(stylize(f"Wrote {len(registry.tasks)} tasks to {output_path}", fg="green", bold=True))


@tasks_cmd.command("inspect")
@click.argument("task_id")
def tasks_inspect_cmd(task_id: str) -> None:
    """Show one task and its submission stub."""
    registry, task = _task_or_die(task_id)
    _print_task_detail(registry, task)


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
    """Verify a proof against one exact task."""
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
    """Build a local submission package."""
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
    """Validate and replay Lemma Corpus JSONL files."""


@corpus_cmd.command("validate")
@click.argument("corpus_jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def corpus_validate_cmd(corpus_jsonl: Path) -> None:
    """Validate corpus JSONL rows."""
    from lemma.corpus import validate_jsonl

    try:
        count = validate_jsonl(corpus_jsonl)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(stylize(f"VALID: {count} corpus rows", fg="green", bold=True))


@corpus_cmd.command("replay")
@click.argument("corpus_jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def corpus_replay_cmd(corpus_jsonl: Path) -> None:
    """Replay corpus proofs through the Lean verifier."""
    from lemma.corpus import replay_jsonl

    settings = LemmaSettings()
    results = replay_jsonl(settings, corpus_jsonl)
    passed = sum(1 for result in results if result.passed)
    click.echo(json.dumps([result.model_dump() for result in results], indent=2))
    if passed != len(results):
        raise SystemExit(1)


@main.command("validate")
@click.option("--check", is_flag=True, help="Check task registry and verifier configuration.")
@click.option("--worker", is_flag=True, help="Run the Lean verification HTTP worker.")
@click.option("--host", default="localhost", show_default=True, help="Worker bind host.")
@click.option("--port", default=8787, type=int, show_default=True, help="Worker bind port.")
def validate_cmd(check: bool, worker: bool, host: str, port: int) -> None:
    """Validate verifier readiness or run the Lean worker."""
    settings = LemmaSettings()
    setup_logging(settings.log_level)
    if worker:
        from lemma.lean.worker_http import serve_forever

        serve_forever(host, port, settings)
        return

    registry = _load_registry()
    click.echo(stylize("Lemma validate", fg="cyan", bold=True))
    click.echo(stylize("  registry_sha256 ", dim=True) + registry.sha256)
    click.echo(stylize("  tasks           ", dim=True) + str(len(registry.tasks)))
    if check:
        click.echo(stylize("READY: task registry and Lean verifier settings are present.", fg="green"))
        return
    click.echo(stylize("Use --check for preflight or --worker to serve /verify.", dim=True))

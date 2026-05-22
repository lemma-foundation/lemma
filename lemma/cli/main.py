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
@click.option("--prover-command", default=None, help="Override LEMMA_PROVER_COMMAND.")
@click.option("--solver-hotkey", default=None, help="Override solver attribution.")
@click.option("--sign", "sign_submission", is_flag=True, help="Sign the submission with the configured hotkey wallet.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
def mine_cmd(
    once: bool,
    task_id: str | None,
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

    settings = LemmaSettings()
    if not once:
        click.echo(stylize("Running one miner iteration. Use a process supervisor to repeat it.", dim=True))
    try:
        result = mine_once(
            settings,
            task_id=task_id,
            prover_command=prover_command,
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


@tasks_cmd.command("build-mathlib-snapshot")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Mathlib snapshot JSONL manifest.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--seed", default="lemma-mathlib-snapshot", show_default=True)
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


@tasks_cmd.command("build-mixed-registry")
@click.option(
    "--candidate-jsonl",
    "candidate_paths",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    help="JSONL task candidates from vetted non-production mixed supply streams.",
)
@click.option(
    "--mathlib-snapshot",
    "mathlib_snapshot_paths",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    help="Proof-erased Mathlib snapshot rows that already carry launch-gate metadata.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--seed", default="lemma-mixed-supply", show_default=True)
@click.option("--frontier-depth", type=click.IntRange(min=0), default=None)
def tasks_build_mixed_registry_cmd(
    candidate_paths: tuple[Path, ...],
    mathlib_snapshot_paths: tuple[Path, ...],
    output_path: Path,
    seed: str,
    frontier_depth: int | None,
) -> None:
    """Build a non-production mixed-supply registry."""
    from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
    from lemma.supply.mixed import build_mixed_registry_tasks, candidates_from_jsonl
    from lemma.supply.types import TaskCandidate
    from lemma.task_supply import write_registry

    candidates: list[TaskCandidate] = []
    for path in candidate_paths:
        candidates.extend(candidates_from_jsonl(path))
    for path in mathlib_snapshot_paths:
        candidates.extend(mathlib_candidates_from_jsonl(path))
    if not candidates:
        raise click.ClickException("provide at least one --candidate-jsonl or --mathlib-snapshot")
    build = build_mixed_registry_tasks(tuple(candidates), seed=seed, frontier_depth=frontier_depth)
    if build.rejected:
        detail = "; ".join(f"{item.id}:{item.reason}" for item in build.rejected[:10])
        raise click.ClickException(f"rejected {len(build.rejected)} launch candidates: {detail}")
    write_registry(build.tasks, output_path)
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "tasks": len(build.tasks),
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("generate-procedural-depth2")
@click.option(
    "--mathlib-snapshot",
    "snapshot_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Proof-erased Mathlib snapshot rows used as the public source pool.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--generation-seed", required=True, help="Epoch seed derived from chain state plus drand.")
@click.option("--epoch-randomness", required=True, help="Public chain/drand epoch material, usually JSON.")
@click.option("--tempo", type=click.IntRange(min=0), required=True)
@click.option("--count", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("--source-limit", type=click.IntRange(min=1), default=None)
@click.option("--prior-corpus-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--citation-alpha", type=click.FloatRange(min=0.0, max=1.0), default=0.25, show_default=True)
@click.option("--citation-weight-cap", type=click.FloatRange(min=1.0), default=100.0, show_default=True)
@click.option(
    "--triviality-retarget-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public settlement JSONL used to retarget the triviality budget T(t).",
)
@click.option(
    "--novelty-cache-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public statement-hash JSONL used by the procedural novelty gate.",
)
@click.option(
    "--import-graph-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public Lean import graph JSONL used by procedural slot-weight receipts.",
)
@click.option("--assume-gates", is_flag=True, help="Dev-only: skip Lean gate execution.")
def tasks_generate_procedural_depth2_cmd(
    snapshot_path: Path,
    output_path: Path,
    generation_seed: str,
    epoch_randomness: str,
    tempo: int,
    count: int,
    source_limit: int | None,
    prior_corpus_dir: Path | None,
    citation_alpha: float,
    citation_weight_cap: float,
    triviality_retarget_jsonl: Path | None,
    novelty_cache_jsonl: Path | None,
    import_graph_jsonl: Path | None,
    assume_gates: bool,
) -> None:
    """Generate depth-2 procedural task candidates from public epoch inputs."""
    from lemma.supply.gates import AssumedProceduralGateRunner, LeanProceduralGateRunner
    from lemma.supply.import_graph import empty_import_graph, read_import_graph
    from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
    from lemma.supply.novelty import empty_novelty_cache, read_novelty_cache
    from lemma.supply.procedural import corpus_sources_from_dir, generate_depth2_candidates, source_pool_hash
    from lemma.supply.triviality_budget import triviality_budget_receipt_for_settings

    settings = LemmaSettings()
    if triviality_retarget_jsonl is not None:
        settings = settings.model_copy(update={"procedural_triviality_retarget_jsonl": triviality_retarget_jsonl})
    triviality_budget = triviality_budget_receipt_for_settings(settings, tempo=tempo)
    sources = mathlib_candidates_from_jsonl(snapshot_path, limit=source_limit)
    if prior_corpus_dir is not None:
        sources = sources + corpus_sources_from_dir(prior_corpus_dir, before_tempo=tempo)
    novelty_cache = (
        read_novelty_cache(novelty_cache_jsonl) if novelty_cache_jsonl is not None else empty_novelty_cache()
    )
    import_graph = read_import_graph(import_graph_jsonl) if import_graph_jsonl is not None else empty_import_graph()
    candidates = generate_depth2_candidates(
        sources,
        generation_seed=generation_seed,
        epoch_randomness=epoch_randomness,
        count=count,
        tempo=tempo,
        citation_alpha=citation_alpha,
        citation_weight_cap=citation_weight_cap,
        gate_runner=AssumedProceduralGateRunner(novelty_cache=novelty_cache, import_graph=import_graph)
        if assume_gates
        else LeanProceduralGateRunner(
            settings,
            triviality_budget_receipt=triviality_budget,
            novelty_cache=novelty_cache,
            import_graph=import_graph,
        ),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(candidate.model_dump_json() + "\n" for candidate in candidates), encoding="utf-8")
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "source_pool_sha256": source_pool_hash(sources),
                "triviality_budget_s": triviality_budget.budget_s,
                "triviality_retarget_sha256": hashlib.sha256(
                    json.dumps(triviality_budget.inputs, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "novelty_cache_sha256": novelty_cache.sha256,
                "novelty_cache_entries": len(novelty_cache.statement_hashes),
                "import_graph_sha256": import_graph.sha256,
                "import_graph_entries": import_graph.entry_count,
                "candidates": len(candidates),
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("rebuild-procedural-registry")
@click.option(
    "--mathlib-snapshot",
    "snapshot_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Proof-erased Mathlib snapshot rows used as the public source pool.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--generation-seed", required=True, help="Epoch seed derived from chain state plus drand.")
@click.option("--epoch-randomness", required=True, help="Public chain/drand epoch material, usually JSON.")
@click.option("--tempo", type=click.IntRange(min=0), required=True)
@click.option("--count", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("--source-limit", type=click.IntRange(min=1), default=None)
@click.option("--frontier-depth", type=click.IntRange(min=0), default=None)
@click.option("--prior-corpus-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--citation-alpha", type=click.FloatRange(min=0.0, max=1.0), default=0.25, show_default=True)
@click.option("--citation-weight-cap", type=click.FloatRange(min=1.0), default=100.0, show_default=True)
@click.option(
    "--triviality-retarget-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public settlement JSONL used to retarget the triviality budget T(t).",
)
@click.option(
    "--novelty-cache-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public statement-hash JSONL used by the procedural novelty gate.",
)
@click.option(
    "--import-graph-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public Lean import graph JSONL used by procedural slot-weight receipts.",
)
def tasks_rebuild_procedural_registry_cmd(
    snapshot_path: Path,
    output_path: Path,
    generation_seed: str,
    epoch_randomness: str,
    tempo: int,
    count: int,
    source_limit: int | None,
    frontier_depth: int | None,
    prior_corpus_dir: Path | None,
    citation_alpha: float,
    citation_weight_cap: float,
    triviality_retarget_jsonl: Path | None,
    novelty_cache_jsonl: Path | None,
    import_graph_jsonl: Path | None,
) -> None:
    """Rebuild the production procedural registry from public inputs."""
    from lemma.supply.gates import LeanProceduralGateRunner
    from lemma.supply.import_graph import empty_import_graph, read_import_graph
    from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
    from lemma.supply.novelty import empty_novelty_cache, read_novelty_cache
    from lemma.supply.procedural import (
        build_procedural_registry_tasks,
        corpus_sources_from_dir,
        generate_depth2_candidates,
        source_pool_hash,
    )
    from lemma.supply.triviality_budget import triviality_budget_receipt_for_settings
    from lemma.task_supply import write_registry

    settings = LemmaSettings()
    if triviality_retarget_jsonl is not None:
        settings = settings.model_copy(update={"procedural_triviality_retarget_jsonl": triviality_retarget_jsonl})
    triviality_budget = triviality_budget_receipt_for_settings(settings, tempo=tempo)
    sources = mathlib_candidates_from_jsonl(snapshot_path, limit=source_limit)
    if prior_corpus_dir is not None:
        sources = sources + corpus_sources_from_dir(prior_corpus_dir, before_tempo=tempo)
    novelty_cache = (
        read_novelty_cache(novelty_cache_jsonl) if novelty_cache_jsonl is not None else empty_novelty_cache()
    )
    import_graph = read_import_graph(import_graph_jsonl) if import_graph_jsonl is not None else empty_import_graph()
    candidates = generate_depth2_candidates(
        sources,
        generation_seed=generation_seed,
        epoch_randomness=epoch_randomness,
        count=count,
        tempo=tempo,
        citation_alpha=citation_alpha,
        citation_weight_cap=citation_weight_cap,
        gate_runner=LeanProceduralGateRunner(
            settings,
            triviality_budget_receipt=triviality_budget,
            novelty_cache=novelty_cache,
            import_graph=import_graph,
        ),
    )
    build = build_procedural_registry_tasks(candidates, seed=generation_seed, frontier_depth=frontier_depth)
    if build.rejected:
        detail = "; ".join(f"{item.id}:{item.reason}" for item in build.rejected[:10])
        raise click.ClickException(f"rejected {len(build.rejected)} procedural candidates: {detail}")
    write_registry(build.tasks, output_path)
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "source_pool_sha256": source_pool_hash(sources),
                "triviality_budget_s": triviality_budget.budget_s,
                "triviality_retarget_sha256": hashlib.sha256(
                    json.dumps(triviality_budget.inputs, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "novelty_cache_sha256": novelty_cache.sha256,
                "novelty_cache_entries": len(novelty_cache.statement_hashes),
                "import_graph_sha256": import_graph.sha256,
                "import_graph_entries": import_graph.entry_count,
                "tasks": len(build.tasks),
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("build-procedural-registry")
@click.option(
    "--candidate-jsonl",
    "candidate_paths",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    required=True,
    help="JSONL task candidates emitted by the deterministic depth-2 generator.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--seed", default="lemma-procedural-depth2", show_default=True)
@click.option("--frontier-depth", type=click.IntRange(min=0), default=None)
def tasks_build_procedural_registry_cmd(
    candidate_paths: tuple[Path, ...],
    output_path: Path,
    seed: str,
    frontier_depth: int | None,
) -> None:
    """Build a production-shaped procedural depth-2 task registry."""
    from lemma.supply.procedural import build_procedural_registry_tasks, candidates_from_jsonl
    from lemma.supply.types import TaskCandidate
    from lemma.task_supply import write_registry

    candidates: list[TaskCandidate] = []
    for path in candidate_paths:
        candidates.extend(candidates_from_jsonl(path))
    build = build_procedural_registry_tasks(tuple(candidates), seed=seed, frontier_depth=frontier_depth)
    if build.rejected:
        detail = "; ".join(f"{item.id}:{item.reason}" for item in build.rejected[:10])
        raise click.ClickException(f"rejected {len(build.rejected)} procedural candidates: {detail}")
    write_registry(build.tasks, output_path)
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "tasks": len(build.tasks),
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("extract-mathlib-snapshot")
@click.option(
    "--mathlib-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Pinned Mathlib checkout root.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--include", "includes", multiple=True, help="Repo-relative glob, e.g. Mathlib/Data/Nat/*.lean.")
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.option("--depth0-limit", type=click.IntRange(min=0), default=None)
@click.option("--depth1-limit", type=click.IntRange(min=0), default=None)
@click.option("--depth2-limit", type=click.IntRange(min=0), default=None)
@click.option("--mathlib-rev", default=None, help="Override git-derived Mathlib revision.")
@click.option("--source-license", default="Apache-2.0", show_default=True)
@click.option("--elaborate-types", is_flag=True, help="Use Lean #check output for self-contained theorem types.")
@click.option(
    "--lake-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Lake project root for --elaborate-types.",
)
def tasks_extract_mathlib_snapshot_cmd(
    mathlib_root: Path,
    output_path: Path,
    includes: tuple[str, ...],
    limit: int | None,
    depth0_limit: int | None,
    depth1_limit: int | None,
    depth2_limit: int | None,
    mathlib_rev: str | None,
    source_license: str,
    elaborate_types: bool,
    lake_root: Path | None,
) -> None:
    """Extract proof-erased snapshot rows from a pinned Mathlib checkout."""
    from collections import Counter

    from lemma.supply.mathlib_extract import ExtractConfig, extract_snapshot_rows, write_snapshot_jsonl

    try:
        rows = extract_snapshot_rows(
            ExtractConfig(
                mathlib_root=mathlib_root,
                includes=includes or ("Mathlib/**/*.lean",),
                limit=limit,
                depth0_limit=depth0_limit,
                depth1_limit=depth1_limit,
                depth2_limit=depth2_limit,
                mathlib_rev=mathlib_rev,
                source_license=source_license,
                elaborate_types=elaborate_types,
                lake_root=lake_root,
            )
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    write_snapshot_jsonl(rows, output_path)
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "queue_depth_counts": dict(sorted(Counter(str(row.queue_depth) for row in rows).items())),
                "rows": len(rows),
            },
            indent=2,
            sort_keys=True,
        )
    )


@tasks_cmd.command("extract-import-graph")
@click.option(
    "--mathlib-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Pinned Mathlib checkout root.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--include", "includes", multiple=True, help="Repo-relative glob, e.g. Mathlib/Data/Nat/*.lean.")
def tasks_extract_import_graph_cmd(
    mathlib_root: Path,
    output_path: Path,
    includes: tuple[str, ...],
) -> None:
    """Extract a public Lean module import graph from a pinned Mathlib checkout."""
    from lemma.supply.import_graph import extract_import_graph_rows, import_graph_from_rows, write_import_graph_jsonl

    rows = extract_import_graph_rows(mathlib_root, includes or ("Mathlib/**/*.lean",))
    write_import_graph_jsonl(rows, output_path)
    graph = import_graph_from_rows(rows)
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "import_graph_sha256": graph.sha256,
                "import_graph_entries": graph.entry_count,
            },
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
    """Export accepted Lean proofs as a mathematical corpus.

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
@click.option("--set-weights", is_flag=True, help="Submit computed weights to Bittensor.")
@click.option("--no-set-weights", is_flag=True, help="Compute weights without submitting them.")
@click.option("--submissions-jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--submission-spool", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--bucket-reveals-jsonl", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--verify-chain-commitments", is_flag=True, help="Read chain commitments for bucket reveals.")
@click.option("--verify-drand-reveals", is_flag=True, help="Decrypt bucket ciphertexts and match revealed proofs.")
@click.option("--validator-hotkey", default=None, help="Override validator attribution.")
@click.option("--require-signatures", is_flag=True, help="Require signed live miner submissions.")
@click.option("--require-commit-reveal", is_flag=True, help="Require revealed submissions to carry commit metadata.")
def validate_cmd(
    once: bool,
    set_weights: bool,
    no_set_weights: bool,
    submissions_jsonl: Path | None,
    submission_spool: Path | None,
    bucket_reveals_jsonl: Path | None,
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
    spool_dir = submission_spool or settings.submission_spool_dir
    if settings.protocol_mode == "production" and (submissions_jsonl is not None or spool_dir is not None):
        raise click.ClickException(
            "production validation requires --bucket-reveals-jsonl; direct JSON/spool intake is dev-only"
        )
    registry = None
    chain_authenticated_keys: frozenset[tuple[str, str, str]] = frozenset()
    bucket_reveal_count = 0
    bucket_rejections: list[str] = []
    submissions = read_submissions_jsonl(submissions_jsonl) if submissions_jsonl else []
    spool_paths: tuple[Path, ...] = ()
    if spool_dir is not None:
        spool_submissions, spool_paths = read_submission_spool(spool_dir)
        submissions.extend(spool_submissions)
    if bucket_reveals_jsonl is not None:
        from lemma.chain.commitments import read_all_commitments
        from lemma.chain.miner_buckets import read_bucket_reveals_jsonl, submissions_from_bucket_reveals
        from lemma.validator import current_active_tempo, task_registry_for_validation

        active_tempo = current_active_tempo(settings)
        registry = task_registry_for_validation(settings, tempo=active_tempo)
        reveals = read_bucket_reveals_jsonl(bucket_reveals_jsonl)
        bucket_reveal_count = len(reveals)
        chain_commitments = (
            read_all_commitments(settings)
            if verify_chain_commitments or settings.protocol_mode == "production"
            else None
        )
        bucket_submissions, chain_authenticated_keys = submissions_from_bucket_reveals(
            reveals,
            active_tasks_for_validation(registry, settings, tempo=active_tempo),
            verify_drand=verify_drand_reveals or settings.protocol_mode == "production",
            chain_commitments=chain_commitments,
            strict=False,
            rejection_log=bucket_rejections.append,
        )
        submissions.extend(bucket_submissions)
    if not once and submissions_jsonl is None and spool_dir is None and bucket_reveals_jsonl is None:
        click.echo(stylize("No live miner intake configured; running a local dry validator iteration.", dim=True))
    result = validate_once(
        settings,
        submissions,
        registry=registry,
        validator_hotkey=validator_hotkey,
        no_set_weights=(not set_weights) or no_set_weights or not once,
        require_signatures=require_signatures,
        require_commit_reveal=require_commit_reveal,
        chain_authenticated_keys=chain_authenticated_keys,
    )
    if spool_paths and spool_dir is not None:
        archive_submission_spool(spool_paths, spool_dir)
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

"""Ingredient-mode CLI commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import click
from pydantic import BaseModel

from lemma.common.config import LemmaSettings


def _ingredient_difficulty_state_context_from_cli(
    *,
    difficulty_state_jsonl: Path | None,
    difficulty_state_sha256: str | None,
    difficulty_lane: str | None,
    tempo: int | None,
) -> tuple[str | None, str | None]:
    if difficulty_state_jsonl is None:
        return difficulty_state_sha256, difficulty_lane
    if tempo is None:
        raise click.ClickException("--tempo is required with --difficulty-state-jsonl")
    from lemma.supply.ingredients import ingredient_difficulty_state_context

    if difficulty_state_jsonl.is_symlink() or not difficulty_state_jsonl.is_file():
        raise click.ClickException("ingredient difficulty state path invalid")
    try:
        actual_sha256, active_lane = ingredient_difficulty_state_context(
            difficulty_state_jsonl.read_bytes(),
            tempo=tempo,
        )
    except OSError as e:
        raise click.ClickException(f"ingredient difficulty state unreadable: {e.filename}") from e
    except ValueError as e:
        raise click.ClickException(f"ingredient {e}") from e
    if difficulty_state_sha256 is not None and difficulty_state_sha256 != actual_sha256:
        raise click.ClickException("ingredient difficulty state sha256 mismatch")
    if difficulty_lane is not None and difficulty_lane != active_lane:
        raise click.ClickException("ingredient difficulty state active lane mismatch")
    return actual_sha256, active_lane


def _require_canonical_json_artifact(raw: bytes, model: BaseModel | dict[str, Any], error: str) -> None:
    from lemma.supply.ingredients import canonical_json_bytes

    if raw != canonical_json_bytes(model) + b"\n":
        raise click.ClickException(error)


def _require_canonical_ingredient_manifest(raw: bytes, manifest: BaseModel | dict[str, Any]) -> None:
    _require_canonical_json_artifact(raw, manifest, "ingredient manifest noncanonical")


def _read_ingredient_manifest_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise click.ClickException("ingredient manifest path invalid")
    try:
        return path.read_bytes()
    except OSError as e:
        raise click.ClickException(f"ingredient manifest unreadable: {path}") from e


def _read_ingredient_task_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise click.ClickException("ingredient task path invalid")
    try:
        return path.read_bytes()
    except OSError as e:
        raise click.ClickException(f"ingredient task unreadable: {path}") from e


def _read_ingredient_statement_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise click.ClickException("ingredient statement file path invalid")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise click.ClickException(f"ingredient statement file unreadable: {path}") from e


_PRODUCTION_INGREDIENT_GATE_CHECKS = (
    "lean_challenge_typechecked",
    "lean_verify_reason:ok",
    "soundness_template_typechecked",
    "soundness_template_no_holes",
    "soundness_template_witness_checked",
    "soundness_template_verify_reason:ok",
    "bounded_triviality_checked",
    "baseline_triviality_not_solved",
    "bounded_triviality_reason:compile_error",
    "novelty_cache_bound",
    "theorem_type_not_in_novelty_cache",
    "selection_family_not_in_novelty_cache",
)


def _require_production_ingredient_gate_profile(gate_receipt: Any, context: str) -> None:
    if gate_receipt.runner != "lean-statement-gate":
        raise click.ClickException(f"production ingredient {context} requires Lean statement gate")
    missing = [check for check in _PRODUCTION_INGREDIENT_GATE_CHECKS if check not in gate_receipt.checks]
    if missing:
        raise click.ClickException(
            f"production ingredient {context} gate checks missing: {', '.join(missing)}"
        )


def _expected_ingredient_gate_receipts(
    *,
    root_path: Path,
    manifest_mathlib_commit: str,
    selection: Any,
    task: Any,
    gate_receipt: Any,
    shortcut_receipt: Any,
    generation_receipt: Any,
    ingredient_manifest_sha256: str,
    novelty_cache_jsonl: Path | None,
    production: bool,
    context: str,
) -> tuple[Any, Any]:
    from pydantic import ValidationError

    from lemma.supply.ingredients import (
        canonical_sha256,
        ingredient_novelty_gate_details,
        ingredient_shortcut_gate_receipt,
        ingredient_shortcut_tactic_gate_details,
        ingredient_statement_gate_receipt,
        ingredient_triviality_gate_details,
    )

    if production:
        _require_production_ingredient_gate_profile(gate_receipt, context)

    triviality_details = None
    triviality_checks = {"bounded_triviality_checked", "baseline_triviality_not_solved"}
    triviality_reason = next(
        (
            check.removeprefix("bounded_triviality_reason:")
            for check in gate_receipt.checks
            if check.startswith("bounded_triviality_reason:")
        ),
        None,
    )
    if triviality_checks & set(gate_receipt.checks) or triviality_reason is not None:
        if triviality_reason:
            settings = LemmaSettings()
            try:
                triviality_details = ingredient_triviality_gate_details(
                    theorem_name=task.theorem_name,
                    theorem_type_expr=task.type_expr,
                    imports=task.imports,
                    verify_reason=triviality_reason,
                    max_heartbeats=settings.procedural_triviality_budget_heartbeats,
                )
            except ValueError as e:
                raise click.ClickException(str(e)) from e
    novelty_details = None
    if {"novelty_cache_bound", "theorem_type_not_in_novelty_cache"} & set(gate_receipt.checks):
        if novelty_cache_jsonl is None:
            raise click.ClickException("ingredient task artifact novelty cache required")
        from lemma.supply.novelty import read_novelty_cache

        try:
            novelty_details = ingredient_novelty_gate_details(
                theorem_type_expr=task.type_expr,
                novelty_cache=read_novelty_cache(novelty_cache_jsonl, strict_statement_hash_rows=True),
                selection=selection,
            )
        except (OSError, ValueError) as e:
            raise click.ClickException(str(e)) from e
    shortcut_tactic_details = None
    if "shortcut_tactics_checked" in shortcut_receipt.checks:
        shortcut_tactic_details = shortcut_receipt.details.get("shortcut_tactic_gate")
        if not isinstance(shortcut_tactic_details, dict):
            raise click.ClickException("ingredient task artifact shortcut receipt mismatch")
        try:
            budget = shortcut_tactic_details.get("shortcut_tactic_budget_heartbeats")
            tactics = shortcut_tactic_details.get("shortcut_tactics")
            if not isinstance(budget, int) or isinstance(budget, bool):
                raise ValueError("ingredient shortcut tactic details invalid")
            if not isinstance(tactics, list) or not all(isinstance(tactic, str) for tactic in tactics):
                raise ValueError("ingredient shortcut tactic details invalid")
            shortcut_tactic_details = ingredient_shortcut_tactic_gate_details(
                theorem_name=task.theorem_name,
                theorem_type_expr=task.type_expr,
                imports=task.imports,
                tactics=tactics,
                verify_reason=str(shortcut_tactic_details.get("verify_reason", "")),
                max_heartbeats=budget,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e
    try:
        expected_gate_receipt = ingredient_statement_gate_receipt(
            root_path,
            selection=selection,
            active_task_id=task.id,
            active_target_sha256=task.target_sha256,
            theorem_statement_sha256=generation_receipt.theorem_statement_sha256,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr=task.type_expr,
            runner=gate_receipt.runner,
            checks=gate_receipt.checks,
            triviality_details=triviality_details,
            novelty_details=novelty_details,
        )
        expected_shortcut_receipt = ingredient_shortcut_gate_receipt(
            root_path,
            selection=selection,
            active_task_id=task.id,
            active_target_sha256=task.target_sha256,
            theorem_statement_sha256=generation_receipt.theorem_statement_sha256,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr=task.type_expr,
            mathlib_commit=manifest_mathlib_commit,
            theorem_name=task.theorem_name,
            imports=task.imports,
            shortcut_tactic_details=shortcut_tactic_details,
        )
    except (OSError, ValueError, ValidationError) as e:
        raise click.ClickException(str(e)) from e
    return expected_gate_receipt, expected_shortcut_receipt


def _write_regular_output_from(source_path: Path, output_path: Path, label: str) -> None:
    if output_path.is_symlink() or (output_path.exists() and not output_path.is_file()):
        raise click.ClickException(f"{label} output path invalid")
    for parent in (output_path.parent, *output_path.parent.parents):
        if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
            raise click.ClickException(f"{label} output directory invalid")
    try:
        raw = source_path.read_bytes()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_name(output_path.name + ".tmp")
        tmp_path.write_bytes(raw)
        tmp_path.replace(output_path)
        output_path.chmod(0o644)
    except OSError as e:
        raise click.ClickException(f"{label} output write failed: {output_path}") from e


@click.group("ingredients", hidden=True)
def ingredients_cmd() -> None:
    """Inspect ingredient snapshot artifacts."""


@ingredients_cmd.command("inspect")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Ingredient manifest JSON.",
)
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Optional ingredient repository root for component hash verification.",
)
def ingredients_inspect_cmd(manifest_path: Path, root_path: Path | None) -> None:
    """Inspect an ingredient manifest without building a task."""
    from pydantic import ValidationError

    from lemma.supply.ingredients import (
        IngredientManifest,
        ingredient_manifest_component_hashes,
        ingredient_manifest_component_schema_counts,
        ingredient_recipe_artifact_hashes,
        ingredient_repository_report_hashes,
        ingredient_root_mathlib_commit,
    )

    raw = _read_ingredient_manifest_bytes(manifest_path)
    try:
        manifest = IngredientManifest.model_validate_json(raw)
    except ValidationError as e:
        raise click.ClickException("ingredient manifest schema invalid") from e
    _require_canonical_ingredient_manifest(raw, manifest)
    summary: dict[str, object] = {
        "created_at": manifest.created_at,
        "ingredient_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "lemma_corpus_snapshot_sha256": manifest.lemma_corpus_snapshot_sha256,
        "manifest": str(manifest_path),
        "mathlib_commit": manifest.mathlib_commit,
        "recipe_bundle_sha256": manifest.recipe_bundle_sha256,
        "reserve_selector_policy_sha256": manifest.reserve_selector_policy_sha256,
        "schema_version": manifest.schema_version,
    }
    if root_path is not None:
        try:
            actual_hashes = ingredient_manifest_component_hashes(root_path)
        except OSError as e:
            raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        mismatches = [
            field for field, actual in actual_hashes.items() if getattr(manifest, field) != actual
        ]
        if mismatches:
            raise click.ClickException(f"ingredient component hash mismatch: {', '.join(mismatches)}")
        try:
            component_schema_counts = ingredient_manifest_component_schema_counts(
                root_path,
                mathlib_commit=manifest.mathlib_commit,
            )
            report_hashes = ingredient_repository_report_hashes(
                root_path,
                component_schema_counts=component_schema_counts,
                mathlib_commit=manifest.mathlib_commit,
            )
            recipe_artifact_hashes = ingredient_recipe_artifact_hashes(root_path)
            mathlib_commit = ingredient_root_mathlib_commit(root_path)
        except OSError as e:
            raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        if mathlib_commit != manifest.mathlib_commit:
            raise click.ClickException("ingredient mathlib commit mismatch")
        summary["component_count"] = len(actual_hashes)
        summary["component_schema_counts"] = component_schema_counts
        summary["component_schema_status"] = "verified"
        summary["component_status"] = "verified"
        summary["mathlib_commit_status"] = "verified"
        summary["report_count"] = len(report_hashes)
        summary["report_hashes"] = report_hashes
        summary["report_status"] = "verified"
        summary["recipe_artifact_count"] = len(recipe_artifact_hashes)
        summary["recipe_artifact_hashes"] = recipe_artifact_hashes
        summary["recipe_artifact_status"] = "verified"
        summary["root"] = str(root_path)
    click.echo(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
    )


@ingredients_cmd.command("select-receipt")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Ingredient manifest JSON.",
)
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root.",
)
@click.option("--challenge-seed-sha256", default=None, help="Public ingredient challenge seed SHA256.")
@click.option("--queue-position", type=click.IntRange(min=0), default=None, help="Public active slot index.")
@click.option("--active-k", type=click.IntRange(min=1), default=None, help="Public active task count.")
@click.option(
    "--difficulty-lane",
    type=click.Choice(["easy", "medium", "hard", "frontier"]),
    default=None,
    help="Difficulty lane selected from public difficulty state.",
)
@click.option("--netuid", type=click.IntRange(min=0), default=None, help="Netuid used with --epoch-seed.")
@click.option("--tempo", type=click.IntRange(min=0), default=None, help="Tempo used with --epoch-seed.")
@click.option("--epoch-seed", default=None, help="Public epoch seed used to compute the challenge seed.")
@click.option(
    "--difficulty-state-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public difficulty-state JSONL used to derive the active lane and hash.",
)
@click.option(
    "--difficulty-state-sha256",
    default=None,
    help="Public difficulty-state hash used with --epoch-seed.",
)
def ingredients_select_receipt_cmd(
    manifest_path: Path,
    root_path: Path,
    challenge_seed_sha256: str | None,
    queue_position: int | None,
    active_k: int | None,
    difficulty_lane: str | None,
    netuid: int | None,
    tempo: int | None,
    epoch_seed: str | None,
    difficulty_state_jsonl: Path | None,
    difficulty_state_sha256: str | None,
) -> None:
    """Select raw ingredients from a verified repository root."""
    from pydantic import ValidationError

    from lemma.supply.ingredients import (
        DifficultyLane,
        IngredientManifest,
        canonical_sha256,
        ingredient_challenge_seed_sha256,
        ingredient_challenge_slot_seed_sha256,
        ingredient_manifest_component_hashes,
        select_ingredient_receipt_from_root,
    )

    raw = _read_ingredient_manifest_bytes(manifest_path)
    try:
        manifest = IngredientManifest.model_validate_json(raw)
    except ValidationError as e:
        raise click.ClickException("ingredient manifest schema invalid") from e
    _require_canonical_ingredient_manifest(raw, manifest)
    try:
        actual_hashes = ingredient_manifest_component_hashes(root_path)
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    mismatches = [
        field for field, actual in actual_hashes.items() if getattr(manifest, field) != actual
    ]
    if mismatches:
        raise click.ClickException(f"ingredient component hash mismatch: {', '.join(mismatches)}")
    difficulty_state_sha256, difficulty_lane = _ingredient_difficulty_state_context_from_cli(
        difficulty_state_jsonl=difficulty_state_jsonl,
        difficulty_state_sha256=difficulty_state_sha256,
        difficulty_lane=difficulty_lane,
        tempo=tempo,
    )
    seed_context = (netuid, tempo, epoch_seed, difficulty_state_sha256)
    if any(value is not None for value in seed_context):
        if any(value is None for value in seed_context):
            raise click.ClickException(
                "--netuid, --tempo, --epoch-seed, and --difficulty-state-sha256 must be provided together"
            )
        computed_seed = ingredient_challenge_seed_sha256(
            netuid=cast(int, netuid),
            tempo=cast(int, tempo),
            epoch_seed=cast(str, epoch_seed),
            ingredient_manifest_sha256=hashlib.sha256(raw).hexdigest(),
            recipe_bundle_sha256=manifest.recipe_bundle_sha256,
            difficulty_state_sha256=cast(str, difficulty_state_sha256),
        )
        if challenge_seed_sha256 is not None and challenge_seed_sha256 != computed_seed:
            raise click.ClickException("ingredient selection challenge seed mismatch")
        challenge_seed_sha256 = computed_seed
    if challenge_seed_sha256 is None:
        raise click.ClickException(
            "provide --challenge-seed-sha256 or --netuid/--tempo/--epoch-seed/--difficulty-state-sha256"
        )
    if (queue_position is None) != (active_k is None):
        raise click.ClickException("--queue-position and --active-k must be provided together")
    selection_seed_sha256 = challenge_seed_sha256
    if queue_position is not None and active_k is not None:
        try:
            selection_seed_sha256 = ingredient_challenge_slot_seed_sha256(
                challenge_seed_sha256=challenge_seed_sha256,
                queue_position=queue_position,
                active_K=active_k,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e
    if difficulty_lane is None:
        raise click.ClickException("provide --difficulty-lane or --difficulty-state-jsonl")
    try:
        receipt = select_ingredient_receipt_from_root(
            root_path,
            challenge_seed_sha256=selection_seed_sha256,
            difficulty_lane=cast(DifficultyLane, difficulty_lane),
            mathlib_commit=manifest.mathlib_commit,
        )
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except ValidationError as e:
        raise click.ClickException("ingredient selection receipt invalid") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(
        json.dumps(
            {
                "challenge_seed_sha256": challenge_seed_sha256,
                "difficulty_lane": difficulty_lane,
                "ingredient_manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "manifest": str(manifest_path),
                "root": str(root_path),
                "selection": receipt.model_dump(mode="json"),
                "selection_receipt_sha256": canonical_sha256(receipt),
                "selection_seed_sha256": selection_seed_sha256,
                **(
                    {
                        "active_K": active_k,
                        "queue_position": queue_position,
                    }
                    if active_k is not None and queue_position is not None
                    else {}
                ),
                **(
                    {
                        "difficulty_state_sha256": difficulty_state_sha256,
                        "epoch_seed_sha256": hashlib.sha256(cast(str, epoch_seed).encode("utf-8")).hexdigest(),
                        "netuid": netuid,
                        "tempo": tempo,
                    }
                    if epoch_seed is not None and difficulty_state_sha256 is not None
                    else {"difficulty_state_sha256": difficulty_state_sha256}
                    if difficulty_state_sha256 is not None
                    else {}
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


@ingredients_cmd.command("verify-task")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Ingredient manifest JSON.",
)
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root.",
)
@click.option(
    "--task",
    "task_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Ingredient task JSON object.",
)
@click.option(
    "--generation-receipt",
    "generation_receipt_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional public IngredientGenerationReceipt JSON.",
)
@click.option(
    "--generation-receipt-envelope",
    "generation_receipt_envelope_paths",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    help="Optional public IngredientGenerationReceiptEnvelope JSON. May be repeated.",
)
@click.option(
    "--generation-receipt-envelope-quorum",
    type=click.IntRange(min=1),
    default=None,
    help="Required verified receipt envelopes. Defaults to one.",
)
@click.option(
    "--verify-envelope-signatures",
    is_flag=True,
    default=False,
    help="Verify receipt envelope signatures using the envelope signer as an SS58 address.",
)
@click.option("--challenge-seed-sha256", default=None, help="Public ingredient challenge seed SHA256.")
@click.option(
    "--difficulty-lane",
    type=click.Choice(["easy", "medium", "hard", "frontier"]),
    default=None,
    help="Difficulty lane selected from public difficulty state.",
)
@click.option(
    "--difficulty-state-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public difficulty-state JSONL used to derive the active lane and hash.",
)
@click.option("--netuid", type=click.IntRange(min=0), default=None, help="Netuid used with --epoch-seed.")
@click.option(
    "--epoch-seed",
    default=None,
    help="Optional public epoch seed used to recompute the challenge seed.",
)
def ingredients_verify_task_cmd(
    manifest_path: Path,
    root_path: Path,
    task_path: Path,
    generation_receipt_path: Path | None,
    generation_receipt_envelope_paths: tuple[Path, ...],
    generation_receipt_envelope_quorum: int | None,
    verify_envelope_signatures: bool,
    challenge_seed_sha256: str | None,
    difficulty_lane: str | None,
    difficulty_state_jsonl: Path | None,
    netuid: int | None,
    epoch_seed: str | None,
) -> None:
    """Verify an ingredient task against public selection artifacts."""
    from pydantic import ValidationError

    from lemma.supply.ingredients import (
        DifficultyLane,
        IngredientGenerationReceipt,
        IngredientGenerationReceiptEnvelope,
        IngredientManifest,
        Ss58IngredientEnvelopeSignatureVerifier,
        canonical_sha256,
        ingredient_challenge_seed_sha256,
        ingredient_generation_receipt_from_task,
        ingredient_manifest_component_hashes,
        verify_ingredient_generation_receipt_artifact,
        verify_ingredient_generation_receipt_envelope_quorum,
        verify_ingredient_task_against_root,
    )
    from lemma.tasks import LemmaTask

    if generation_receipt_path is not None and generation_receipt_envelope_paths:
        raise click.ClickException(
            "--generation-receipt and --generation-receipt-envelope are mutually exclusive"
        )
    settings = LemmaSettings()
    if settings.protocol_mode == "production":
        missing = []
        if generation_receipt_path is None and not generation_receipt_envelope_paths:
            missing.append("--generation-receipt or --generation-receipt-envelope")
        if difficulty_state_jsonl is None:
            missing.append("--difficulty-state-jsonl")
        if netuid is None:
            missing.append("--netuid")
        if epoch_seed is None:
            missing.append("--epoch-seed")
        if missing:
            raise click.ClickException(
                f"production ingredient task verification requires {', '.join(missing)}"
            )
    if (netuid is None) != (epoch_seed is None):
        raise click.ClickException("--netuid and --epoch-seed must be provided together")
    required_generation_receipt_envelope_quorum = generation_receipt_envelope_quorum or 1
    if not generation_receipt_envelope_paths and (
        generation_receipt_envelope_quorum is not None
        or required_generation_receipt_envelope_quorum > 1
        or verify_envelope_signatures
    ):
        raise click.ClickException(
            "provide --generation-receipt-envelope to verify envelope quorum or signatures"
        )
    signature_verifier = Ss58IngredientEnvelopeSignatureVerifier() if verify_envelope_signatures else None

    def read_public_evidence(path: Path, name: str) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise click.ClickException(f"ingredient {name} path invalid")
        try:
            return path.read_bytes()
        except OSError as e:
            raise click.ClickException(f"ingredient {name} unreadable: {path}") from e

    raw_manifest = _read_ingredient_manifest_bytes(manifest_path)
    try:
        manifest = IngredientManifest.model_validate_json(raw_manifest)
    except ValidationError as e:
        raise click.ClickException("ingredient manifest schema invalid") from e
    _require_canonical_ingredient_manifest(raw_manifest, manifest)
    try:
        actual_hashes = ingredient_manifest_component_hashes(root_path)
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    mismatches = [
        field for field, actual in actual_hashes.items() if getattr(manifest, field) != actual
    ]
    if mismatches:
        raise click.ClickException(f"ingredient component hash mismatch: {', '.join(mismatches)}")
    task_raw = _read_ingredient_task_bytes(task_path)
    try:
        task = LemmaTask.model_validate_json(task_raw)
    except ValidationError as e:
        raise click.ClickException("ingredient task schema invalid") from e
    _require_canonical_json_artifact(task_raw, task, "ingredient task noncanonical")
    try:
        task_receipt = ingredient_generation_receipt_from_task(task)
    except ValidationError as e:
        raise click.ClickException("ingredient task receipt invalid") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    difficulty_state_sha256, difficulty_lane = _ingredient_difficulty_state_context_from_cli(
        difficulty_state_jsonl=difficulty_state_jsonl,
        difficulty_state_sha256=None,
        difficulty_lane=difficulty_lane,
        tempo=task_receipt.tempo,
    )
    if difficulty_lane is None:
        raise click.ClickException("provide --difficulty-lane or --difficulty-state-jsonl")
    if difficulty_state_sha256 is not None and difficulty_state_sha256 != task_receipt.difficulty_state_sha256:
        raise click.ClickException("ingredient task difficulty state sha256 mismatch")
    if challenge_seed_sha256 is None:
        if netuid is None or epoch_seed is None or difficulty_state_sha256 is None:
            raise click.ClickException(
                "provide --challenge-seed-sha256 or --netuid/--epoch-seed/--difficulty-state-jsonl"
            )
        challenge_seed_sha256 = ingredient_challenge_seed_sha256(
            netuid=netuid,
            tempo=task_receipt.tempo,
            epoch_seed=epoch_seed,
            ingredient_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
            recipe_bundle_sha256=manifest.recipe_bundle_sha256,
            difficulty_state_sha256=difficulty_state_sha256,
        )
    try:
        receipt = verify_ingredient_task_against_root(
            task,
            root_path,
            manifest=manifest,
            ingredient_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
            challenge_seed_sha256=challenge_seed_sha256,
            difficulty_lane=cast(DifficultyLane, difficulty_lane),
        )
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except ValidationError as e:
        raise click.ClickException("ingredient task receipt invalid") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    if epoch_seed is not None and netuid is not None:
        if hashlib.sha256(epoch_seed.encode("utf-8")).hexdigest() != receipt.epoch_seed_sha256:
            raise click.ClickException("ingredient task epoch seed mismatch")
        expected_challenge_seed = ingredient_challenge_seed_sha256(
            netuid=netuid,
            tempo=receipt.tempo,
            epoch_seed=epoch_seed,
            ingredient_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
            recipe_bundle_sha256=manifest.recipe_bundle_sha256,
            difficulty_state_sha256=receipt.difficulty_state_sha256,
        )
        if expected_challenge_seed != challenge_seed_sha256:
            raise click.ClickException("ingredient task challenge seed mismatch")
    generation_receipt_status = "reconstructed"
    if generation_receipt_path is not None:
        generation_receipt_raw = read_public_evidence(
            generation_receipt_path, "generation receipt artifact"
        )
        try:
            artifact_receipt = IngredientGenerationReceipt.model_validate_json(generation_receipt_raw)
            _require_canonical_json_artifact(
                generation_receipt_raw,
                artifact_receipt,
                "ingredient generation receipt artifact noncanonical",
            )
            verify_ingredient_generation_receipt_artifact(
                task,
                artifact_receipt,
                root_path,
                manifest=manifest,
                ingredient_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
                challenge_seed_sha256=challenge_seed_sha256,
                difficulty_lane=cast(DifficultyLane, difficulty_lane),
            )
        except ValidationError as e:
            raise click.ClickException("ingredient generation receipt artifact invalid") from e
        except OSError as e:
            raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        generation_receipt_status = "verified"
    generation_receipt_envelope_sha256s = None
    if generation_receipt_envelope_paths:
        try:
            envelopes = []
            for path in generation_receipt_envelope_paths:
                envelope_raw = read_public_evidence(path, "generation receipt envelope")
                envelope = IngredientGenerationReceiptEnvelope.model_validate_json(envelope_raw)
                _require_canonical_json_artifact(
                    envelope_raw,
                    envelope,
                    "ingredient generation receipt envelope noncanonical",
                )
                envelopes.append(envelope)
            verified_envelopes = verify_ingredient_generation_receipt_envelope_quorum(
                task,
                tuple(envelopes),
                root_path,
                manifest=manifest,
                ingredient_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
                challenge_seed_sha256=challenge_seed_sha256,
                difficulty_lane=cast(DifficultyLane, difficulty_lane),
                quorum=required_generation_receipt_envelope_quorum,
                signature_verifier=signature_verifier,
            )
        except ValidationError as e:
            raise click.ClickException("ingredient generation receipt envelope invalid") from e
        except OSError as e:
            raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        generation_receipt_status = "verified"
        generation_receipt_envelope_sha256s = [
            canonical_sha256(envelope) for envelope in verified_envelopes
        ]
    generation_receipt_sha256 = canonical_sha256(receipt)
    summary = {
        "active_task_id": task.id,
        "challenge_seed_sha256": challenge_seed_sha256,
        "difficulty_lane": receipt.selection.difficulty_lane,
        "difficulty_state_sha256": receipt.difficulty_state_sha256,
        "epoch_seed_sha256": receipt.epoch_seed_sha256,
        "generation_receipt_sha256": generation_receipt_sha256,
        "generation_receipt_status": generation_receipt_status,
        "ingredient_manifest_sha256": receipt.ingredient_manifest_sha256,
        "manifest": str(manifest_path),
        "root": str(root_path),
        "selection_receipt_sha256": canonical_sha256(receipt.selection),
        "selected_recipe_id": receipt.selection.selected_recipe_id,
        "selected_selector_id": receipt.selection.selected_selector_id,
        "tempo": receipt.tempo,
        "task": str(task_path),
        "task_status": "verified",
    }
    if netuid is not None:
        summary["netuid"] = netuid
    if generation_receipt_path is not None:
        summary["generation_receipt"] = str(generation_receipt_path)
    if generation_receipt_envelope_paths:
        summary["generation_receipt_envelope_quorum"] = required_generation_receipt_envelope_quorum
        summary["envelope_signature_status"] = "verified" if verify_envelope_signatures else "metadata_only"
        summary["generation_receipt_envelope_sha256s"] = generation_receipt_envelope_sha256s
        summary["generation_receipt_envelopes"] = [
            str(path) for path in generation_receipt_envelope_paths
        ]
    click.echo(json.dumps(summary, indent=2, sort_keys=True))


@ingredients_cmd.command("write-manifest")
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root.",
)
@click.option(
    "--lemma-corpus-snapshot-sha256",
    required=True,
    help="Pinned public Proof Atlas snapshot SHA256.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Manifest output path. Defaults to ROOT/manifest.json.",
)
@click.option(
    "--mathlib-commit",
    default=None,
    help="Optional Mathlib commit override. Defaults to ROOT/mathlib_commit.txt.",
)
def ingredients_write_manifest_cmd(
    root_path: Path,
    lemma_corpus_snapshot_sha256: str,
    output_path: Path | None,
    mathlib_commit: str | None,
) -> None:
    """Write a canonical ingredient manifest from a repository root."""
    from pydantic import ValidationError

    from lemma.supply.ingredients import (
        ingredient_manifest_bytes,
        ingredient_manifest_component_schema_counts,
        ingredient_manifest_from_root,
        ingredient_recipe_artifact_hashes,
        ingredient_repository_report_hashes,
    )

    output_path = output_path or root_path / "manifest.json"
    try:
        manifest = ingredient_manifest_from_root(
            root_path,
            lemma_corpus_snapshot_sha256=lemma_corpus_snapshot_sha256,
            mathlib_commit=mathlib_commit,
        )
        component_schema_counts = ingredient_manifest_component_schema_counts(
            root_path,
            mathlib_commit=manifest.mathlib_commit,
        )
        ingredient_repository_report_hashes(
            root_path,
            component_schema_counts=component_schema_counts,
            mathlib_commit=manifest.mathlib_commit,
        )
        ingredient_recipe_artifact_hashes(root_path)
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except (ValueError, ValidationError) as e:
        raise click.ClickException(str(e)) from e
    raw = ingredient_manifest_bytes(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    click.echo(
        json.dumps(
            {
                "component_count": len(component_schema_counts),
                "component_schema_status": "verified",
                "ingredient_manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "manifest": str(output_path),
                "mathlib_commit": manifest.mathlib_commit,
                "recipe_artifact_status": "verified",
                "report_status": "verified",
            },
            indent=2,
            sort_keys=True,
        )
    )


@ingredients_cmd.command("extract-mathlib")
@click.option(
    "--mathlib-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Pinned Mathlib checkout root.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root to write raw Mathlib ingredients into.",
)
@click.option("--include", "includes", multiple=True, help="Repo-relative glob, e.g. Mathlib/Data/Nat/*.lean.")
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.option("--mathlib-commit", default=None, help="Override git-derived Mathlib commit.")
@click.option("--source-license", default="Apache-2.0", show_default=True)
def ingredients_extract_mathlib_cmd(
    mathlib_root: Path,
    output_path: Path,
    includes: tuple[str, ...],
    limit: int | None,
    mathlib_commit: str | None,
    source_license: str,
) -> None:
    """Extract raw Mathlib ingredients into the ingredient repository layout."""
    from lemma.supply.ingredients import MathlibDefinitionLike, write_mathlib_ingredient_extract
    from lemma.supply.mathlib_extract import ExtractConfig, extract_definition_rows, extract_snapshot_rows

    try:
        config = ExtractConfig(
            mathlib_root=mathlib_root,
            includes=includes or ("Mathlib/**/*.lean",),
            limit=limit,
            mathlib_rev=mathlib_commit,
            source_license=source_license,
        )
        rows = extract_snapshot_rows(config)
        definitions = extract_definition_rows(config)
        summary = write_mathlib_ingredient_extract(
            rows,
            output_path,
            definitions=cast(Iterable[MathlibDefinitionLike], definitions),
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(summary, indent=2, sort_keys=True))


@ingredients_cmd.command("build-compatibility")
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root.",
)
@click.option(
    "--no-paid-recipes",
    is_flag=True,
    help="Write/verify only the empty compatibility scaffold for raw ingredient repositories.",
)
def ingredients_build_compatibility_cmd(root_path: Path, no_paid_recipes: bool) -> None:
    """Build public compatibility artifacts for an ingredient repository."""
    from lemma.supply.ingredients import build_empty_ingredient_compatibility, build_ingredient_compatibility

    try:
        summary = (
            build_empty_ingredient_compatibility(root_path)
            if no_paid_recipes
            else build_ingredient_compatibility(root_path)
        )
    except (OSError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(summary, indent=2, sort_keys=True))


@click.command("assemble-ingredient-active-registry")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Ingredient manifest JSON.",
)
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root.",
)
@click.option(
    "--bundle",
    "bundle_paths",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    multiple=True,
    required=True,
    help="One slot bundle from tasks build-ingredient-task. Repeat for every active slot.",
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option(
    "--novelty-cache-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public novelty cache JSONL required when slot gate receipts claim novelty checks.",
)
@click.option(
    "--difficulty-state-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public difficulty-state JSONL used to verify each slot seed context.",
)
@click.option("--epoch-seed", default=None, help="Public epoch seed used to recompute each slot seed.")
def tasks_assemble_ingredient_active_registry_cmd(
    manifest_path: Path,
    root_path: Path,
    bundle_paths: tuple[Path, ...],
    output_path: Path,
    novelty_cache_jsonl: Path | None,
    difficulty_state_jsonl: Path | None,
    epoch_seed: str | None,
) -> None:
    """Assemble a complete ingredient active registry from public slot bundles."""
    import tempfile

    from pydantic import ValidationError

    from lemma.supply.ingredients import (
        IngredientGateReceipt,
        IngredientGenerationReceipt,
        IngredientGenerationReceiptEnvelope,
        IngredientManifest,
        IngredientSelectionReceipt,
        IngredientTaskArtifactManifest,
        IngredientTaskArtifactRef,
        canonical_json_bytes,
        canonical_sha256,
        ingredient_challenge_seed_sha256,
        ingredient_challenge_slot_seed_sha256,
        ingredient_manifest_component_hashes,
        verify_ingredient_task_against_root,
    )
    from lemma.task_supply import write_registry
    from lemma.tasks import LemmaTask, TaskError, load_task_registry

    settings = LemmaSettings()
    if settings.protocol_mode == "production":
        missing = []
        if difficulty_state_jsonl is None:
            missing.append("--difficulty-state-jsonl")
        if epoch_seed is None:
            missing.append("--epoch-seed")
        if novelty_cache_jsonl is None:
            missing.append("--novelty-cache-jsonl")
        if missing:
            raise click.ClickException(
                f"production ingredient active-registry assembly requires {', '.join(missing)}"
            )

    raw_manifest = _read_ingredient_manifest_bytes(manifest_path)
    try:
        manifest = IngredientManifest.model_validate_json(raw_manifest)
    except ValidationError as e:
        raise click.ClickException("ingredient manifest schema invalid") from e
    _require_canonical_ingredient_manifest(raw_manifest, manifest)
    ingredient_manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    try:
        actual_hashes = ingredient_manifest_component_hashes(root_path)
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    mismatches = [
        field for field, actual in actual_hashes.items() if getattr(manifest, field) != actual
    ]
    if mismatches:
        raise click.ClickException(f"ingredient component hash mismatch: {', '.join(mismatches)}")

    artifact_paths = {
        "active_registry": "active-registry.json",
        "gate_receipt": "gate-receipt.json",
        "generation_receipt": "generation-receipt.json",
        "generation_receipt_envelope": "generation-receipt-envelope.json",
        "selection_receipt": "selection-receipt.json",
        "shortcut_receipt": "shortcut-receipt.json",
        "task": "task.json",
    }

    def read_artifact(bundle_path: Path, ref: IngredientTaskArtifactRef, name: str) -> bytes:
        relative = Path(ref.path)
        if ref.path != artifact_paths[name] or relative.is_absolute() or ".." in relative.parts:
            raise click.ClickException(f"ingredient task artifact path invalid: {name}")
        path = bundle_path / relative
        if path.is_symlink() or not path.is_file():
            raise click.ClickException(f"ingredient task artifact path invalid: {name}")
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise click.ClickException(f"ingredient task artifact unreadable: {name}") from e
        if hashlib.sha256(raw).hexdigest() != ref.sha256:
            raise click.ClickException(f"ingredient task artifact hash mismatch: {name}")
        return raw

    def read_model(raw: bytes, model_type: type[BaseModel], name: str) -> BaseModel:
        try:
            model = model_type.model_validate_json(raw)
        except ValidationError as e:
            raise click.ClickException("ingredient task artifact schema invalid") from e
        _require_canonical_json_artifact(
            raw,
            model,
            f"ingredient task artifact noncanonical: {name}",
        )
        return model

    def require_exact_bundle_contents(bundle_path: Path) -> None:
        expected = {"artifact-manifest.json", *artifact_paths.values()}
        try:
            entries = tuple(bundle_path.iterdir())
        except OSError as e:
            raise click.ClickException(f"ingredient task bundle unreadable: {bundle_path}") from e
        for entry in entries:
            if entry.name not in expected or entry.is_symlink() or not entry.is_file():
                raise click.ClickException(f"ingredient task artifact unexpected path: {entry.name}")
        present = {entry.name for entry in entries}
        missing = sorted(expected - present)
        if missing:
            raise click.ClickException(f"ingredient task artifact missing: {missing[0]}")

    slots: list[tuple[int, LemmaTask, IngredientTaskArtifactManifest]] = []
    seen_positions: set[int] = set()
    seen_task_ids: set[str] = set()
    seen_selection_seeds: set[str] = set()
    common_context: dict[str, object] | None = None

    for bundle_path in bundle_paths:
        if bundle_path.is_symlink() or not bundle_path.is_dir():
            raise click.ClickException("ingredient task bundle path invalid")
        artifact_manifest_path = bundle_path / "artifact-manifest.json"
        if artifact_manifest_path.is_symlink() or not artifact_manifest_path.is_file():
            raise click.ClickException("ingredient task artifact manifest path invalid")
        try:
            raw_artifact_manifest = artifact_manifest_path.read_bytes()
            artifact_payload = json.loads(raw_artifact_manifest)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise click.ClickException("ingredient task artifact manifest invalid JSON") from e
        if not isinstance(artifact_payload, dict):
            raise click.ClickException("ingredient task artifact manifest invalid JSON")
        if raw_artifact_manifest != canonical_json_bytes(artifact_payload) + b"\n":
            raise click.ClickException("ingredient task artifact manifest noncanonical")
        try:
            artifact_manifest = IngredientTaskArtifactManifest.model_validate(artifact_payload)
        except ValidationError as e:
            raise click.ClickException("ingredient task artifact manifest schema invalid") from e
        if artifact_manifest.ingredient_manifest_sha256 != ingredient_manifest_sha256:
            raise click.ClickException("ingredient task artifact manifest ingredient manifest mismatch")
        require_exact_bundle_contents(bundle_path)

        task = cast(
            LemmaTask,
            read_model(read_artifact(bundle_path, artifact_manifest.artifacts.task, "task"), LemmaTask, "task"),
        )
        selection = cast(
            IngredientSelectionReceipt,
            read_model(
                read_artifact(
                    bundle_path,
                    artifact_manifest.artifacts.selection_receipt,
                    "selection_receipt",
                ),
                IngredientSelectionReceipt,
                "selection_receipt",
            ),
        )
        gate_receipt = cast(
            IngredientGateReceipt,
            read_model(
                read_artifact(bundle_path, artifact_manifest.artifacts.gate_receipt, "gate_receipt"),
                IngredientGateReceipt,
                "gate_receipt",
            ),
        )
        shortcut_receipt = cast(
            IngredientGateReceipt,
            read_model(
                read_artifact(
                    bundle_path,
                    artifact_manifest.artifacts.shortcut_receipt,
                    "shortcut_receipt",
                ),
                IngredientGateReceipt,
                "shortcut_receipt",
            ),
        )
        receipt = cast(
            IngredientGenerationReceipt,
            read_model(
                read_artifact(
                    bundle_path,
                    artifact_manifest.artifacts.generation_receipt,
                    "generation_receipt",
                ),
                IngredientGenerationReceipt,
                "generation_receipt",
            ),
        )
        envelope = cast(
            IngredientGenerationReceiptEnvelope,
            read_model(
                read_artifact(
                    bundle_path,
                    artifact_manifest.artifacts.generation_receipt_envelope,
                    "generation_receipt_envelope",
                ),
                IngredientGenerationReceiptEnvelope,
                "generation_receipt_envelope",
            ),
        )
        registry_raw = read_artifact(
            bundle_path,
            artifact_manifest.artifacts.active_registry,
            "active_registry",
        )
        try:
            slot_registry = load_task_registry(registry_raw, strict_top_level=True)
        except TaskError as e:
            raise click.ClickException(str(e)) from e
        if slot_registry.tasks != (task,):
            raise click.ClickException("ingredient task artifact registry mismatch")

        if task.id in seen_task_ids:
            raise click.ClickException("ingredient active registry task id duplicated")
        seen_task_ids.add(task.id)
        if artifact_manifest.queue_position in seen_positions:
            raise click.ClickException("ingredient active registry slot duplicated")
        seen_positions.add(artifact_manifest.queue_position)
        selection_seed = artifact_manifest.selection_seed_sha256 or artifact_manifest.challenge_seed_sha256
        if selection_seed in seen_selection_seeds:
            raise click.ClickException("ingredient active registry selection seed duplicated")
        seen_selection_seeds.add(selection_seed)

        context = {
            "active_K": artifact_manifest.active_K,
            "challenge_seed_sha256": artifact_manifest.challenge_seed_sha256,
            "difficulty_lane": artifact_manifest.difficulty_lane,
            "difficulty_state_sha256": artifact_manifest.difficulty_state_sha256,
            "epoch_seed_sha256": artifact_manifest.epoch_seed_sha256,
            "ingredient_repo_commit": artifact_manifest.ingredient_repo_commit,
            "lemma_corpus_snapshot_sha256": artifact_manifest.lemma_corpus_snapshot_sha256,
            "mathlib_commit": artifact_manifest.mathlib_commit,
            "netuid": artifact_manifest.netuid,
            "recipe_bundle_sha256": artifact_manifest.recipe_bundle_sha256,
            "tempo": artifact_manifest.tempo,
        }
        if common_context is None:
            common_context = context
        elif context != common_context:
            raise click.ClickException("ingredient active registry slot context mismatch")

        if (
            artifact_manifest.active_task_id != task.id
            or receipt.active_task_id != task.id
            or artifact_manifest.active_target_sha256 != task.target_sha256
            or artifact_manifest.theorem_statement_sha256 != receipt.theorem_statement_sha256
            or artifact_manifest.active_K != receipt.active_K
            or artifact_manifest.queue_position != task.queue_position
            or artifact_manifest.epoch_seed_sha256 != receipt.epoch_seed_sha256
            or artifact_manifest.difficulty_state_sha256 != receipt.difficulty_state_sha256
            or artifact_manifest.difficulty_lane != receipt.selection.difficulty_lane
            or selection_seed != receipt.selection.selection_seed_sha256
            or receipt.selection != selection
            or envelope.generation_receipt != receipt
        ):
            raise click.ClickException("ingredient active registry slot artifact mismatch")
        if (
            artifact_manifest.selection_receipt_sha256 != canonical_sha256(selection)
            or artifact_manifest.gate_receipt_sha256 != canonical_sha256(gate_receipt)
            or artifact_manifest.shortcut_receipt_sha256 != canonical_sha256(shortcut_receipt)
            or artifact_manifest.generation_receipt_sha256 != canonical_sha256(receipt)
            or artifact_manifest.generation_receipt_envelope_sha256 != canonical_sha256(envelope)
            or receipt.gate_receipt_sha256 != canonical_sha256(gate_receipt)
            or receipt.shortcut_receipt_sha256 != canonical_sha256(shortcut_receipt)
        ):
            raise click.ClickException("ingredient active registry slot receipt mismatch")
        expected_gate_receipt, expected_shortcut_receipt = _expected_ingredient_gate_receipts(
            root_path=root_path,
            manifest_mathlib_commit=manifest.mathlib_commit,
            selection=selection,
            task=task,
            gate_receipt=gate_receipt,
            shortcut_receipt=shortcut_receipt,
            generation_receipt=receipt,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            novelty_cache_jsonl=novelty_cache_jsonl,
            production=settings.protocol_mode == "production",
            context="active-registry assembly",
        )
        if gate_receipt != expected_gate_receipt:
            raise click.ClickException("ingredient active registry slot gate receipt mismatch")
        if shortcut_receipt != expected_shortcut_receipt:
            raise click.ClickException("ingredient active registry slot shortcut receipt mismatch")
        if epoch_seed is not None:
            if hashlib.sha256(epoch_seed.encode("utf-8")).hexdigest() != artifact_manifest.epoch_seed_sha256:
                raise click.ClickException("ingredient active registry epoch seed mismatch")
            expected_challenge_seed = ingredient_challenge_seed_sha256(
                netuid=artifact_manifest.netuid,
                tempo=artifact_manifest.tempo,
                epoch_seed=epoch_seed,
                ingredient_manifest_sha256=ingredient_manifest_sha256,
                recipe_bundle_sha256=manifest.recipe_bundle_sha256,
                difficulty_state_sha256=artifact_manifest.difficulty_state_sha256,
            )
            if expected_challenge_seed != artifact_manifest.challenge_seed_sha256:
                raise click.ClickException("ingredient active registry challenge seed mismatch")
            try:
                expected_selection_seed = ingredient_challenge_slot_seed_sha256(
                    challenge_seed_sha256=expected_challenge_seed,
                    queue_position=artifact_manifest.queue_position,
                    active_K=artifact_manifest.active_K,
                )
            except ValueError as e:
                raise click.ClickException(str(e)) from e
            if expected_selection_seed != receipt.selection.selection_seed_sha256:
                raise click.ClickException("ingredient active registry selection seed mismatch")
        _ingredient_difficulty_state_context_from_cli(
            difficulty_state_jsonl=difficulty_state_jsonl,
            difficulty_state_sha256=artifact_manifest.difficulty_state_sha256,
            difficulty_lane=artifact_manifest.difficulty_lane,
            tempo=artifact_manifest.tempo,
        )
        try:
            verified_receipt = verify_ingredient_task_against_root(
                task,
                root_path,
                manifest=manifest,
                ingredient_manifest_sha256=ingredient_manifest_sha256,
                challenge_seed_sha256=artifact_manifest.challenge_seed_sha256,
                difficulty_lane=receipt.selection.difficulty_lane,
            )
        except (OSError, ValueError, ValidationError) as e:
            raise click.ClickException(str(e)) from e
        if verified_receipt != receipt:
            raise click.ClickException("ingredient active registry slot receipt mismatch")

        slots.append((artifact_manifest.queue_position, task, artifact_manifest))

    if common_context is None:
        raise click.ClickException("ingredient active registry requires at least one slot bundle")
    active_k = cast(int, common_context["active_K"])
    if len(slots) != active_k:
        raise click.ClickException("ingredient active registry bundle count mismatch")
    if seen_positions != set(range(active_k)):
        raise click.ClickException("ingredient active registry slot coverage mismatch")
    if settings.protocol_mode == "production" and active_k != settings.active_task_count:
        raise click.ClickException("production ingredient active-registry assembly active K mismatch")

    ordered_slots = sorted(slots, key=lambda item: item[0])
    ordered_tasks = tuple(task for _position, task, _artifact in ordered_slots)
    with tempfile.TemporaryDirectory() as tmp_dir:
        staged_registry = Path(tmp_dir) / "active-registry.json"
        write_registry(ordered_tasks, staged_registry)
        _write_regular_output_from(staged_registry, output_path, "ingredient active registry")
    click.echo(
        json.dumps(
            {
                "active_K": active_k,
                "active_registry": str(output_path),
                "active_registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "active_task_ids": [task.id for task in ordered_tasks],
                "bundle_count": len(bundle_paths),
                "challenge_seed_sha256": common_context["challenge_seed_sha256"],
                "difficulty_lane": common_context["difficulty_lane"],
                "difficulty_state_sha256": common_context["difficulty_state_sha256"],
                "epoch_seed_sha256": common_context["epoch_seed_sha256"],
                "queue_positions": [position for position, _task, _artifact in ordered_slots],
                "tempo": common_context["tempo"],
            },
            indent=2,
            sort_keys=True,
        )
    )


@click.command("build-ingredient-task")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Ingredient manifest JSON.",
)
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root.",
)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory for the task, receipt, envelope, registry, and artifact manifest.",
)
@click.option(
    "--active-registry-output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional active-registry JSON output/cache path to atomically write from the built bundle.",
)
@click.option("--netuid", type=click.IntRange(min=0), required=True)
@click.option("--tempo", type=click.IntRange(min=0), required=True)
@click.option("--epoch-seed", required=True)
@click.option("--queue-position", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--active-k", type=click.IntRange(min=1), default=None)
@click.option("--difficulty-state-sha256", default=None)
@click.option(
    "--difficulty-state-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public difficulty-state JSONL used to derive the active lane and hash.",
)
@click.option(
    "--difficulty-lane",
    type=click.Choice(["easy", "medium", "hard", "frontier"]),
    default=None,
)
@click.option("--ingredient-repo-commit", required=True)
@click.option("--active-task-id", required=True)
@click.option("--theorem-name", required=True)
@click.option("--type-expr", default=None)
@click.option("--statement", default=None)
@click.option(
    "--statement-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--realize-selected-recipe",
    is_flag=True,
    default=False,
    help="Synthesize the theorem type and statement from the public selected recipe.",
)
@click.option("--gate-receipt-sha256", default=None, help="Optional expected statement-gate receipt hash.")
@click.option("--shortcut-receipt-sha256", default=None, help="Optional expected shortcut-gate receipt hash.")
@click.option(
    "--run-statement-gate",
    is_flag=True,
    default=False,
    help="Run Lean on the generated Challenge.lean statement before writing the statement-gate receipt.",
)
@click.option(
    "--run-soundness-template-gate",
    is_flag=True,
    default=False,
    help="Run Lean on the selected recipe soundness template before writing the statement-gate receipt.",
)
@click.option(
    "--run-triviality-gate",
    is_flag=True,
    default=False,
    help="Run the bounded Lean baseline tactic stack and reject theorem types it proves.",
)
@click.option(
    "--run-shortcut-tactic-gate",
    is_flag=True,
    default=False,
    help="Run selected recipe simp/aesop/omega/grind shortcut tactic checks and reject theorem types they prove.",
)
@click.option(
    "--novelty-cache-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public novelty cache JSONL used to reject previously seen theorem types.",
)
@click.option("--title", default="")
@click.option("--import", "imports", multiple=True, default=("Mathlib",), show_default=True)
@click.option("--evidence-signer-id", default=None)
@click.option("--signature", default=None)
@click.option("--evidence-key-uri", default=None, help="Development evidence signer URI, for example //Alice.")
def tasks_build_ingredient_task_cmd(
    manifest_path: Path,
    root_path: Path,
    output_dir: Path,
    active_registry_output: Path | None,
    netuid: int,
    tempo: int,
    epoch_seed: str,
    queue_position: int,
    active_k: int | None,
    difficulty_state_sha256: str | None,
    difficulty_state_jsonl: Path | None,
    difficulty_lane: str | None,
    ingredient_repo_commit: str,
    active_task_id: str,
    theorem_name: str,
    type_expr: str | None,
    statement: str | None,
    statement_file: Path | None,
    realize_selected_recipe: bool,
    gate_receipt_sha256: str | None,
    shortcut_receipt_sha256: str | None,
    run_statement_gate: bool,
    run_soundness_template_gate: bool,
    run_triviality_gate: bool,
    run_shortcut_tactic_gate: bool,
    novelty_cache_jsonl: Path | None,
    title: str,
    imports: tuple[str, ...],
    evidence_signer_id: str | None,
    signature: str | None,
    evidence_key_uri: str | None,
) -> None:
    """Build a public ingredient task artifact bundle from verified repository inputs."""
    from pydantic import ValidationError

    from lemma.problems.base import Problem
    from lemma.supply.ingredients import (
        DifficultyLane,
        IngredientManifest,
        build_ingredient_generation_receipt,
        build_ingredient_task,
        canonical_json_bytes,
        canonical_sha256,
        ingredient_challenge_seed_sha256,
        ingredient_challenge_slot_seed_sha256,
        ingredient_generation_receipt_envelope,
        ingredient_generation_receipt_envelope_signing_payload,
        ingredient_manifest_component_hashes,
        ingredient_novelty_family_hash,
        ingredient_novelty_gate_details,
        ingredient_shortcut_gate_receipt,
        ingredient_shortcut_tactic_gate_details,
        ingredient_shortcut_tactic_probe_script,
        ingredient_shortcut_tactics_for_selection,
        ingredient_soundness_template_source,
        ingredient_soundness_witness_probe_script,
        ingredient_statement_gate_receipt,
        ingredient_triviality_gate_details,
        ingredient_triviality_probe_script,
        realize_ingredient_theorem_statement,
        select_ingredient_receipt_from_root,
        text_sha256,
        validate_ingredient_imports,
        validate_ingredient_statement_header,
        validate_ingredient_task_title,
        validate_ingredient_theorem_name,
        verify_ingredient_generation_receipt_envelope,
        verify_ingredient_task_against_root,
    )
    from lemma.task_supply import DEFAULT_TOOLCHAIN, write_registry
    from lemma.tasks import problem_target_sha256

    if realize_selected_recipe:
        if type_expr is not None or statement is not None or statement_file is not None:
            raise click.ClickException(
                "--realize-selected-recipe cannot be combined with --type-expr, --statement, or --statement-file"
            )
    else:
        if type_expr is None:
            raise click.ClickException("provide --type-expr or --realize-selected-recipe")
        if (statement is None) == (statement_file is None):
            raise click.ClickException("provide exactly one of --statement or --statement-file")
    if (evidence_signer_id is None) != (signature is None):
        raise click.ClickException("--evidence-signer-id and --signature must be provided together")
    if evidence_key_uri and (evidence_signer_id is not None or signature is not None):
        raise click.ClickException("--evidence-key-uri cannot be combined with --evidence-signer-id/--signature")
    if run_soundness_template_gate and not run_statement_gate:
        raise click.ClickException("--run-soundness-template-gate requires --run-statement-gate")
    if run_triviality_gate and not run_statement_gate:
        raise click.ClickException("--run-triviality-gate requires --run-statement-gate")
    if run_shortcut_tactic_gate and not run_statement_gate:
        raise click.ClickException("--run-shortcut-tactic-gate requires --run-statement-gate")
    if novelty_cache_jsonl is not None and not run_statement_gate:
        raise click.ClickException("--novelty-cache-jsonl requires --run-statement-gate")
    settings = LemmaSettings()
    active_k_value = active_k if active_k is not None else 1
    if settings.protocol_mode == "production":
        missing = []
        if not realize_selected_recipe:
            missing.append("--realize-selected-recipe")
        if difficulty_state_jsonl is None:
            missing.append("--difficulty-state-jsonl")
        if not run_statement_gate:
            missing.append("--run-statement-gate")
        if not run_soundness_template_gate:
            missing.append("--run-soundness-template-gate")
        if not run_triviality_gate:
            missing.append("--run-triviality-gate")
        if novelty_cache_jsonl is None:
            missing.append("--novelty-cache-jsonl")
        if missing:
            raise click.ClickException(
                f"production ingredient task artifact requires {', '.join(missing)}"
            )
        if active_k is not None and active_k_value != settings.active_task_count:
            raise click.ClickException("production ingredient task artifact --active-k must match LEMMA_ACTIVE_K")
        active_k_value = settings.active_task_count
        if active_k_value != 1 and active_registry_output is not None:
            raise click.ClickException(
                "production ingredient task artifact active-registry output requires "
                "assemble-ingredient-active-registry"
            )
    if queue_position >= active_k_value:
        raise click.ClickException("ingredient task artifact slot invalid")
    if output_dir.is_symlink():
        raise click.ClickException("ingredient task artifact output directory invalid")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise click.ClickException("ingredient task artifact output directory invalid")
        try:
            next(output_dir.iterdir())
        except StopIteration:
            pass
        except OSError as e:
            raise click.ClickException(f"ingredient task artifact output directory unreadable: {output_dir}") from e
        else:
            raise click.ClickException("ingredient task artifact output directory must be empty")

    raw_manifest = _read_ingredient_manifest_bytes(manifest_path)
    try:
        manifest = IngredientManifest.model_validate_json(raw_manifest)
    except ValidationError as e:
        raise click.ClickException("ingredient manifest schema invalid") from e
    _require_canonical_ingredient_manifest(raw_manifest, manifest)
    try:
        actual_hashes = ingredient_manifest_component_hashes(root_path)
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    mismatches = [
        field for field, actual in actual_hashes.items() if getattr(manifest, field) != actual
    ]
    if mismatches:
        raise click.ClickException(f"ingredient component hash mismatch: {', '.join(mismatches)}")
    if manifest.lemma_corpus_snapshot_sha256 is None:
        raise click.ClickException("ingredient manifest requires lemma_corpus_snapshot_sha256")
    difficulty_state_sha256, difficulty_lane = _ingredient_difficulty_state_context_from_cli(
        difficulty_state_jsonl=difficulty_state_jsonl,
        difficulty_state_sha256=difficulty_state_sha256,
        difficulty_lane=difficulty_lane,
        tempo=tempo,
    )
    if difficulty_state_sha256 is None:
        raise click.ClickException("provide --difficulty-state-sha256 or --difficulty-state-jsonl")
    if difficulty_lane is None:
        raise click.ClickException("provide --difficulty-lane or --difficulty-state-jsonl")

    ingredient_manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    challenge_seed_sha256 = ingredient_challenge_seed_sha256(
        netuid=netuid,
        tempo=tempo,
        epoch_seed=epoch_seed,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        recipe_bundle_sha256=manifest.recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
    )
    try:
        selection_seed_sha256 = ingredient_challenge_slot_seed_sha256(
            challenge_seed_sha256=challenge_seed_sha256,
            queue_position=queue_position,
            active_K=active_k_value,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    try:
        selection = select_ingredient_receipt_from_root(
            root_path,
            challenge_seed_sha256=selection_seed_sha256,
            difficulty_lane=cast(DifficultyLane, difficulty_lane),
            mathlib_commit=manifest.mathlib_commit,
        )
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except (ValidationError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if realize_selected_recipe:
        try:
            type_expr, statement_text = realize_ingredient_theorem_statement(
                root_path,
                selection=selection,
                theorem_name=theorem_name,
            )
        except (OSError, ValueError, ValidationError) as e:
            raise click.ClickException(str(e)) from e
    else:
        type_expr = cast(str, type_expr)
        statement_text = (
            statement if statement is not None else _read_ingredient_statement_text(cast(Path, statement_file))
        )
    try:
        validate_ingredient_theorem_name(theorem_name)
        validate_ingredient_imports(imports)
        validate_ingredient_task_title(title)
        validate_ingredient_statement_header(
            theorem_name=theorem_name,
            type_expr=type_expr,
            statement=statement_text,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    theorem_statement_sha256 = hashlib.sha256(statement_text.encode("utf-8")).hexdigest()
    active_target_sha256 = problem_target_sha256(
        Problem(
            id=active_task_id,
            theorem_name=theorem_name,
            type_expr=type_expr,
            split="ingredient",
            lean_toolchain=DEFAULT_TOOLCHAIN,
            mathlib_rev=manifest.mathlib_commit,
            imports=imports,
            extra={"challenge_full": statement_text},
        )
    )
    selection_receipt_sha256 = canonical_sha256(selection)
    statement_gate_checks: tuple[str, ...] = (
        "statement_hash_bound",
        "target_hash_bound",
        "soundness_template_bound",
    )
    statement_gate_runner = "declared-public-artifact"

    def probe_submission_for(theorem: str, theorem_type: str, modules: tuple[str, ...]) -> str:
        return "\n".join(
            [
                *(f"import {module}" for module in modules),
                "",
                "namespace Submission",
                "",
                f"theorem {theorem} : {theorem_type} := by",
                "  sorry",
                "",
                "end Submission",
                "",
            ]
        )

    if run_statement_gate:
        from lemma.lean.verify_runner import run_lean_verify

        gate_problem = Problem(
            id=active_task_id,
            theorem_name=theorem_name,
            type_expr=type_expr,
            split="ingredient",
            lean_toolchain=DEFAULT_TOOLCHAIN,
            mathlib_rev=manifest.mathlib_commit,
            imports=imports,
            extra={
                "challenge_full": statement_text,
                "ingredient_gate_kind": "statement",
                "lean_build_target": "Challenge",
                "lean_skip_axiom_check": True,
                "lean_skip_submission_axiom_check": True,
            },
        )
        statement_result = run_lean_verify(
            settings,
            verify_timeout_s=settings.lean_verify_timeout_s,
            problem=gate_problem,
            proof_script=probe_submission_for(theorem_name, type_expr, imports),
            submission_policy="strict_envelope",
        )
        if not statement_result.passed:
            raise click.ClickException(f"ingredient statement gate failed: {statement_result.reason}")
        statement_gate_checks = (
            "lean_challenge_typechecked",
            f"lean_verify_reason:{statement_result.reason}",
            "statement_hash_bound",
            "target_hash_bound",
            "soundness_template_bound",
        )
        statement_gate_runner = "lean-statement-gate"
    if run_soundness_template_gate:
        from lemma.lean.verify_runner import run_lean_verify

        try:
            soundness_template_path, soundness_template_bytes = ingredient_soundness_template_source(
                root_path,
                selection,
            )
        except (OSError, ValueError, ValidationError) as e:
            raise click.ClickException(str(e)) from e
        try:
            soundness_template_text = soundness_template_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise click.ClickException("ingredient statement gate soundness template invalid") from e
        soundness_proof_script = probe_submission_for(theorem_name, type_expr, imports)
        soundness_witness_checked = False
        if realize_selected_recipe:
            try:
                soundness_proof_script = ingredient_soundness_witness_probe_script(
                    root_path,
                    selection=selection,
                    theorem_name=theorem_name,
                    theorem_type_expr=type_expr,
                    imports=imports,
                )
            except ValueError as e:
                raise click.ClickException(str(e)) from e
            soundness_witness_checked = True
        soundness_problem = Problem(
            id=active_task_id,
            theorem_name=theorem_name,
            type_expr=type_expr,
            split="ingredient",
            lean_toolchain=DEFAULT_TOOLCHAIN,
            mathlib_rev=manifest.mathlib_commit,
            imports=imports,
            extra={
                "challenge_full": soundness_template_text,
                "ingredient_gate_kind": "soundness_template",
                "lean_build_target": "Challenge",
                "lean_skip_axiom_check": True,
                "lean_skip_submission_axiom_check": True,
            },
        )
        soundness_result = run_lean_verify(
            settings,
            verify_timeout_s=settings.lean_verify_timeout_s,
            problem=soundness_problem,
            proof_script=soundness_proof_script,
            submission_policy="strict_envelope",
        )
        if not soundness_result.passed:
            raise click.ClickException(
                f"ingredient soundness template gate failed: {soundness_template_path}: {soundness_result.reason}"
            )
        statement_gate_checks = (
            *statement_gate_checks,
            "soundness_template_typechecked",
            "soundness_template_no_holes",
            *(("soundness_template_witness_checked",) if soundness_witness_checked else ()),
            f"soundness_template_verify_reason:{soundness_result.reason}",
        )
    triviality_details = None
    if run_triviality_gate:
        from lemma.lean.verify_runner import run_lean_verify

        triviality_problem = Problem(
            id=active_task_id,
            theorem_name=theorem_name,
            type_expr=type_expr,
            split="ingredient",
            lean_toolchain=DEFAULT_TOOLCHAIN,
            mathlib_rev=manifest.mathlib_commit,
            imports=imports,
            extra={
                "ingredient_gate_kind": "triviality",
                "lean_max_heartbeats": settings.procedural_triviality_budget_heartbeats,
            },
        )
        triviality_result = run_lean_verify(
            settings,
            verify_timeout_s=settings.procedural_gate_timeout_s,
            problem=triviality_problem,
            proof_script=ingredient_triviality_probe_script(
                theorem_name=theorem_name,
                theorem_type_expr=type_expr,
                imports=imports,
            ),
            submission_policy="strict_envelope",
        )
        if triviality_result.passed:
            raise click.ClickException("ingredient triviality gate failed: baseline solved theorem type")
        if triviality_result.reason != "compile_error":
            raise click.ClickException(f"ingredient triviality gate inconclusive: {triviality_result.reason}")
        triviality_details = ingredient_triviality_gate_details(
            theorem_name=theorem_name,
            theorem_type_expr=type_expr,
            imports=imports,
            verify_reason=triviality_result.reason,
            max_heartbeats=settings.procedural_triviality_budget_heartbeats,
        )
        statement_gate_checks = (
            *statement_gate_checks,
            "bounded_triviality_checked",
            "baseline_triviality_not_solved",
            f"bounded_triviality_reason:{triviality_result.reason}",
        )
    novelty_details = None
    if novelty_cache_jsonl is not None:
        from lemma.supply.novelty import read_novelty_cache

        try:
            novelty_details = ingredient_novelty_gate_details(
                theorem_type_expr=type_expr,
                novelty_cache=read_novelty_cache(novelty_cache_jsonl, strict_statement_hash_rows=True),
                selection=selection,
            )
        except (OSError, ValueError) as e:
            raise click.ClickException(str(e)) from e
        statement_gate_checks = (
            *statement_gate_checks,
            "novelty_cache_bound",
            "theorem_type_not_in_novelty_cache",
            "selection_family_not_in_novelty_cache",
        )
    shortcut_tactic_details = None
    try:
        shortcut_tactics = ingredient_shortcut_tactics_for_selection(root_path, selection)
    except (OSError, ValueError, ValidationError) as e:
        raise click.ClickException(str(e)) from e
    if shortcut_tactics:
        if not run_shortcut_tactic_gate:
            raise click.ClickException("--run-shortcut-tactic-gate required by selected recipe shortcut checks")
        from lemma.lean.verify_runner import run_lean_verify

        shortcut_tactic_problem = Problem(
            id=active_task_id,
            theorem_name=theorem_name,
            type_expr=type_expr,
            split="ingredient",
            lean_toolchain=DEFAULT_TOOLCHAIN,
            mathlib_rev=manifest.mathlib_commit,
            imports=imports,
            extra={
                "ingredient_gate_kind": "shortcut_tactics",
                "lean_max_heartbeats": settings.procedural_triviality_budget_heartbeats,
            },
        )
        shortcut_tactic_result = run_lean_verify(
            settings,
            verify_timeout_s=settings.procedural_gate_timeout_s,
            problem=shortcut_tactic_problem,
            proof_script=ingredient_shortcut_tactic_probe_script(
                theorem_name=theorem_name,
                theorem_type_expr=type_expr,
                imports=imports,
                tactics=shortcut_tactics,
            ),
            submission_policy="strict_envelope",
        )
        if shortcut_tactic_result.passed:
            raise click.ClickException("ingredient shortcut tactic gate failed: tactic solved theorem type")
        if shortcut_tactic_result.reason != "compile_error":
            raise click.ClickException(
                f"ingredient shortcut tactic gate inconclusive: {shortcut_tactic_result.reason}"
            )
        shortcut_tactic_details = ingredient_shortcut_tactic_gate_details(
            theorem_name=theorem_name,
            theorem_type_expr=type_expr,
            imports=imports,
            tactics=shortcut_tactics,
            verify_reason=shortcut_tactic_result.reason,
            max_heartbeats=settings.procedural_triviality_budget_heartbeats,
        )
    try:
        gate_receipt = ingredient_statement_gate_receipt(
            root_path,
            selection=selection,
            active_task_id=active_task_id,
            active_target_sha256=active_target_sha256,
            theorem_statement_sha256=theorem_statement_sha256,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            selection_receipt_sha256=selection_receipt_sha256,
            theorem_type_expr=type_expr,
            runner=statement_gate_runner,
            checks=statement_gate_checks,
            triviality_details=triviality_details,
            novelty_details=novelty_details,
        )
        shortcut_receipt = ingredient_shortcut_gate_receipt(
            root_path,
            selection=selection,
            active_task_id=active_task_id,
            active_target_sha256=active_target_sha256,
            theorem_statement_sha256=theorem_statement_sha256,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            selection_receipt_sha256=selection_receipt_sha256,
            theorem_type_expr=type_expr,
            mathlib_commit=manifest.mathlib_commit,
            theorem_name=theorem_name,
            imports=imports,
            shortcut_tactic_details=shortcut_tactic_details,
        )
    except (OSError, ValueError, ValidationError) as e:
        raise click.ClickException(str(e)) from e
    actual_gate_receipt_sha256 = canonical_sha256(gate_receipt)
    actual_shortcut_receipt_sha256 = canonical_sha256(shortcut_receipt)
    if gate_receipt_sha256 is not None and gate_receipt_sha256 != actual_gate_receipt_sha256:
        raise click.ClickException("gate receipt sha256 mismatch")
    if (
        shortcut_receipt_sha256 is not None
        and shortcut_receipt_sha256 != actual_shortcut_receipt_sha256
    ):
        raise click.ClickException("shortcut receipt sha256 mismatch")
    try:
        receipt = build_ingredient_generation_receipt(
            tempo=tempo,
            epoch_seed=epoch_seed,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            lemma_corpus_snapshot_sha256=manifest.lemma_corpus_snapshot_sha256,
            ingredient_repo_commit=ingredient_repo_commit,
            mathlib_commit=manifest.mathlib_commit,
            recipe_bundle_sha256=manifest.recipe_bundle_sha256,
            difficulty_state_sha256=difficulty_state_sha256,
            selection=selection,
            active_task_id=active_task_id,
            active_target_sha256=active_target_sha256,
            theorem_statement=statement_text,
            gate_receipt=gate_receipt,
            shortcut_receipt=shortcut_receipt,
            active_K=active_k_value,
        )
        task = build_ingredient_task(
            receipt=receipt,
            theorem_name=theorem_name,
            type_expr=type_expr,
            statement=statement_text,
            title=title,
            imports=imports,
            lean_toolchain=DEFAULT_TOOLCHAIN,
            queue_position=queue_position,
        )
    except (ValidationError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    try:
        verify_ingredient_task_against_root(
            task,
            root_path,
            manifest=manifest,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            challenge_seed_sha256=challenge_seed_sha256,
            difficulty_lane=cast(DifficultyLane, difficulty_lane),
        )
    except (OSError, ValueError, ValidationError) as e:
        raise click.ClickException(str(e)) from e

    if evidence_key_uri:
        from bittensor_wallet import Keypair

        keypair = Keypair.create_from_uri(evidence_key_uri)
        signable_envelope = ingredient_generation_receipt_envelope(
            receipt,
            signer_id=str(keypair.ss58_address),
            signature="pending",
        )
        signed = keypair.sign(ingredient_generation_receipt_envelope_signing_payload(signable_envelope))
        signature_hex = "0x" + signed.hex() if isinstance(signed, bytes) else str(signed)
        envelope = ingredient_generation_receipt_envelope(
            receipt,
            signer_id=str(keypair.ss58_address),
            signature=signature_hex if signature_hex.startswith("0x") else "0x" + signature_hex,
        )
    else:
        envelope = ingredient_generation_receipt_envelope(
            receipt,
            signer_id=evidence_signer_id,
            signature=signature,
        )
    try:
        verify_ingredient_generation_receipt_envelope(
            task,
            envelope,
            root_path,
            manifest=manifest,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            challenge_seed_sha256=challenge_seed_sha256,
            difficulty_lane=cast(DifficultyLane, difficulty_lane),
        )
    except (OSError, ValueError, ValidationError) as e:
        raise click.ClickException(str(e)) from e

    output_dir.mkdir(parents=True, exist_ok=True)
    task_path = output_dir / "task.json"
    selection_path = output_dir / "selection-receipt.json"
    gate_receipt_path = output_dir / "gate-receipt.json"
    shortcut_receipt_path = output_dir / "shortcut-receipt.json"
    receipt_path = output_dir / "generation-receipt.json"
    envelope_path = output_dir / "generation-receipt-envelope.json"
    registry_path = output_dir / "active-registry.json"
    artifact_manifest_path = output_dir / "artifact-manifest.json"
    task_path.write_bytes(canonical_json_bytes(task) + b"\n")
    selection_path.write_bytes(canonical_json_bytes(selection) + b"\n")
    gate_receipt_path.write_bytes(canonical_json_bytes(gate_receipt) + b"\n")
    shortcut_receipt_path.write_bytes(canonical_json_bytes(shortcut_receipt) + b"\n")
    receipt_path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    envelope_path.write_bytes(canonical_json_bytes(envelope) + b"\n")
    write_registry((task,), registry_path)
    artifact_manifest = {
        "schema_version": 1,
        "active_task_id": task.id,
        "active_target_sha256": task.target_sha256,
        "theorem_statement_sha256": receipt.theorem_statement_sha256,
        "selected_selector_id": selection.selected_selector_id,
        "selected_recipe_id": selection.selected_recipe_id,
        "selected_parameters_sha256": canonical_sha256(
            {"selected_parameters": selection.selected_parameters}
        ),
        "theorem_type_expr_sha256": text_sha256(task.type_expr),
        "novelty_family_hash": ingredient_novelty_family_hash(selection),
        "lemma_corpus_snapshot_sha256": receipt.lemma_corpus_snapshot_sha256,
        "ingredient_repo_commit": receipt.ingredient_repo_commit,
        "mathlib_commit": receipt.mathlib_commit,
        "recipe_bundle_sha256": receipt.recipe_bundle_sha256,
        "netuid": netuid,
        "active_K": active_k_value,
        "queue_position": queue_position,
        "tempo": tempo,
        "epoch_seed_sha256": receipt.epoch_seed_sha256,
        "challenge_seed_sha256": challenge_seed_sha256,
        "selection_seed_sha256": selection_seed_sha256,
        "difficulty_state_sha256": difficulty_state_sha256,
        "difficulty_lane": receipt.selection.difficulty_lane,
        "ingredient_manifest_sha256": ingredient_manifest_sha256,
        "selection_receipt_sha256": canonical_sha256(selection),
        "gate_receipt_sha256": canonical_sha256(gate_receipt),
        "shortcut_receipt_sha256": canonical_sha256(shortcut_receipt),
        "generation_receipt_sha256": canonical_sha256(receipt),
        "generation_receipt_envelope_sha256": canonical_sha256(envelope),
        "artifacts": {
            "task": {"path": "task.json", "sha256": hashlib.sha256(task_path.read_bytes()).hexdigest()},
            "selection_receipt": {
                "path": "selection-receipt.json",
                "sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
            },
            "gate_receipt": {
                "path": "gate-receipt.json",
                "sha256": hashlib.sha256(gate_receipt_path.read_bytes()).hexdigest(),
            },
            "shortcut_receipt": {
                "path": "shortcut-receipt.json",
                "sha256": hashlib.sha256(shortcut_receipt_path.read_bytes()).hexdigest(),
            },
            "generation_receipt": {
                "path": "generation-receipt.json",
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            },
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": hashlib.sha256(envelope_path.read_bytes()).hexdigest(),
            },
            "active_registry": {
                "path": "active-registry.json",
                "sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            },
        },
    }
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    if active_registry_output is not None:
        _write_regular_output_from(registry_path, active_registry_output, "active registry")
    click.echo(
        json.dumps(
            {
                "active_registry": str(registry_path),
                "active_registry_output": str(active_registry_output) if active_registry_output is not None else None,
                "active_registry_output_sha256": (
                    hashlib.sha256(active_registry_output.read_bytes()).hexdigest()
                    if active_registry_output is not None
                    else None
                ),
                "active_K": active_k_value,
                "active_registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
                "active_task_id": task.id,
                "artifact_manifest": str(artifact_manifest_path),
                "challenge_seed_sha256": challenge_seed_sha256,
                "difficulty_lane": receipt.selection.difficulty_lane,
                "difficulty_state_sha256": difficulty_state_sha256,
                "epoch_seed_sha256": receipt.epoch_seed_sha256,
                "gate_receipt": str(gate_receipt_path),
                "gate_receipt_sha256": canonical_sha256(gate_receipt),
                "generation_receipt": str(receipt_path),
                "generation_receipt_envelope": str(envelope_path),
                "generation_receipt_envelope_sha256": canonical_sha256(envelope),
                "generation_receipt_sha256": canonical_sha256(receipt),
                "ingredient_repo_commit": receipt.ingredient_repo_commit,
                "lemma_corpus_snapshot_sha256": receipt.lemma_corpus_snapshot_sha256,
                "mathlib_commit": receipt.mathlib_commit,
                "netuid": netuid,
                "novelty_family_hash": ingredient_novelty_family_hash(selection),
                "queue_position": queue_position,
                "recipe_bundle_sha256": receipt.recipe_bundle_sha256,
                "selection_receipt": str(selection_path),
                "selection_receipt_sha256": canonical_sha256(selection),
                "selection_seed_sha256": selection_seed_sha256,
                "selected_parameters_sha256": canonical_sha256(
                    {"selected_parameters": selection.selected_parameters}
                ),
                "selected_recipe_id": selection.selected_recipe_id,
                "selected_selector_id": selection.selected_selector_id,
                "shortcut_receipt": str(shortcut_receipt_path),
                "shortcut_receipt_sha256": canonical_sha256(shortcut_receipt),
                "tempo": tempo,
                "task": str(task_path),
                "theorem_type_expr_sha256": text_sha256(task.type_expr),
            },
            indent=2,
            sort_keys=True,
        )
    )


@ingredients_cmd.command("verify-bundle")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Ingredient manifest JSON.",
)
@click.option(
    "--root",
    "root_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient repository root.",
)
@click.option(
    "--bundle",
    "bundle_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Ingredient task artifact bundle directory.",
)
@click.option(
    "--generation-receipt-envelope-quorum",
    type=click.IntRange(min=1),
    default=None,
    help="Required verified receipt envelopes. Defaults to one.",
)
@click.option(
    "--verify-envelope-signatures",
    is_flag=True,
    default=False,
    help="Verify receipt envelope signatures using the envelope signer as an SS58 address.",
)
@click.option(
    "--generation-receipt-envelope",
    "extra_generation_receipt_envelope_paths",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    help="Additional public IngredientGenerationReceiptEnvelope JSON for quorum verification.",
)
@click.option(
    "--novelty-cache-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public novelty cache JSONL required when the bundle gate receipt claims a novelty check.",
)
@click.option(
    "--difficulty-state-jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Public difficulty-state JSONL used to verify the bundle seed context.",
)
@click.option(
    "--epoch-seed",
    default=None,
    help="Optional public epoch seed used to recompute the bundle challenge seed.",
)
def ingredients_verify_bundle_cmd(
    manifest_path: Path,
    root_path: Path,
    bundle_path: Path,
    generation_receipt_envelope_quorum: int | None,
    verify_envelope_signatures: bool,
    extra_generation_receipt_envelope_paths: tuple[Path, ...],
    novelty_cache_jsonl: Path | None,
    difficulty_state_jsonl: Path | None,
    epoch_seed: str | None,
) -> None:
    """Verify an ingredient task artifact bundle."""
    from pydantic import ValidationError

    from lemma.supply.ingredients import (
        IngredientGateReceipt,
        IngredientGenerationReceipt,
        IngredientGenerationReceiptEnvelope,
        IngredientManifest,
        IngredientSelectionReceipt,
        IngredientTaskArtifactManifest,
        IngredientTaskArtifactRef,
        Ss58IngredientEnvelopeSignatureVerifier,
        canonical_json_bytes,
        canonical_sha256,
        ingredient_challenge_seed_sha256,
        ingredient_challenge_slot_seed_sha256,
        ingredient_manifest_component_hashes,
        ingredient_novelty_family_hash,
        text_sha256,
        verify_ingredient_generation_receipt_envelope_quorum,
        verify_ingredient_task_against_root,
    )
    from lemma.tasks import LemmaTask, TaskError, load_task_registry

    settings = LemmaSettings()
    if settings.protocol_mode == "production":
        missing = []
        if difficulty_state_jsonl is None:
            missing.append("--difficulty-state-jsonl")
        if epoch_seed is None:
            missing.append("--epoch-seed")
        if novelty_cache_jsonl is None:
            missing.append("--novelty-cache-jsonl")
        if missing:
            raise click.ClickException(
                f"production ingredient bundle verification requires {', '.join(missing)}"
            )

    artifact_paths = {
        "active_registry": "active-registry.json",
        "gate_receipt": "gate-receipt.json",
        "generation_receipt": "generation-receipt.json",
        "generation_receipt_envelope": "generation-receipt-envelope.json",
        "selection_receipt": "selection-receipt.json",
        "shortcut_receipt": "shortcut-receipt.json",
        "task": "task.json",
    }
    if bundle_path.is_symlink() or not bundle_path.is_dir():
        raise click.ClickException("ingredient task bundle path invalid")

    def artifact_path(ref: IngredientTaskArtifactRef, name: str) -> Path:
        relative = Path(ref.path)
        if ref.path != artifact_paths[name] or relative.is_absolute() or ".." in relative.parts:
            raise click.ClickException(f"ingredient task artifact path invalid: {name}")
        path = bundle_path / relative
        if path.is_symlink() or not path.is_file():
            raise click.ClickException(f"ingredient task artifact path invalid: {name}")
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as e:
            raise click.ClickException(f"ingredient task artifact unreadable: {name}") from e
        if actual != ref.sha256:
            raise click.ClickException(f"ingredient task artifact hash mismatch: {name}")
        return path

    def require_canonical_artifact(path: Path, name: str, model: BaseModel) -> None:
        if path.read_bytes() != canonical_json_bytes(model) + b"\n":
            raise click.ClickException(f"ingredient task artifact noncanonical: {name}")

    def require_canonical_registry(path: Path, task: LemmaTask) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "tasks": [task.model_dump(mode="json", exclude_none=True)],
        }
        expected = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if path.read_bytes() != expected:
            raise click.ClickException("ingredient task artifact noncanonical: active_registry")

    def read_external_evidence(path: Path, name: str) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise click.ClickException(f"ingredient {name} path invalid")
        try:
            return path.read_bytes()
        except OSError as e:
            raise click.ClickException(f"ingredient {name} unreadable: {path}") from e

    def require_exact_bundle_contents() -> None:
        expected = {"artifact-manifest.json", *artifact_paths.values()}
        try:
            entries = tuple(bundle_path.iterdir())
        except OSError as e:
            raise click.ClickException(f"ingredient task bundle unreadable: {bundle_path}") from e
        for entry in entries:
            if entry.name not in expected or entry.is_symlink() or not entry.is_file():
                raise click.ClickException(f"ingredient task artifact unexpected path: {entry.name}")
        present = {entry.name for entry in entries}
        missing = sorted(expected - present)
        if missing:
            raise click.ClickException(f"ingredient task artifact missing: {missing[0]}")

    raw_manifest = _read_ingredient_manifest_bytes(manifest_path)
    try:
        manifest = IngredientManifest.model_validate_json(raw_manifest)
    except ValidationError as e:
        raise click.ClickException("ingredient manifest schema invalid") from e
    _require_canonical_ingredient_manifest(raw_manifest, manifest)
    ingredient_manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    try:
        actual_hashes = ingredient_manifest_component_hashes(root_path)
    except OSError as e:
        raise click.ClickException(f"ingredient component unreadable: {e.filename}") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    mismatches = [
        field for field, actual in actual_hashes.items() if getattr(manifest, field) != actual
    ]
    if mismatches:
        raise click.ClickException(f"ingredient component hash mismatch: {', '.join(mismatches)}")

    artifact_manifest_path = bundle_path / "artifact-manifest.json"
    if artifact_manifest_path.is_symlink() or not artifact_manifest_path.is_file():
        raise click.ClickException("ingredient task artifact manifest path invalid")
    raw_artifact_manifest = artifact_manifest_path.read_bytes()
    try:
        artifact_payload = json.loads(raw_artifact_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise click.ClickException("ingredient task artifact manifest invalid JSON") from e
    if not isinstance(artifact_payload, dict):
        raise click.ClickException("ingredient task artifact manifest invalid JSON")
    if raw_artifact_manifest != canonical_json_bytes(artifact_payload) + b"\n":
        raise click.ClickException("ingredient task artifact manifest noncanonical")
    try:
        artifact_manifest = IngredientTaskArtifactManifest.model_validate(artifact_payload)
    except ValidationError as e:
        raise click.ClickException("ingredient task artifact manifest schema invalid") from e
    if artifact_manifest.ingredient_manifest_sha256 != ingredient_manifest_sha256:
        raise click.ClickException("ingredient task artifact manifest ingredient manifest mismatch")

    task_path = artifact_path(artifact_manifest.artifacts.task, "task")
    selection_path = artifact_path(artifact_manifest.artifacts.selection_receipt, "selection_receipt")
    gate_receipt_path = artifact_path(artifact_manifest.artifacts.gate_receipt, "gate_receipt")
    shortcut_receipt_path = artifact_path(
        artifact_manifest.artifacts.shortcut_receipt,
        "shortcut_receipt",
    )
    receipt_path = artifact_path(artifact_manifest.artifacts.generation_receipt, "generation_receipt")
    envelope_path = artifact_path(
        artifact_manifest.artifacts.generation_receipt_envelope,
        "generation_receipt_envelope",
    )
    registry_path = artifact_path(artifact_manifest.artifacts.active_registry, "active_registry")
    require_exact_bundle_contents()
    try:
        task = LemmaTask.model_validate_json(task_path.read_bytes())
        selection = IngredientSelectionReceipt.model_validate_json(selection_path.read_bytes())
        gate_receipt = IngredientGateReceipt.model_validate_json(gate_receipt_path.read_bytes())
        shortcut_receipt = IngredientGateReceipt.model_validate_json(
            shortcut_receipt_path.read_bytes()
        )
        receipt = IngredientGenerationReceipt.model_validate_json(receipt_path.read_bytes())
        envelope = IngredientGenerationReceiptEnvelope.model_validate_json(envelope_path.read_bytes())
        registry = load_task_registry(registry_path.read_bytes(), strict_top_level=True)
    except ValidationError as e:
        raise click.ClickException("ingredient task artifact schema invalid") from e
    except TaskError as e:
        raise click.ClickException(str(e)) from e
    if task.id != artifact_manifest.active_task_id or receipt.active_task_id != task.id:
        raise click.ClickException("ingredient task artifact active task mismatch")
    if (
        artifact_manifest.active_target_sha256 != task.target_sha256
        or artifact_manifest.theorem_statement_sha256 != receipt.theorem_statement_sha256
    ):
        raise click.ClickException("ingredient task artifact target mismatch")
    if registry.tasks != (task,):
        raise click.ClickException("ingredient task artifact registry mismatch")
    require_canonical_artifact(task_path, "task", task)
    require_canonical_artifact(selection_path, "selection_receipt", selection)
    require_canonical_artifact(gate_receipt_path, "gate_receipt", gate_receipt)
    require_canonical_artifact(shortcut_receipt_path, "shortcut_receipt", shortcut_receipt)
    require_canonical_artifact(receipt_path, "generation_receipt", receipt)
    require_canonical_artifact(envelope_path, "generation_receipt_envelope", envelope)
    require_canonical_registry(registry_path, task)
    if receipt.selection != selection:
        raise click.ClickException("ingredient task artifact selection mismatch")
    if (
        artifact_manifest.selected_selector_id != selection.selected_selector_id
        or artifact_manifest.selected_recipe_id != selection.selected_recipe_id
    ):
        raise click.ClickException("ingredient task artifact selection metadata mismatch")
    if (
        artifact_manifest.selected_parameters_sha256
        != canonical_sha256({"selected_parameters": selection.selected_parameters})
        or artifact_manifest.theorem_type_expr_sha256 != text_sha256(task.type_expr)
        or artifact_manifest.novelty_family_hash != ingredient_novelty_family_hash(selection)
        or task.metadata.get("novelty_family_hash") != artifact_manifest.novelty_family_hash
    ):
        raise click.ClickException("ingredient task artifact realized context mismatch")
    if (
        artifact_manifest.lemma_corpus_snapshot_sha256 != receipt.lemma_corpus_snapshot_sha256
        or manifest.lemma_corpus_snapshot_sha256 != receipt.lemma_corpus_snapshot_sha256
        or artifact_manifest.ingredient_repo_commit != receipt.ingredient_repo_commit
        or artifact_manifest.mathlib_commit != receipt.mathlib_commit
        or artifact_manifest.recipe_bundle_sha256 != receipt.recipe_bundle_sha256
        or artifact_manifest.mathlib_commit != manifest.mathlib_commit
        or artifact_manifest.recipe_bundle_sha256 != manifest.recipe_bundle_sha256
    ):
        raise click.ClickException("ingredient task artifact provenance mismatch")
    if envelope.generation_receipt != receipt:
        raise click.ClickException("ingredient task artifact envelope mismatch")
    selection_seed_sha256 = artifact_manifest.selection_seed_sha256 or artifact_manifest.challenge_seed_sha256
    if (
        artifact_manifest.tempo != receipt.tempo
        or artifact_manifest.active_K != receipt.active_K
        or artifact_manifest.queue_position != task.queue_position
        or artifact_manifest.epoch_seed_sha256 != receipt.epoch_seed_sha256
        or artifact_manifest.difficulty_state_sha256 != receipt.difficulty_state_sha256
        or artifact_manifest.difficulty_lane != receipt.selection.difficulty_lane
        or selection_seed_sha256 != receipt.selection.selection_seed_sha256
    ):
        raise click.ClickException("ingredient task artifact seed context mismatch")
    _ingredient_difficulty_state_context_from_cli(
        difficulty_state_jsonl=difficulty_state_jsonl,
        difficulty_state_sha256=artifact_manifest.difficulty_state_sha256,
        difficulty_lane=artifact_manifest.difficulty_lane,
        tempo=artifact_manifest.tempo,
    )
    if epoch_seed is not None:
        if hashlib.sha256(epoch_seed.encode("utf-8")).hexdigest() != artifact_manifest.epoch_seed_sha256:
            raise click.ClickException("ingredient task artifact epoch seed mismatch")
        expected_challenge_seed = ingredient_challenge_seed_sha256(
            netuid=artifact_manifest.netuid,
            tempo=artifact_manifest.tempo,
            epoch_seed=epoch_seed,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            recipe_bundle_sha256=manifest.recipe_bundle_sha256,
            difficulty_state_sha256=artifact_manifest.difficulty_state_sha256,
        )
        if expected_challenge_seed != artifact_manifest.challenge_seed_sha256:
            raise click.ClickException("ingredient task artifact challenge seed mismatch")
        try:
            expected_selection_seed = ingredient_challenge_slot_seed_sha256(
                challenge_seed_sha256=expected_challenge_seed,
                queue_position=artifact_manifest.queue_position,
                active_K=artifact_manifest.active_K,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        if expected_selection_seed != receipt.selection.selection_seed_sha256:
            raise click.ClickException("ingredient task artifact selection seed mismatch")
    if canonical_sha256(selection) != artifact_manifest.selection_receipt_sha256:
        raise click.ClickException("ingredient task artifact selection receipt mismatch")
    if canonical_sha256(gate_receipt) != artifact_manifest.gate_receipt_sha256:
        raise click.ClickException("ingredient task artifact gate receipt mismatch")
    if canonical_sha256(shortcut_receipt) != artifact_manifest.shortcut_receipt_sha256:
        raise click.ClickException("ingredient task artifact shortcut receipt mismatch")
    if canonical_sha256(receipt) != artifact_manifest.generation_receipt_sha256:
        raise click.ClickException("ingredient task artifact generation receipt mismatch")
    if canonical_sha256(envelope) != artifact_manifest.generation_receipt_envelope_sha256:
        raise click.ClickException("ingredient task artifact generation receipt envelope mismatch")
    expected_receipt_bindings = {
        "active_task_id": task.id,
        "active_target_sha256": task.target_sha256,
        "theorem_statement_sha256": receipt.theorem_statement_sha256,
        "ingredient_manifest_sha256": ingredient_manifest_sha256,
        "selection_receipt_sha256": canonical_sha256(selection),
    }
    for gate_name, gate, expected_kind in (
        ("gate", gate_receipt, "statement_gate"),
        ("shortcut", shortcut_receipt, "shortcut_gate"),
    ):
        if gate.receipt_kind != expected_kind or gate.status != "passed":
            raise click.ClickException(f"ingredient task artifact {gate_name} receipt mismatch")
        if (
            gate.active_task_id != expected_receipt_bindings["active_task_id"]
            or gate.active_target_sha256 != expected_receipt_bindings["active_target_sha256"]
            or gate.theorem_statement_sha256 != expected_receipt_bindings["theorem_statement_sha256"]
            or gate.ingredient_manifest_sha256 != expected_receipt_bindings["ingredient_manifest_sha256"]
            or gate.selection_receipt_sha256 != expected_receipt_bindings["selection_receipt_sha256"]
        ):
            raise click.ClickException(f"ingredient task artifact {gate_name} receipt mismatch")
        if not gate.runner.strip() or not gate.checks or any(not check.strip() for check in gate.checks):
            raise click.ClickException(f"ingredient task artifact {gate_name} receipt mismatch")
    if "shortcut_tactics_checked" in shortcut_receipt.checks and (
        gate_receipt.runner != "lean-statement-gate"
        or "lean_challenge_typechecked" not in gate_receipt.checks
        or "lean_verify_reason:ok" not in gate_receipt.checks
    ):
        raise click.ClickException("ingredient task artifact shortcut tactic requires statement gate")
    expected_gate_receipt, expected_shortcut_receipt = _expected_ingredient_gate_receipts(
        root_path=root_path,
        manifest_mathlib_commit=manifest.mathlib_commit,
        selection=selection,
        task=task,
        gate_receipt=gate_receipt,
        shortcut_receipt=shortcut_receipt,
        generation_receipt=receipt,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        novelty_cache_jsonl=novelty_cache_jsonl,
        production=settings.protocol_mode == "production",
        context="bundle",
    )
    if gate_receipt != expected_gate_receipt:
        raise click.ClickException("ingredient task artifact gate receipt mismatch")
    if shortcut_receipt != expected_shortcut_receipt:
        raise click.ClickException("ingredient task artifact shortcut receipt mismatch")
    if receipt.gate_receipt_sha256 != canonical_sha256(gate_receipt):
        raise click.ClickException("ingredient task artifact gate receipt mismatch")
    if receipt.shortcut_receipt_sha256 != canonical_sha256(shortcut_receipt):
        raise click.ClickException("ingredient task artifact shortcut receipt mismatch")

    try:
        verified_receipt = verify_ingredient_task_against_root(
            task,
            root_path,
            manifest=manifest,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            challenge_seed_sha256=artifact_manifest.challenge_seed_sha256,
            difficulty_lane=receipt.selection.difficulty_lane,
        )
        if verified_receipt != receipt:
            raise click.ClickException("ingredient task artifact receipt mismatch")
        envelopes = [envelope]
        for path in extra_generation_receipt_envelope_paths:
            envelope_raw = read_external_evidence(path, "generation receipt envelope")
            extra_envelope = IngredientGenerationReceiptEnvelope.model_validate_json(envelope_raw)
            _require_canonical_json_artifact(
                envelope_raw,
                extra_envelope,
                "ingredient generation receipt envelope noncanonical",
            )
            envelopes.append(extra_envelope)
        verified_envelopes = verify_ingredient_generation_receipt_envelope_quorum(
            task,
            tuple(envelopes),
            root_path,
            manifest=manifest,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            challenge_seed_sha256=artifact_manifest.challenge_seed_sha256,
            difficulty_lane=receipt.selection.difficulty_lane,
            quorum=generation_receipt_envelope_quorum or 1,
            signature_verifier=Ss58IngredientEnvelopeSignatureVerifier()
            if verify_envelope_signatures
            else None,
        )
    except (OSError, ValueError, ValidationError) as e:
        raise click.ClickException(str(e)) from e

    click.echo(
        json.dumps(
            {
                "active_registry": str(registry_path),
                "active_registry_sha256": artifact_manifest.artifacts.active_registry.sha256,
                "active_task_id": task.id,
                "artifact_manifest": str(artifact_manifest_path),
                "artifact_manifest_sha256": hashlib.sha256(raw_artifact_manifest).hexdigest(),
                "bundle": str(bundle_path),
                "bundle_status": "verified",
                "generation_receipt_envelope_quorum": generation_receipt_envelope_quorum or 1,
                "envelope_signature_status": "verified"
                if verify_envelope_signatures
                else "metadata_only",
                "challenge_seed_sha256": artifact_manifest.challenge_seed_sha256,
                "difficulty_lane": artifact_manifest.difficulty_lane,
                "difficulty_state_sha256": artifact_manifest.difficulty_state_sha256,
                "epoch_seed_sha256": artifact_manifest.epoch_seed_sha256,
                "gate_receipt_sha256": canonical_sha256(gate_receipt),
                "generation_receipt_envelope_sha256s": [
                    canonical_sha256(envelope) for envelope in verified_envelopes
                ],
                "generation_receipt_sha256": canonical_sha256(receipt),
                "ingredient_repo_commit": artifact_manifest.ingredient_repo_commit,
                "ingredient_manifest_sha256": ingredient_manifest_sha256,
                "lemma_corpus_snapshot_sha256": artifact_manifest.lemma_corpus_snapshot_sha256,
                "mathlib_commit": artifact_manifest.mathlib_commit,
                "netuid": artifact_manifest.netuid,
                "novelty_family_hash": artifact_manifest.novelty_family_hash,
                "recipe_bundle_sha256": artifact_manifest.recipe_bundle_sha256,
                "selection_receipt_sha256": canonical_sha256(selection),
                "selected_parameters_sha256": artifact_manifest.selected_parameters_sha256,
                "selected_recipe_id": selection.selected_recipe_id,
                "selected_selector_id": selection.selected_selector_id,
                "shortcut_receipt_sha256": canonical_sha256(shortcut_receipt),
                "tempo": artifact_manifest.tempo,
                "task": str(task_path),
                "theorem_type_expr_sha256": artifact_manifest.theorem_type_expr_sha256,
            },
            indent=2,
            sort_keys=True,
        )
    )


@click.command("build-fixture-ingredient-registry", hidden=True)
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--netuid", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--tempo", type=click.IntRange(min=0), required=True)
@click.option("--epoch-seed", required=True)
@click.option("--ingredient-manifest-sha256", default="1" * 64, show_default=True)
@click.option("--lemma-corpus-snapshot-sha256", default="f" * 64, show_default=True)
@click.option("--ingredient-repo-commit", default="abc123", show_default=True)
@click.option("--mathlib-commit", default="abc123", show_default=True)
@click.option("--recipe-bundle-sha256", default="2" * 64, show_default=True)
@click.option("--difficulty-state-sha256", default="3" * 64, show_default=True)
@click.option("--difficulty-lane", type=click.Choice(["easy", "medium", "hard", "frontier"]), default="hard")
@click.option("--active-task-id", default="lemma.ingredient.fixture_true", show_default=True)
@click.option("--theorem-name", default="fixture_ingredient_true", show_default=True)
@click.option("--type-expr", default="True", show_default=True)
@click.option("--statement", default="theorem fixture_ingredient_true : True := by\n  sorry", show_default=True)
@click.option("--gate-receipt-sha256", default="6" * 64, show_default=True)
@click.option("--shortcut-receipt-sha256", default="7" * 64, show_default=True)
def tasks_build_fixture_ingredient_registry_cmd(
    output_path: Path,
    netuid: int,
    tempo: int,
    epoch_seed: str,
    ingredient_manifest_sha256: str,
    lemma_corpus_snapshot_sha256: str,
    ingredient_repo_commit: str,
    mathlib_commit: str,
    recipe_bundle_sha256: str,
    difficulty_state_sha256: str,
    difficulty_lane: str,
    active_task_id: str,
    theorem_name: str,
    type_expr: str,
    statement: str,
    gate_receipt_sha256: str,
    shortcut_receipt_sha256: str,
) -> None:
    """Build a hidden one-task ingredient fixture registry."""
    from lemma.supply.ingredients import (
        CompatibilityEdge,
        DefinitionIngredient,
        DifficultyLane,
        FactIngredient,
        RecipeRule,
        RecipeSelector,
        build_fixture_ingredient_registry,
    )
    from lemma.task_supply import write_registry

    try:
        registry = build_fixture_ingredient_registry(
            netuid=netuid,
            tempo=tempo,
            epoch_seed=epoch_seed,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            lemma_corpus_snapshot_sha256=lemma_corpus_snapshot_sha256,
            ingredient_repo_commit=ingredient_repo_commit,
            mathlib_commit=mathlib_commit,
            recipe_bundle_sha256=recipe_bundle_sha256,
            difficulty_state_sha256=difficulty_state_sha256,
            difficulty_lane=cast(DifficultyLane, difficulty_lane),
            selectors=(
                RecipeSelector(
                    selector_id="fixture_true_selector_v1",
                    difficulty_lane=cast(DifficultyLane, difficulty_lane),
                    recipe_ids=("fixture_true_recipe_v1",),
                    ingredient_filters={"domains": ["Logic"]},
                ),
            ),
            recipes=(
                RecipeRule(
                    recipe_id="fixture_true_recipe_v1",
                    version=1,
                    domains=("Logic",),
                    required_ingredient_classes=("logic_definition", "logic_fact"),
                    required_definitions=("True",),
                    required_fact_kinds=("lemma",),
                    parameter_rule="none",
                    soundness_template="fixture/true.lean",
                ),
            ),
            definitions=(
                DefinitionIngredient(
                    definition_id="True",
                    lean_name="True",
                    domain="Logic",
                    type_signature="Prop",
                    imports=("Mathlib",),
                    source_path="Mathlib/Init/Logic.lean",
                    mathlib_commit=mathlib_commit,
                ),
            ),
            facts=(
                FactIngredient(
                    fact_id="True.intro",
                    lean_name="True.intro",
                    kind="lemma",
                    domain="Logic",
                    type_expr="True",
                    imports=("Mathlib",),
                    source_path="Mathlib/Init/Logic.lean",
                    mathlib_commit=mathlib_commit,
                    difficulty_hint=1,
                ),
            ),
            compatibility_edges=(
                CompatibilityEdge(
                    edge_id="fixture_true_recipe_v1.edge.logic",
                    recipe_id="fixture_true_recipe_v1",
                    ingredient_class="logic_fact",
                    allowed_domains=("Logic",),
                    allowed_definition_ids=("True",),
                    allowed_fact_patterns=("True",),
                    difficulty_lanes=(cast(DifficultyLane, difficulty_lane),),
                    certification_receipt_sha256="9" * 64,
                ),
            ),
            theorem_name=theorem_name,
            type_expr=type_expr,
            statement=statement,
            active_task_id=active_task_id,
            gate_receipt_sha256=gate_receipt_sha256,
            shortcut_receipt_sha256=shortcut_receipt_sha256,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    write_registry(registry.tasks, output_path)
    click.echo(
        json.dumps(
            {
                "output": str(output_path),
                "registry_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "tasks": len(registry.tasks),
            },
            indent=2,
            sort_keys=True,
        )
    )


@click.command("replay-ingredient-generation", hidden=True)
@click.option("--tempo", type=click.IntRange(min=0), default=None)
def tasks_replay_ingredient_generation_cmd(tempo: int | None) -> None:
    """Replay the hidden ingredient active-cache receipt checks."""
    from lemma.protocol_invariants import enforce_production_invariants
    from lemma.supply.ingredients import expected_ingredient_generation_receipt_sha256
    from lemma.validator import active_tasks_for_validation, current_active_tempo, task_registry_for_validation

    settings = LemmaSettings()
    if settings.task_supply_mode != "ingredient":
        raise click.ClickException("ingredient replay requires LEMMA_TASK_SUPPLY_MODE=ingredient")
    active_tempo = current_active_tempo(settings) if tempo is None else tempo
    registry = task_registry_for_validation(settings, tempo=active_tempo)
    if settings.protocol_mode == "production":
        try:
            enforce_production_invariants(settings, registry)
        except RuntimeError as e:
            raise click.ClickException(str(e)) from e
    active_tasks = active_tasks_for_validation(registry, settings, tempo=active_tempo)
    if not active_tasks:
        raise click.ClickException("ingredient replay requires active tasks")
    receipts: list[tuple[str, str]] = []
    for task in active_tasks:
        receipt_sha256 = expected_ingredient_generation_receipt_sha256(task)
        if task.metadata.get("generation_receipt_sha256") != receipt_sha256:
            raise click.ClickException("ingredient generation receipt mismatch")
        receipts.append((task.id, receipt_sha256))
    first_task_id, first_receipt_sha256 = receipts[0]
    click.echo(
        json.dumps(
            {
                "tempo": active_tempo,
                "active_K": len(active_tasks),
                "active_task_id": first_task_id,
                "active_task_ids": [task_id for task_id, _receipt in receipts],
                "generation_receipt_sha256": first_receipt_sha256,
                "generation_receipt_sha256s": [receipt for _task_id, receipt in receipts],
            },
            indent=2,
            sort_keys=True,
        )
    )


def register_ingredient_commands(main_group: click.Group, tasks_group: click.Group) -> None:
    main_group.add_command(ingredients_cmd)
    tasks_group.add_command(tasks_assemble_ingredient_active_registry_cmd)
    tasks_group.add_command(tasks_build_ingredient_task_cmd)
    tasks_group.add_command(tasks_build_fixture_ingredient_registry_cmd)
    tasks_group.add_command(tasks_replay_ingredient_generation_cmd)

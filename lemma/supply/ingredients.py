"""Ingredient-mode data contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Literal, NamedTuple, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator

from lemma.problems.base import Problem
from lemma.supply.mathlib_snapshot import MathlibSnapshotRow
from lemma.task_supply import DEFAULT_TOOLCHAIN
from lemma.tasks import (
    LEAN_VERIFIER_ID,
    LEAN_VERIFIER_VERSION,
    LemmaTask,
    SourceRef,
    TaskRegistry,
    problem_target_sha256,
    task_registry_from_tasks,
)

SHA256_PATTERN = r"^[a-f0-9]{64}$"
GIT_COMMIT_PATTERN = r"^[a-f0-9]{6,40}$"
GIT_COMMIT_RE = re.compile(r"[a-f0-9]{6,40}")
INGREDIENT_LABEL_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*$"
LEAN_MODULE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")
LEAN_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
INGREDIENT_LABEL_RE = re.compile(INGREDIENT_LABEL_PATTERN)
PUBLIC_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
PUBLIC_ARTIFACT_PATH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
INGREDIENT_ACTIVE_TASK_ID_PREFIX = "lemma.ingredient."
INGREDIENT_TASK_ARTIFACT_PATHS = {
    "active_registry": "active-registry.json",
    "gate_receipt": "gate-receipt.json",
    "generation_receipt": "generation-receipt.json",
    "generation_receipt_envelope": "generation-receipt-envelope.json",
    "selection_receipt": "selection-receipt.json",
    "shortcut_receipt": "shortcut-receipt.json",
    "task": "task.json",
}
NAT_PARAMETER_RE = re.compile(r"0|[1-9][0-9]*")
INT_PARAMETER_RE = re.compile(r"0|-?[1-9][0-9]*")
BOOL_PARAMETER_ORDER = {"false": 0, "true": 1}
INGREDIENT_SELECTED_PARAMETER_KEYS = frozenset({"Bool", "Int", "Nat"})
INGREDIENT_TASK_SOURCE_LICENSE = "Apache-2.0"
INGREDIENT_TASK_SUBMISSION_POLICY = "restricted_helpers"
DifficultyLane = Literal["easy", "medium", "hard", "frontier"]
DIFFICULTY_LANES: tuple[DifficultyLane, ...] = ("easy", "medium", "hard", "frontier")
INGREDIENT_DIFFICULTY_STATE_KEYS = frozenset({"tempo", "difficulty_lane"})
HashOrderValue = TypeVar("HashOrderValue")


class MathlibDefinitionLike(Protocol):
    definition_name: str
    type_signature: str
    imports: tuple[str, ...]
    mathlib_rev: str
    source_path: str
    source_license: str
    queue_depth: int
    topic: str | None
    subtopic: str | None


def _exact_public_int(value: Any) -> Any:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected exact integer")
    return value


def _reject_placeholder_sha256(value: str, message: str) -> str:
    if value == "0" * 64:
        raise ValueError(message)
    return value


def _reject_placeholder_git_commit(value: str, message: str) -> str:
    if set(value) == {"0"}:
        raise ValueError(message)
    return value


def _require_schema_version_1(payload: dict[str, object], error: str) -> None:
    try:
        schema_version = _exact_public_int(payload.get("schema_version"))
    except ValueError as e:
        raise ValueError(error) from e
    if schema_version != 1:
        raise ValueError(error)


def validate_ingredient_selected_parameters(
    value: object,
    *,
    message: str = "ingredient selection receipt selected parameters invalid",
) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > 1:
        raise ValueError(message)
    for name, selected in value.items():
        if not isinstance(name, str) or name not in INGREDIENT_SELECTED_PARAMETER_KEYS:
            raise ValueError(f"{message}:{name}")
        if not isinstance(selected, str):
            raise ValueError(f"{message}:{name}")
        if name == "Nat" and not NAT_PARAMETER_RE.fullmatch(selected):
            raise ValueError(f"{message}:{name}")
        if name == "Int" and not INT_PARAMETER_RE.fullmatch(selected):
            raise ValueError(f"{message}:{name}")
        if name == "Bool" and selected not in {"false", "true"}:
            raise ValueError(f"{message}:{name}")
    return cast(dict[str, Any], value)


INGREDIENT_MANIFEST_COMPONENT_PATHS = {
    "definitions_sha256": "ingredients/definitions.jsonl",
    "facts_sha256": "ingredients/facts.jsonl",
    "source_theorems_sha256": "ingredients/source_theorems.jsonl",
    "source_lemmas_sha256": "ingredients/source_lemmas.jsonl",
    "compatibility_graph_sha256": "compatibility/compatibility_graph.jsonl",
    "source_compatibility_sha256": "compatibility/source_compatibility.jsonl",
    "definition_compatibility_sha256": "compatibility/definition_compatibility.jsonl",
    "bridge_catalog_sha256": "compatibility/bridge_catalog.jsonl",
    "recipe_selectors_sha256": "compatibility/recipe_selectors.jsonl",
    "recipe_bundle_sha256": "recipes/operator_bundle.json",
    "difficulty_ladder_sha256": "policy/difficulty_ladder.json",
    "difficulty_retarget_sha256": "policy/difficulty_retarget.json",
    "novelty_policy_sha256": "policy/novelty_policy.json",
    "shortcut_policy_sha256": "policy/shortcut_policy.json",
    "reserve_selector_policy_sha256": "policy/reserve_selector_policy.json",
}
INGREDIENT_REPOSITORY_REPORT_PATHS = {
    "extraction_report": "reports/extraction_report.json",
    "ingredient_quality_report": "reports/ingredient_quality_report.json",
}
INGREDIENT_RECIPE_ARTIFACT_PATHS = {
    "recipe_rules": "recipes/recipe_rules.json",
    "parameter_sets": "recipes/parameter_sets.json",
}
INGREDIENT_QUALITY_REPORT_KEYS = frozenset(
    {
        "definition_count",
        "fact_count",
        "compatibility_edge_count",
        "recipe_count",
        "difficulty_lane_coverage",
        "bridge_coverage",
        "estimated_theorem_space_size",
        "shortcut_risk_distribution",
        "reserve_selector_health",
    }
)
INGREDIENT_EXTRACTION_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "mathlib_commit",
        "source_row_count",
        "fact_count",
        "definition_count",
        "source_license_counts",
    }
)
INGREDIENT_SHORTCUT_TACTIC_ORDER = ("simp", "aesop", "omega", "grind")
INGREDIENT_SHORTCUT_TACTIC_CHECKS = frozenset(INGREDIENT_SHORTCUT_TACTIC_ORDER)
INGREDIENT_SHORTCUT_CHECK_ORDER = (
    "source_oracle",
    "source_subterm_oracle",
    "source_numeric_skeleton_oracle",
    "source_shape_skeleton_oracle",
    "source_token_multiset_oracle",
    *INGREDIENT_SHORTCUT_TACTIC_ORDER,
)
SUPPORTED_INGREDIENT_SHORTCUT_CHECKS = frozenset(INGREDIENT_SHORTCUT_CHECK_ORDER)
SUPPORTED_INGREDIENT_STATEMENT_GATE_CHECKS = frozenset(
    {
        "baseline_triviality_not_solved",
        "bounded_triviality_checked",
        "lean_challenge_typechecked",
        "novelty_cache_bound",
        "selection_family_not_in_novelty_cache",
        "soundness_template_bound",
        "soundness_template_no_holes",
        "soundness_template_typechecked",
        "soundness_template_witness_checked",
        "statement_hash_bound",
        "target_hash_bound",
        "theorem_type_not_in_novelty_cache",
    }
)
SUPPORTED_INGREDIENT_STATEMENT_GATE_CHECK_PREFIXES = (
    "bounded_triviality_reason:",
    "lean_verify_reason:",
    "soundness_template_verify_reason:",
)
SUPPORTED_INGREDIENT_SHORTCUT_RISK_LABELS = frozenset(
    {"bootstrap_only", "calibration_only", "paid_eligible", "reject"}
)
INGREDIENT_NOVELTY_CHECK_ORDER = ("theorem_type_cache", "selection_family_cache")
SUPPORTED_INGREDIENT_NOVELTY_CHECKS = frozenset(INGREDIENT_NOVELTY_CHECK_ORDER)
SUPPORTED_INGREDIENT_PARAMETER_RULES = frozenset({"finite_bool", "finite_int", "finite_nat", "none"})
SUPPORTED_INGREDIENT_SELECTOR_FILTERS = frozenset({"domains", "max_simp_risk", "min_dependency_depth"})
SUPPORTED_INGREDIENT_SIMP_RISKS = frozenset({"low", "medium", "high"})
INGREDIENT_SIMP_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
INGREDIENT_RECIPE_BUNDLE_KEYS = frozenset({"schema_version", "recipes"})
INGREDIENT_DIFFICULTY_LADDER_KEYS = frozenset({"schema_version", "difficulty_lanes"})
INGREDIENT_DIFFICULTY_RETARGET_POLICY_KEYS = frozenset(
    {"schema_version", "retarget_mode", "state_schema"}
)
INGREDIENT_SHORTCUT_POLICY_KEYS = frozenset({"schema_version", "supported_checks"})
INGREDIENT_NOVELTY_POLICY_KEYS = frozenset(
    {"schema_version", "supported_checks", "novelty_cache_version"}
)
INGREDIENT_RESERVE_SELECTOR_POLICY_KEYS = frozenset(
    {"schema_version", "reserve_enabled", "selection_method"}
)
INGREDIENT_DEFINITION_METADATA_KEYS = frozenset({"allowed_recipes", "simp_risk"})
INGREDIENT_FACT_METADATA_KEYS = frozenset(
    {
        "difficulty_score",
        "direct_dependency_count",
        "dependency_depth",
        "proof_sha256",
        "queue_depth",
        "source_line",
        "statement_family",
        "subtopic",
        "topic",
        "usable_as_source_fact",
    }
)


class BootstrapRecipeSpec(NamedTuple):
    recipe_id: str
    required_definitions: tuple[str, ...]
    fact_pattern: str
    soundness_template: str
    soundness_theorem: str
    theorem_type_template: str
    soundness_type_expr: str
    domains: tuple[str, ...] = ("List", "Nat")
    required_ingredient_classes: tuple[str, ...] = ("list_definition", "list_fact")
    fact_domain: str = "List"


BOOTSTRAP_RECIPE_SPECS = (
    BootstrapRecipeSpec(
        recipe_id="nat_add_zero_v1",
        required_definitions=("Nat.add",),
        fact_pattern="add",
        soundness_template="soundness_templates/nat_add_zero.lean",
        soundness_theorem="nat_add_zero_soundness",
        theorem_type_template="Nat.add {n} 0 = {n}",
        soundness_type_expr="Nat.add n 0 = n",
        domains=("Nat",),
        required_ingredient_classes=("nat_definition", "nat_fact"),
        fact_domain="Nat",
    ),
    BootstrapRecipeSpec(
        recipe_id="nat_mul_one_v1",
        required_definitions=("Nat.mul",),
        fact_pattern="mul",
        soundness_template="soundness_templates/nat_mul_one.lean",
        soundness_theorem="nat_mul_one_soundness",
        theorem_type_template="Nat.mul {n} 1 = {n}",
        soundness_type_expr="Nat.mul n 1 = n",
        domains=("Nat",),
        required_ingredient_classes=("nat_definition", "nat_fact"),
        fact_domain="Nat",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_length_v1",
        required_definitions=("List.length",),
        fact_pattern="length",
        soundness_template="soundness_templates/list_length.lean",
        soundness_theorem="list_length_soundness",
        theorem_type_template="List.length (List.replicate {n} 0) = {n}",
        soundness_type_expr="List.length (List.replicate n 0) = n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_append_length_v1",
        required_definitions=("List.append", "List.length"),
        fact_pattern="append",
        soundness_template="soundness_templates/list_append_length.lean",
        soundness_theorem="list_append_length_soundness",
        theorem_type_template=(
            "List.length ((List.replicate {n} 0) ++ (List.replicate {n} 1)) = {n} + {n}"
        ),
        soundness_type_expr="List.length ((List.replicate n 0) ++ (List.replicate n 1)) = n + n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_dedup_pair_length_v1",
        required_definitions=("List.dedup",),
        fact_pattern="dedup",
        soundness_template="soundness_templates/list_dedup_pair_length.lean",
        soundness_theorem="list_dedup_pair_length_soundness",
        theorem_type_template="List.length (List.dedup [{n}, {n}]) = 1",
        soundness_type_expr="List.length (List.dedup [n, n]) = 1",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_drop_length_v1",
        required_definitions=("List.drop", "List.length"),
        fact_pattern="drop",
        soundness_template="soundness_templates/list_drop_length.lean",
        soundness_theorem="list_drop_length_soundness",
        theorem_type_template="List.length (List.drop {n} (List.replicate ({n} + {n}) 0)) = {n}",
        soundness_type_expr="List.length (List.drop n (List.replicate (n + n) 0)) = n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_filter_true_length_v1",
        required_definitions=("List.filter", "List.length"),
        fact_pattern="filter",
        soundness_template="soundness_templates/list_filter_true_length.lean",
        soundness_theorem="list_filter_true_length_soundness",
        theorem_type_template=(
            "List.length (List.filter (fun _ : Nat => true) (List.replicate {n} 0)) = {n}"
        ),
        soundness_type_expr="List.length (List.filter (fun _ : Nat => true) (List.replicate n 0)) = n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_map_length_v1",
        required_definitions=("List.length", "List.map"),
        fact_pattern="map",
        soundness_template="soundness_templates/list_map_length.lean",
        soundness_theorem="list_map_length_soundness",
        theorem_type_template=(
            "List.length (List.map (fun x : Nat => x) (List.replicate {n} 0)) = {n}"
        ),
        soundness_type_expr="List.length (List.map (fun x : Nat => x) (List.replicate n 0)) = n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_range_length_v1",
        required_definitions=("List.length", "List.range"),
        fact_pattern="range",
        soundness_template="soundness_templates/list_range_length.lean",
        soundness_theorem="list_range_length_soundness",
        theorem_type_template="List.length (List.range {n}) = {n}",
        soundness_type_expr="List.length (List.range n) = n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_reverse_length_v1",
        required_definitions=("List.length", "List.reverse"),
        fact_pattern="reverse",
        soundness_template="soundness_templates/list_reverse_length.lean",
        soundness_theorem="list_reverse_length_soundness",
        theorem_type_template="List.length (List.reverse (List.replicate {n} 0)) = {n}",
        soundness_type_expr="List.length (List.reverse (List.replicate n 0)) = n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_take_length_v1",
        required_definitions=("List.length", "List.take"),
        fact_pattern="take",
        soundness_template="soundness_templates/list_take_length.lean",
        soundness_theorem="list_take_length_soundness",
        theorem_type_template="List.length (List.take {n} (List.replicate {n} 0)) = {n}",
        soundness_type_expr="List.length (List.take n (List.replicate n 0)) = n",
    ),
    BootstrapRecipeSpec(
        recipe_id="list_zip_length_v1",
        required_definitions=("List.length", "List.zip"),
        fact_pattern="zip",
        soundness_template="soundness_templates/list_zip_length.lean",
        soundness_theorem="list_zip_length_soundness",
        theorem_type_template="List.length (List.zip (List.replicate {n} 0) (List.replicate {n} 1)) = {n}",
        soundness_type_expr="List.length (List.zip (List.replicate n 0) (List.replicate n 1)) = n",
    ),
)
BOOTSTRAP_RECIPE_BY_ID = {spec.recipe_id: spec for spec in BOOTSTRAP_RECIPE_SPECS}
INGREDIENT_BRIDGE_METADATA_KEYS = frozenset({"meaning"})
SOUNDNESS_TEMPLATE_FORBIDDEN_TOKEN_RE = re.compile(
    r"\b(admit|axiom|constant|extern|opaque|sorry|unsafe)\b|sorryAx"
)


class IngredientManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    mathlib_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    lemma_corpus_snapshot_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    definitions_sha256: str = Field(pattern=SHA256_PATTERN)
    facts_sha256: str = Field(pattern=SHA256_PATTERN)
    source_theorems_sha256: str = Field(pattern=SHA256_PATTERN)
    source_lemmas_sha256: str = Field(pattern=SHA256_PATTERN)
    compatibility_graph_sha256: str = Field(pattern=SHA256_PATTERN)
    source_compatibility_sha256: str = Field(pattern=SHA256_PATTERN)
    definition_compatibility_sha256: str = Field(pattern=SHA256_PATTERN)
    bridge_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    recipe_selectors_sha256: str = Field(pattern=SHA256_PATTERN)
    recipe_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    difficulty_ladder_sha256: str = Field(pattern=SHA256_PATTERN)
    difficulty_retarget_sha256: str = Field(pattern=SHA256_PATTERN)
    novelty_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    shortcut_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reserve_selector_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _validate_exact_ints(cls, value: Any) -> Any:
        return _exact_public_int(value)

    @field_validator("mathlib_commit")
    @classmethod
    def _validate_mathlib_commit(cls, value: str) -> str:
        return _reject_placeholder_git_commit(value, "ingredient manifest mathlib commit placeholder")

    @field_validator(
        "lemma_corpus_snapshot_sha256",
        "definitions_sha256",
        "facts_sha256",
        "source_theorems_sha256",
        "source_lemmas_sha256",
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
        "recipe_bundle_sha256",
        "difficulty_ladder_sha256",
        "difficulty_retarget_sha256",
        "novelty_policy_sha256",
        "shortcut_policy_sha256",
        "reserve_selector_policy_sha256",
    )
    @classmethod
    def _validate_manifest_hashes(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _reject_placeholder_sha256(value, "ingredient manifest sha256 placeholder")


class DefinitionIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_id: str
    lean_name: str
    domain: str
    type_signature: str
    imports: tuple[str, ...]
    source_path: str
    mathlib_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mathlib_commit")
    @classmethod
    def _validate_mathlib_commit(cls, value: str) -> str:
        return _reject_placeholder_git_commit(value, "ingredient definition mathlib commit placeholder")


class FactIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    lean_name: str
    kind: Literal["lemma", "theorem"]
    domain: str
    type_expr: str
    imports: tuple[str, ...]
    source_path: str
    mathlib_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    difficulty_hint: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("difficulty_hint", mode="before")
    @classmethod
    def _validate_exact_ints(cls, value: Any) -> Any:
        return _exact_public_int(value)

    @field_validator("mathlib_commit")
    @classmethod
    def _validate_mathlib_commit(cls, value: str) -> str:
        return _reject_placeholder_git_commit(value, "ingredient fact mathlib commit placeholder")


class RecipeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    version: int = Field(ge=1)
    domains: tuple[str, ...]
    required_ingredient_classes: tuple[str, ...]
    required_definitions: tuple[str, ...]
    required_fact_kinds: tuple[str, ...]
    preconditions: tuple[str, ...] = ()
    parameter_rule: str
    soundness_template: str
    shortcut_checks: tuple[str, ...] = ()
    difficulty_delta: int = 0

    @field_validator("version", "difficulty_delta", mode="before")
    @classmethod
    def _validate_exact_ints(cls, value: Any) -> Any:
        return _exact_public_int(value)


class RecipeSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector_id: str
    difficulty_lane: DifficultyLane
    recipe_ids: tuple[str, ...]
    ingredient_filters: dict[str, Any] = Field(default_factory=dict)
    selection_method: Literal["hash_order_first_eligible"] = "hash_order_first_eligible"


class BridgeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bridge_id: str
    from_domain: str
    to_domain: str
    safe_recipes: tuple[str, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompatibilityEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str
    recipe_id: str
    ingredient_class: str
    allowed_domains: tuple[str, ...]
    allowed_definition_ids: tuple[str, ...] = ()
    allowed_fact_patterns: tuple[str, ...] = ()
    bridge_ids: tuple[str, ...] = ()
    difficulty_lanes: tuple[DifficultyLane, ...]
    certification_receipt_sha256: str = Field(pattern=SHA256_PATTERN)


class IngredientSelectionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_selector_id: str
    selected_recipe_id: str
    selected_definition_ids: tuple[str, ...] = ()
    selected_fact_ids: tuple[str, ...] = ()
    selected_bridge_ids: tuple[str, ...] = ()
    selected_parameters: dict[str, Any] = Field(default_factory=dict)
    difficulty_lane: DifficultyLane
    selection_seed_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("selected_recipe_id")
    @classmethod
    def _validate_selected_recipe_id(cls, value: str) -> str:
        _validate_public_label(value, "ingredient selection receipt recipe invalid")
        return value

    @field_validator("selected_selector_id")
    @classmethod
    def _validate_selected_selector_id(cls, value: str) -> str:
        _validate_public_label(value, "ingredient selection receipt selector invalid")
        return value

    @field_validator("selected_definition_ids", "selected_fact_ids", "selected_bridge_ids")
    @classmethod
    def _validate_selected_ids(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        for item in value:
            _validate_public_label(
                item,
                f"ingredient selection receipt {info.field_name} invalid",
            )
        duplicate = _first_duplicate(value)
        if duplicate is not None:
            raise ValueError(f"ingredient selection receipt {info.field_name} duplicate: {duplicate}")
        return value

    @field_validator("selected_parameters", mode="before")
    @classmethod
    def _validate_selected_parameters(cls, value: object) -> dict[str, Any]:
        return validate_ingredient_selected_parameters(value)

    @field_validator("selection_seed_sha256")
    @classmethod
    def _validate_selection_seed(cls, value: str) -> str:
        return _reject_placeholder_sha256(value, "ingredient selection receipt seed placeholder")


class IngredientGenerationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    tempo: int = Field(ge=0)
    active_K: int = Field(ge=1)
    epoch_seed_sha256: str = Field(pattern=SHA256_PATTERN)
    ingredient_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    lemma_corpus_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    ingredient_repo_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    mathlib_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    recipe_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    difficulty_state_sha256: str = Field(pattern=SHA256_PATTERN)
    selection: IngredientSelectionReceipt
    active_task_id: str = Field(pattern=INGREDIENT_LABEL_PATTERN)
    active_target_sha256: str = Field(pattern=SHA256_PATTERN)
    theorem_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    gate_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    shortcut_receipt_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("schema_version", "tempo", "active_K", mode="before")
    @classmethod
    def _validate_exact_ints(cls, value: Any) -> Any:
        return _exact_public_int(value)

    @field_validator("epoch_seed_sha256")
    @classmethod
    def _validate_epoch_seed(cls, value: str) -> str:
        return _reject_placeholder_sha256(value, "ingredient generation receipt epoch seed placeholder")

    @field_validator(
        "active_target_sha256",
        "difficulty_state_sha256",
        "gate_receipt_sha256",
        "ingredient_manifest_sha256",
        "lemma_corpus_snapshot_sha256",
        "recipe_bundle_sha256",
        "shortcut_receipt_sha256",
        "theorem_statement_sha256",
    )
    @classmethod
    def _validate_receipt_hashes(cls, value: str) -> str:
        return _reject_placeholder_sha256(value, "ingredient generation receipt sha256 placeholder")

    @field_validator("ingredient_repo_commit", "mathlib_commit")
    @classmethod
    def _validate_commits(cls, value: str, info: ValidationInfo) -> str:
        return _reject_placeholder_git_commit(
            value,
            f"ingredient generation receipt {info.field_name} placeholder",
        )

    @field_validator("active_task_id")
    @classmethod
    def _validate_active_task_id(cls, value: str) -> str:
        validate_ingredient_active_task_id(value)
        return value


class IngredientGateReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    receipt_kind: Literal["statement_gate", "shortcut_gate"]
    active_task_id: str = Field(pattern=INGREDIENT_LABEL_PATTERN)
    active_target_sha256: str = Field(pattern=SHA256_PATTERN)
    theorem_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    ingredient_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    selection_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    status: Literal["passed"]
    runner: str
    checks: tuple[str, ...]
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _validate_exact_ints(cls, value: Any) -> Any:
        return _exact_public_int(value)

    @field_validator("active_task_id")
    @classmethod
    def _validate_active_task_id(cls, value: str) -> str:
        validate_ingredient_active_task_id(value)
        return value

    @field_validator(
        "active_target_sha256",
        "ingredient_manifest_sha256",
        "selection_receipt_sha256",
        "theorem_statement_sha256",
    )
    @classmethod
    def _validate_receipt_hashes(cls, value: str) -> str:
        return _reject_placeholder_sha256(value, "ingredient gate receipt sha256 placeholder")

    @field_validator("runner")
    @classmethod
    def _validate_runner(cls, value: str) -> str:
        _validate_public_token(value, "ingredient gate runner invalid")
        _reject_placeholder_public_token(value, "ingredient gate runner placeholder")
        return value

    @field_validator("checks")
    @classmethod
    def _validate_checks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("ingredient gate checks missing")
        duplicate = _first_duplicate(value)
        if duplicate is not None:
            raise ValueError(f"ingredient gate check duplicate: {duplicate}")
        for check in value:
            _validate_public_token(check, "ingredient gate check invalid")
            _reject_placeholder_public_token(check, "ingredient gate check placeholder")
        return value


class IngredientGenerationReceiptEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    generation_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_receipt: IngredientGenerationReceipt
    signer_id: str | None = None
    signature: str | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _validate_exact_ints(cls, value: Any) -> Any:
        return _exact_public_int(value)

    @field_validator("generation_receipt_sha256")
    @classmethod
    def _validate_generation_receipt_sha256(cls, value: str) -> str:
        return _reject_placeholder_sha256(
            value,
            "ingredient generation receipt envelope sha256 placeholder",
        )

    @field_validator("signer_id", "signature")
    @classmethod
    def _validate_metadata_token(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_envelope_metadata_token(value)
        return value

    @model_validator(mode="after")
    def _validate_embedded_receipt_hash(self) -> IngredientGenerationReceiptEnvelope:
        if self.generation_receipt_sha256 != canonical_sha256(self.generation_receipt):
            raise ValueError("ingredient generation receipt envelope hash mismatch")
        if (self.signer_id is None) != (self.signature is None):
            raise ValueError("ingredient generation receipt envelope signature metadata mismatch")
        return self


class IngredientTaskArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if value.strip() != value or not PUBLIC_ARTIFACT_PATH_RE.fullmatch(value):
            raise ValueError("ingredient task artifact ref path invalid")
        return value

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        return _reject_placeholder_sha256(value, "ingredient task artifact ref sha256 placeholder")


class IngredientTaskArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: IngredientTaskArtifactRef
    selection_receipt: IngredientTaskArtifactRef
    gate_receipt: IngredientTaskArtifactRef
    shortcut_receipt: IngredientTaskArtifactRef
    generation_receipt: IngredientTaskArtifactRef
    generation_receipt_envelope: IngredientTaskArtifactRef
    active_registry: IngredientTaskArtifactRef

    @field_validator(
        "active_registry",
        "gate_receipt",
        "generation_receipt",
        "generation_receipt_envelope",
        "selection_receipt",
        "shortcut_receipt",
        "task",
    )
    @classmethod
    def _validate_expected_path(
        cls,
        value: IngredientTaskArtifactRef,
        info: ValidationInfo,
    ) -> IngredientTaskArtifactRef:
        field_name = info.field_name
        if field_name is None:
            raise ValueError("ingredient task artifact path invalid")
        expected = INGREDIENT_TASK_ARTIFACT_PATHS[field_name]
        if value.path != expected:
            raise ValueError(f"ingredient task artifact path invalid: {field_name}")
        return value


class IngredientTaskArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    active_task_id: str = Field(pattern=INGREDIENT_LABEL_PATTERN)
    active_target_sha256: str = Field(pattern=SHA256_PATTERN)
    theorem_statement_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_selector_id: str
    selected_recipe_id: str
    selected_parameters_sha256: str = Field(pattern=SHA256_PATTERN)
    theorem_type_expr_sha256: str = Field(pattern=SHA256_PATTERN)
    novelty_family_hash: str = Field(pattern=SHA256_PATTERN)
    lemma_corpus_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    ingredient_repo_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    mathlib_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    recipe_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    netuid: int = Field(ge=0)
    tempo: int = Field(ge=0)
    active_K: int = Field(default=1, ge=1)
    queue_position: int = Field(default=0, ge=0)
    epoch_seed_sha256: str = Field(pattern=SHA256_PATTERN)
    challenge_seed_sha256: str = Field(pattern=SHA256_PATTERN)
    selection_seed_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    difficulty_state_sha256: str = Field(pattern=SHA256_PATTERN)
    difficulty_lane: DifficultyLane
    ingredient_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    selection_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    gate_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    shortcut_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_receipt_envelope_sha256: str = Field(pattern=SHA256_PATTERN)
    artifacts: IngredientTaskArtifacts

    @field_validator("active_K", "queue_position", "schema_version", "netuid", "tempo", mode="before")
    @classmethod
    def _validate_exact_ints(cls, value: Any) -> Any:
        return _exact_public_int(value)

    @model_validator(mode="after")
    def _validate_slot_window(self) -> IngredientTaskArtifactManifest:
        if self.queue_position >= self.active_K:
            raise ValueError("ingredient task artifact manifest slot invalid")
        return self

    @field_validator("active_task_id")
    @classmethod
    def _validate_active_task_id(cls, value: str) -> str:
        validate_ingredient_active_task_id(value)
        return value

    @field_validator("selected_recipe_id")
    @classmethod
    def _validate_selected_recipe_id(cls, value: str) -> str:
        _validate_public_label(value, "ingredient task artifact manifest recipe invalid")
        return value

    @field_validator("selected_selector_id")
    @classmethod
    def _validate_selected_selector_id(cls, value: str) -> str:
        _validate_public_label(value, "ingredient task artifact manifest selector invalid")
        return value

    @field_validator("ingredient_repo_commit", "mathlib_commit")
    @classmethod
    def _validate_commits(cls, value: str, info: ValidationInfo) -> str:
        return _reject_placeholder_git_commit(
            value,
            f"ingredient task artifact manifest {info.field_name} placeholder",
        )

    @field_validator("challenge_seed_sha256", "epoch_seed_sha256", "selection_seed_sha256")
    @classmethod
    def _validate_seed_hashes(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _reject_placeholder_sha256(value, "ingredient task artifact manifest seed placeholder")

    @field_validator(
        "difficulty_state_sha256",
        "gate_receipt_sha256",
        "generation_receipt_envelope_sha256",
        "generation_receipt_sha256",
        "ingredient_manifest_sha256",
        "lemma_corpus_snapshot_sha256",
        "novelty_family_hash",
        "recipe_bundle_sha256",
        "selected_parameters_sha256",
        "selection_receipt_sha256",
        "shortcut_receipt_sha256",
        "active_target_sha256",
        "theorem_statement_sha256",
        "theorem_type_expr_sha256",
    )
    @classmethod
    def _validate_manifest_hashes(cls, value: str) -> str:
        return _reject_placeholder_sha256(value, "ingredient task artifact manifest sha256 placeholder")


class IngredientEnvelopeSignatureVerifier(Protocol):
    def verify_envelope_signature(self, *, payload: bytes, signer_id: str, signature: str) -> bool:
        """Return True when the envelope signature is accepted."""
        ...


class Ss58IngredientEnvelopeSignatureVerifier:
    def verify_envelope_signature(self, *, payload: bytes, signer_id: str, signature: str) -> bool:
        from bittensor_wallet import Keypair

        return bool(Keypair(ss58_address=signer_id).verify(payload, signature))


INGREDIENT_JSONL_COMPONENT_MODELS: dict[str, type[BaseModel]] = {
    "definitions_sha256": DefinitionIngredient,
    "facts_sha256": FactIngredient,
    "source_theorems_sha256": FactIngredient,
    "source_lemmas_sha256": FactIngredient,
    "compatibility_graph_sha256": CompatibilityEdge,
    "source_compatibility_sha256": CompatibilityEdge,
    "definition_compatibility_sha256": CompatibilityEdge,
    "bridge_catalog_sha256": BridgeRule,
    "recipe_selectors_sha256": RecipeSelector,
}
INGREDIENT_JSON_COMPONENT_FIELDS = frozenset(INGREDIENT_MANIFEST_COMPONENT_PATHS) - frozenset(
    INGREDIENT_JSONL_COMPONENT_MODELS
)
INGREDIENT_EMPTY_COMPATIBILITY_JSONL_FIELDS = (
    "compatibility_graph_sha256",
    "source_compatibility_sha256",
    "definition_compatibility_sha256",
    "bridge_catalog_sha256",
    "recipe_selectors_sha256",
)
INGREDIENT_JSONL_COMPONENT_ID_FIELDS = {
    "definitions_sha256": "definition_id",
    "facts_sha256": "fact_id",
    "source_theorems_sha256": "fact_id",
    "source_lemmas_sha256": "fact_id",
    "compatibility_graph_sha256": "edge_id",
    "source_compatibility_sha256": "edge_id",
    "definition_compatibility_sha256": "edge_id",
    "bridge_catalog_sha256": "bridge_id",
    "recipe_selectors_sha256": "selector_id",
}


def canonical_json_bytes(model: BaseModel | dict[str, Any]) -> bytes:
    payload = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonical_sha256(model: BaseModel | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(model)).hexdigest()


def ingredient_difficulty_state_records(raw: bytes) -> tuple[dict[str, Any], ...]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as e:
        raise ValueError("requires valid ingredient difficulty state JSONL") from e
    records = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError("requires valid ingredient difficulty state JSONL") from e
        if not isinstance(payload, dict):
            raise ValueError("requires difficulty state JSONL objects")
        if not ingredient_difficulty_state_record_valid(payload):
            raise ValueError("difficulty state row malformed")
        if line.encode("utf-8") != canonical_json_bytes(payload):
            raise ValueError("difficulty state row noncanonical")
        records.append(payload)
    if raw != b"".join(canonical_json_bytes(record) + b"\n" for record in records):
        raise ValueError("difficulty state JSONL noncanonical")
    tempos = [cast(int, record["tempo"]) for record in records]
    if len(tempos) != len(set(tempos)):
        raise ValueError("difficulty state tempo duplicated")
    if tempos != sorted(tempos):
        raise ValueError("difficulty state tempo order invalid")
    return tuple(records)


def ingredient_difficulty_state_record_valid(record: dict[str, Any]) -> bool:
    tempo = record.get("tempo")
    difficulty_lane = record.get("difficulty_lane")
    return (
        set(record) == INGREDIENT_DIFFICULTY_STATE_KEYS
        and isinstance(tempo, int)
        and not isinstance(tempo, bool)
        and isinstance(difficulty_lane, str)
        and difficulty_lane == difficulty_lane.strip()
        and difficulty_lane in DIFFICULTY_LANES
    )


def ingredient_difficulty_state_active_lanes(
    records: tuple[dict[str, Any], ...],
    *,
    tempo: object,
) -> list[object]:
    if not isinstance(tempo, int) or isinstance(tempo, bool):
        return []
    return [
        record.get("difficulty_lane")
        for record in records
        if isinstance(record.get("tempo"), int)
        and not isinstance(record.get("tempo"), bool)
        and record.get("tempo") == tempo
    ]


def ingredient_difficulty_state_context(raw: bytes, *, tempo: int) -> tuple[str, DifficultyLane]:
    if not raw.strip():
        raise ValueError("requires nonempty ingredient difficulty state JSONL")
    records = ingredient_difficulty_state_records(raw)
    active_lanes = ingredient_difficulty_state_active_lanes(records, tempo=tempo)
    if len(active_lanes) > 1:
        raise ValueError("difficulty state has ambiguous active tempo")
    if not active_lanes:
        raise ValueError("difficulty state missing active tempo/lane")
    return hashlib.sha256(raw).hexdigest(), cast(DifficultyLane, active_lanes[0])


def ingredient_generation_receipt_envelope_signing_payload(
    envelope: IngredientGenerationReceiptEnvelope,
) -> bytes:
    payload = envelope.model_dump(mode="json")
    payload.pop("signature", None)
    return canonical_json_bytes(payload)


def ingredient_gate_receipt(
    *,
    receipt_kind: Literal["statement_gate", "shortcut_gate"],
    active_task_id: str,
    active_target_sha256: str,
    theorem_statement_sha256: str,
    ingredient_manifest_sha256: str,
    selection_receipt_sha256: str,
    runner: str = "declared-public-artifact",
    checks: tuple[str, ...] = ("metadata_bound",),
    details: dict[str, Any] | None = None,
) -> IngredientGateReceipt:
    return IngredientGateReceipt(
        schema_version=1,
        receipt_kind=receipt_kind,
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        selection_receipt_sha256=selection_receipt_sha256,
        status="passed",
        runner=runner,
        checks=checks,
        details=details or {},
    )


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def realize_ingredient_theorem_statement(
    root: Path,
    *,
    selection: IngredientSelectionReceipt,
    theorem_name: str,
) -> tuple[str, str]:
    _require_ingredient_root(root)
    validate_ingredient_theorem_name(theorem_name)
    recipe = _selected_recipe_from_root(root, selection)
    selected_nat = selection.selected_parameters.get("Nat")
    if (
        recipe.parameter_rule != "finite_nat"
        or not isinstance(selected_nat, str)
        or not NAT_PARAMETER_RE.fullmatch(selected_nat)
    ):
        raise ValueError(f"ingredient recipe realization parameter invalid: {recipe.recipe_id}:Nat")
    spec = BOOTSTRAP_RECIPE_BY_ID.get(recipe.recipe_id)
    if spec is None:
        raise ValueError(f"ingredient recipe realization unsupported: {recipe.recipe_id}")
    type_expr = spec.theorem_type_template.format(n=selected_nat)
    for definition_id in spec.required_definitions:
        if definition_id not in selection.selected_definition_ids:
            raise ValueError(f"ingredient recipe realization definition missing: {recipe.recipe_id}:{definition_id}")
    validate_ingredient_type_expr(type_expr)
    return type_expr, f"theorem {theorem_name} : {type_expr} := by\n  sorry"


def ingredient_soundness_witness_probe_script(
    root: Path,
    *,
    selection: IngredientSelectionReceipt,
    theorem_name: str,
    theorem_type_expr: str,
    imports: tuple[str, ...],
) -> str:
    expected_type, _statement = realize_ingredient_theorem_statement(
        root,
        selection=selection,
        theorem_name=theorem_name,
    )
    if theorem_type_expr != expected_type:
        raise ValueError("ingredient soundness witness theorem type mismatch")
    selected_nat = selection.selected_parameters["Nat"]
    spec = BOOTSTRAP_RECIPE_BY_ID.get(selection.selected_recipe_id)
    if spec is None:
        raise ValueError(f"ingredient soundness witness unsupported: {selection.selected_recipe_id}")
    validate_ingredient_imports(imports)
    return "\n".join(
        [
            *(f"import {module}" for module in imports),
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : {theorem_type_expr} := by",
            f"  simpa using _root_.{spec.soundness_theorem} {selected_nat}",
            "",
            "end Submission",
            "",
        ]
    )


INGREDIENT_TRIVIALITY_GATE_RUNNER = "lean-baseline-triviality-v1"
INGREDIENT_SHORTCUT_TACTIC_GATE_RUNNER = "lean-shortcut-tactics-v1"


def _ingredient_triviality_stack() -> tuple[tuple[str, str], ...]:
    from lemma.supply.gates import TRIVIALITY_STACK

    return tuple(TRIVIALITY_STACK)


def ingredient_triviality_probe_script(
    *,
    theorem_name: str,
    theorem_type_expr: str,
    imports: Sequence[str],
) -> str:
    validate_ingredient_theorem_name(theorem_name)
    validate_ingredient_type_expr(theorem_type_expr)
    validate_ingredient_imports(imports)
    proof_lines = ["  first", *(f"  | {body.strip()}" for _name, body in _ingredient_triviality_stack())]
    return "\n".join(
        [
            *(f"import {module}" for module in imports),
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : {theorem_type_expr} := by",
            *proof_lines,
            "",
            "end Submission",
            "",
        ]
    )


def ingredient_triviality_gate_details(
    *,
    theorem_name: str,
    theorem_type_expr: str,
    imports: Sequence[str],
    verify_reason: str,
    max_heartbeats: int,
) -> dict[str, Any]:
    if verify_reason != "compile_error":
        raise ValueError("ingredient triviality gate reason invalid")
    if not isinstance(max_heartbeats, int) or isinstance(max_heartbeats, bool) or max_heartbeats < 1:
        raise ValueError("ingredient triviality gate budget invalid")
    probe = ingredient_triviality_probe_script(
        theorem_name=theorem_name,
        theorem_type_expr=theorem_type_expr,
        imports=imports,
    )
    return {
        "runner": INGREDIENT_TRIVIALITY_GATE_RUNNER,
        "baseline_solved": False,
        "triviality_reason": "baseline_failed",
        "verify_reason": verify_reason,
        "triviality_budget_heartbeats": max_heartbeats,
        "triviality_probe_sha256": text_sha256(probe),
        "triviality_stack": [name for name, _body in _ingredient_triviality_stack()],
    }


def _ingredient_shortcut_tactics(checks: Iterable[str]) -> tuple[str, ...]:
    present = set(checks)
    return tuple(tactic for tactic in INGREDIENT_SHORTCUT_TACTIC_ORDER if tactic in present)


def _source_numeric_skeleton(type_expr: str) -> str:
    return re.sub(r"(?<![A-Za-z0-9_'])\d+(?![A-Za-z0-9_'])", "#", type_expr)


def _source_shape_skeleton(type_expr: str) -> str:
    return re.sub(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*", "ID", _source_numeric_skeleton(type_expr))


def _source_token_multiset(type_expr: str) -> tuple[str, ...]:
    tokens = re.findall(
        r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*|[=<>+\-*∧∨↔→≤≥]+",
        _source_numeric_skeleton(type_expr),
    )
    return tuple(sorted(token for token in tokens if token != "#"))


def _canonical_shortcut_checks(checks: Iterable[str]) -> tuple[str, ...]:
    present = set(checks)
    return tuple(check for check in INGREDIENT_SHORTCUT_CHECK_ORDER if check in present)


def _canonical_novelty_checks(checks: Iterable[str]) -> tuple[str, ...]:
    present = set(checks)
    return tuple(check for check in INGREDIENT_NOVELTY_CHECK_ORDER if check in present)


def ingredient_shortcut_tactics_for_selection(root: Path, selection: IngredientSelectionReceipt) -> tuple[str, ...]:
    _require_ingredient_root(root)
    recipe = _selected_recipe_from_root(root, selection)
    return _ingredient_shortcut_tactics(recipe.shortcut_checks)


def ingredient_shortcut_tactic_probe_script(
    *,
    theorem_name: str,
    theorem_type_expr: str,
    imports: Sequence[str],
    tactics: Sequence[str],
) -> str:
    validate_ingredient_theorem_name(theorem_name)
    validate_ingredient_type_expr(theorem_type_expr)
    validate_ingredient_imports(imports)
    tactic_tuple = tuple(tactics)
    if not tactic_tuple or tactic_tuple != _ingredient_shortcut_tactics(tactic_tuple):
        raise ValueError("ingredient shortcut tactic checks invalid")
    proof_lines = ["  first", *(f"  | {tactic}" for tactic in tactic_tuple)]
    return "\n".join(
        [
            *(f"import {module}" for module in imports),
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : {theorem_type_expr} := by",
            *proof_lines,
            "",
            "end Submission",
            "",
        ]
    )


def ingredient_shortcut_tactic_gate_details(
    *,
    theorem_name: str,
    theorem_type_expr: str,
    imports: Sequence[str],
    tactics: Sequence[str],
    verify_reason: str,
    max_heartbeats: int,
) -> dict[str, Any]:
    if verify_reason != "compile_error":
        raise ValueError("ingredient shortcut tactic gate reason invalid")
    if not isinstance(max_heartbeats, int) or isinstance(max_heartbeats, bool) or max_heartbeats < 1:
        raise ValueError("ingredient shortcut tactic gate budget invalid")
    tactic_tuple = tuple(tactics)
    probe = ingredient_shortcut_tactic_probe_script(
        theorem_name=theorem_name,
        theorem_type_expr=theorem_type_expr,
        imports=imports,
        tactics=tactic_tuple,
    )
    return {
        "runner": INGREDIENT_SHORTCUT_TACTIC_GATE_RUNNER,
        "shortcut_tactic_solved": False,
        "shortcut_tactic_reason": "tactic_failed",
        "verify_reason": verify_reason,
        "shortcut_tactic_budget_heartbeats": max_heartbeats,
        "shortcut_tactic_probe_sha256": text_sha256(probe),
        "shortcut_tactics": list(tactic_tuple),
    }


def ingredient_statement_gate_receipt(
    root: Path,
    *,
    selection: IngredientSelectionReceipt,
    active_task_id: str,
    active_target_sha256: str,
    theorem_statement_sha256: str,
    ingredient_manifest_sha256: str,
    selection_receipt_sha256: str,
    theorem_type_expr: str,
    runner: str,
    checks: tuple[str, ...],
    triviality_details: dict[str, Any] | None = None,
    novelty_details: dict[str, Any] | None = None,
) -> IngredientGateReceipt:
    _require_ingredient_root(root)
    validate_ingredient_type_expr(theorem_type_expr)
    if selection_receipt_sha256 != canonical_sha256(selection):
        raise ValueError("ingredient gate selection receipt mismatch")
    required_checks = {"statement_hash_bound", "target_hash_bound", "soundness_template_bound"}
    missing_checks = sorted(required_checks - set(checks))
    if missing_checks:
        raise ValueError(f"ingredient statement gate required check missing: {', '.join(missing_checks)}")
    _validate_statement_gate_checks(checks)
    lean_reason = _statement_gate_single_reason(checks, "lean_verify_reason:")
    soundness_reason = _statement_gate_single_reason(checks, "soundness_template_verify_reason:")
    check_set = set(checks)
    triviality_checks = {"bounded_triviality_checked", "baseline_triviality_not_solved"}
    present_triviality_checks = triviality_checks & check_set
    triviality_reasons = tuple(
        check.removeprefix("bounded_triviality_reason:")
        for check in checks
        if check.startswith("bounded_triviality_reason:")
    )
    novelty_checks = {"novelty_cache_bound", "theorem_type_not_in_novelty_cache"}
    novelty_family_check = "selection_family_not_in_novelty_cache"
    present_novelty_checks = (novelty_checks | {novelty_family_check}) & check_set
    requires_statement_gate = (
        "soundness_template_typechecked" in check_set
        or "soundness_template_no_holes" in check_set
        or "soundness_template_witness_checked" in check_set
        or soundness_reason is not None
        or bool(present_triviality_checks)
        or bool(triviality_reasons)
        or bool(present_novelty_checks)
    )
    if runner not in {"declared-public-artifact", "lean-statement-gate"}:
        raise ValueError("ingredient statement gate runner invalid")
    if ("lean_challenge_typechecked" in checks or lean_reason is not None) and runner != "lean-statement-gate":
        raise ValueError("ingredient statement gate runner invalid")
    if runner == "lean-statement-gate":
        if "lean_challenge_typechecked" not in checks:
            raise ValueError("ingredient statement gate required check missing: lean_challenge_typechecked")
        if lean_reason is None:
            raise ValueError("ingredient statement gate Lean reason missing")
        if lean_reason != "lean_verify_reason:ok":
            raise ValueError("ingredient statement gate Lean reason invalid")
    if soundness_reason is not None and "soundness_template_typechecked" not in checks:
        raise ValueError("ingredient statement gate soundness template checks missing")
    if "soundness_template_no_holes" in checks and "soundness_template_typechecked" not in checks:
        raise ValueError("ingredient statement gate soundness template checks missing")
    if "soundness_template_typechecked" in checks and soundness_reason is None:
        raise ValueError("ingredient statement gate soundness template reason missing")
    if soundness_reason is not None and soundness_reason != "soundness_template_verify_reason:ok":
        raise ValueError("ingredient statement gate soundness template reason invalid")
    if "soundness_template_typechecked" in checks and "soundness_template_no_holes" not in checks:
        raise ValueError("ingredient statement gate required check missing: soundness_template_no_holes")
    if "soundness_template_witness_checked" in checks and "soundness_template_no_holes" not in checks:
        raise ValueError("ingredient statement gate soundness template checks missing")
    present_triviality_reason = any(triviality_reasons)
    if present_triviality_checks or triviality_reasons:
        missing_triviality_checks = sorted(triviality_checks - set(checks))
        if missing_triviality_checks:
            raise ValueError(
                f"ingredient statement gate required check missing: {', '.join(missing_triviality_checks)}"
            )
        if not present_triviality_reason:
            raise ValueError("ingredient statement gate triviality reason missing")
        if triviality_reasons != ("compile_error",):
            raise ValueError("ingredient statement gate triviality reason invalid")
        if triviality_details is None:
            raise ValueError("ingredient statement gate triviality details missing")
        _validate_triviality_gate_details(triviality_details)
    elif triviality_details is not None:
            raise ValueError("ingredient statement gate triviality checks missing")
    if present_novelty_checks:
        missing_novelty_checks = sorted(novelty_checks - set(checks))
        if missing_novelty_checks:
            raise ValueError(
                f"ingredient statement gate required check missing: {', '.join(missing_novelty_checks)}"
            )
        if novelty_details is None:
            raise ValueError("ingredient statement gate novelty details missing")
        has_family_details = "novelty_family_hash" in novelty_details
        if novelty_family_check in checks and not has_family_details:
            raise ValueError("ingredient statement gate novelty family details missing")
        if has_family_details and novelty_family_check not in checks:
            raise ValueError("ingredient statement gate novelty family check missing")
        _validate_novelty_gate_details(root, novelty_details)
    elif novelty_details is not None:
        raise ValueError("ingredient statement gate novelty checks missing")
    if requires_statement_gate and "lean_challenge_typechecked" not in checks:
        raise ValueError("ingredient statement gate required check missing: lean_challenge_typechecked")
    if requires_statement_gate and lean_reason is None:
        raise ValueError("ingredient statement gate Lean reason missing")
    _validate_statement_gate_check_order(checks)
    template_path, template_bytes = ingredient_soundness_template_source(root, selection)
    details: dict[str, Any] = {
        "selected_selector_id": selection.selected_selector_id,
        "selected_recipe_id": selection.selected_recipe_id,
        "selected_parameters": selection.selected_parameters,
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": selection.selected_parameters}),
        "soundness_template": template_path,
        "soundness_template_sha256": hashlib.sha256(template_bytes).hexdigest(),
        "theorem_type_expr_sha256": text_sha256(theorem_type_expr),
    }
    if triviality_details is not None:
        details["triviality_gate"] = triviality_details
    if novelty_details is not None:
        details["novelty_gate"] = novelty_details
    return ingredient_gate_receipt(
        receipt_kind="statement_gate",
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        selection_receipt_sha256=selection_receipt_sha256,
        runner=runner,
        checks=checks,
        details=details,
    )


def _validate_statement_gate_checks(checks: tuple[str, ...]) -> None:
    duplicate = _first_duplicate(checks)
    if duplicate is not None:
        raise ValueError(f"ingredient statement gate check duplicate: {duplicate}")
    for check in checks:
        if check in SUPPORTED_INGREDIENT_STATEMENT_GATE_CHECKS:
            continue
        prefix = next(
            (
                candidate
                for candidate in SUPPORTED_INGREDIENT_STATEMENT_GATE_CHECK_PREFIXES
                if check.startswith(candidate)
            ),
            None,
        )
        if prefix is None:
            raise ValueError(f"ingredient statement gate check unsupported: {check}")
        _validate_public_token(
            check.removeprefix(prefix),
            f"ingredient statement gate check invalid: {check}",
        )


def _statement_gate_single_reason(checks: tuple[str, ...], prefix: str) -> str | None:
    matches = tuple(check for check in checks if check.startswith(prefix))
    if len(matches) > 1:
        raise ValueError(f"ingredient statement gate check duplicate: {prefix.removesuffix(':')}")
    return matches[0] if matches else None


def _validate_statement_gate_check_order(checks: tuple[str, ...]) -> None:
    check_set = set(checks)
    ordered: list[str] = []

    def add(check: str) -> None:
        if check in check_set:
            ordered.append(check)

    def add_reason(prefix: str) -> None:
        reason = _statement_gate_single_reason(checks, prefix)
        if reason is not None:
            ordered.append(reason)

    add("lean_challenge_typechecked")
    add_reason("lean_verify_reason:")
    add("statement_hash_bound")
    add("target_hash_bound")
    add("soundness_template_bound")
    add("soundness_template_typechecked")
    add("soundness_template_no_holes")
    add("soundness_template_witness_checked")
    add_reason("soundness_template_verify_reason:")
    add("bounded_triviality_checked")
    add("baseline_triviality_not_solved")
    add_reason("bounded_triviality_reason:")
    add("novelty_cache_bound")
    add("theorem_type_not_in_novelty_cache")
    add("selection_family_not_in_novelty_cache")
    if tuple(ordered) != checks:
        raise ValueError("ingredient statement gate check order invalid")


def ingredient_soundness_template_source(root: Path, selection: IngredientSelectionReceipt) -> tuple[str, bytes]:
    _require_ingredient_root(root)
    recipe = _selected_recipe_from_root(root, selection)
    template_path = Path(recipe.soundness_template)
    if (
        recipe.soundness_template != recipe.soundness_template.strip()
        or template_path.is_absolute()
        or ".." in template_path.parts
    ):
        raise ValueError("ingredient statement gate soundness template path invalid")
    path = root / "recipes" / template_path
    if path.is_symlink() or not path.is_file():
        raise ValueError("ingredient statement gate soundness template path invalid")
    try:
        template_bytes = path.read_bytes()
    except OSError as e:
        raise ValueError("ingredient statement gate soundness template missing") from e
    try:
        template_text = template_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("ingredient statement gate soundness template invalid") from e
    _validate_soundness_template_source(recipe, template_text)
    return template_path.as_posix(), template_bytes


def ingredient_novelty_gate_details(
    *,
    theorem_type_expr: str,
    novelty_cache: Any,
    selection: IngredientSelectionReceipt | None = None,
) -> dict[str, Any]:
    from lemma.supply.novelty import NOVELTY_CACHE_VERSION, statement_hash

    validate_ingredient_type_expr(theorem_type_expr)
    digest = statement_hash(theorem_type_expr)
    if novelty_cache.contains(digest):
        raise ValueError("ingredient novelty gate failed: theorem type already in novelty cache")
    details = {
        "novelty_cache_entries": len(novelty_cache.statement_hashes),
        "novelty_cache_sha256": novelty_cache.sha256,
        "novelty_cache_version": NOVELTY_CACHE_VERSION,
        "novelty_policy_check": "theorem_type_cache",
        "novelty_statement_hash": digest,
    }
    if selection is not None:
        family_hash = ingredient_novelty_family_hash(selection)
        if novelty_cache.contains_family(family_hash):
            raise ValueError("ingredient novelty gate failed: selection family already in novelty cache")
        details["novelty_family_hash"] = family_hash
        details["novelty_family_cache_entries"] = len(novelty_cache.novelty_family_hashes)
    return details


def ingredient_shortcut_gate_receipt(
    root: Path,
    *,
    selection: IngredientSelectionReceipt,
    active_task_id: str,
    active_target_sha256: str,
    theorem_statement_sha256: str,
    ingredient_manifest_sha256: str,
    selection_receipt_sha256: str,
    theorem_type_expr: str,
    mathlib_commit: str,
    theorem_name: str | None = None,
    imports: Sequence[str] | None = None,
    shortcut_tactic_details: dict[str, Any] | None = None,
) -> IngredientGateReceipt:
    validate_ingredient_type_expr(theorem_type_expr)
    if selection_receipt_sha256 != canonical_sha256(selection):
        raise ValueError("ingredient gate selection receipt mismatch")
    theorem_type = _normalized_lean_expr(theorem_type_expr)
    if not theorem_type:
        raise ValueError("ingredient shortcut gate theorem type malformed")
    recipe = _selected_recipe_from_root(root, selection)
    shortcut_checks = recipe.shortcut_checks
    shortcut_tactics = _ingredient_shortcut_tactics(shortcut_checks)
    source_subterm_oracle = "source_subterm_oracle" in shortcut_checks
    source_numeric_skeleton_oracle = "source_numeric_skeleton_oracle" in shortcut_checks
    source_shape_skeleton_oracle = "source_shape_skeleton_oracle" in shortcut_checks
    source_token_multiset_oracle = "source_token_multiset_oracle" in shortcut_checks
    if shortcut_tactics:
        if shortcut_tactic_details is None:
            raise ValueError("ingredient shortcut tactic details missing")
        shortcut_tactic_gate = shortcut_tactic_details
        if theorem_name is None or imports is None:
            raise ValueError("ingredient shortcut tactic context missing")
        _validate_shortcut_tactic_gate_details(
            shortcut_tactic_gate,
            theorem_name=theorem_name,
            theorem_type_expr=theorem_type_expr,
            imports=imports,
            expected_tactics=shortcut_tactics,
        )
    elif shortcut_tactic_details is not None:
        raise ValueError("ingredient shortcut tactic checks missing")
    facts = _fact_rows_from_root(root, mathlib_commit=mathlib_commit)
    facts_by_id: dict[str, FactIngredient] = {}
    fact_type_catalog = []
    subterm_match_count = 0
    numeric_skeleton_match_count = 0
    shape_skeleton_match_count = 0
    token_multiset_match_count = 0
    theorem_numeric_skeleton = _source_numeric_skeleton(theorem_type)
    theorem_shape_skeleton = _source_shape_skeleton(theorem_type)
    theorem_token_multiset = _source_token_multiset(theorem_type)
    for fact in facts:
        fact_type = _normalized_lean_expr(fact.type_expr)
        fact_numeric_skeleton = _source_numeric_skeleton(fact_type)
        fact_shape_skeleton = _source_shape_skeleton(fact_type)
        fact_token_multiset = _source_token_multiset(fact_type)
        if fact_type == theorem_type:
            raise ValueError("ingredient shortcut gate failed: source fact exactly matches theorem type")
        if source_subterm_oracle and fact_type in theorem_type:
            raise ValueError("ingredient shortcut gate failed: source fact type appears inside theorem type")
        if source_subterm_oracle:
            subterm_match_count += int(fact_type in theorem_type)
        if source_numeric_skeleton_oracle and fact_numeric_skeleton == theorem_numeric_skeleton:
            raise ValueError("ingredient shortcut gate failed: source fact numeric skeleton matches theorem type")
        if source_numeric_skeleton_oracle:
            numeric_skeleton_match_count += int(fact_numeric_skeleton == theorem_numeric_skeleton)
        if source_shape_skeleton_oracle and fact_shape_skeleton == theorem_shape_skeleton:
            raise ValueError("ingredient shortcut gate failed: source fact shape skeleton matches theorem type")
        if source_shape_skeleton_oracle:
            shape_skeleton_match_count += int(fact_shape_skeleton == theorem_shape_skeleton)
        if source_token_multiset_oracle and fact_token_multiset == theorem_token_multiset:
            raise ValueError("ingredient shortcut gate failed: source fact token multiset matches theorem type")
        if source_token_multiset_oracle:
            token_multiset_match_count += int(fact_token_multiset == theorem_token_multiset)
        fact_type_catalog.append({"fact_id": fact.fact_id, "type_sha256": text_sha256(fact_type)})
        if fact.fact_id in selection.selected_fact_ids:
            if fact.fact_id in facts_by_id:
                raise ValueError("ingredient shortcut gate selected fact ambiguous")
            facts_by_id[fact.fact_id] = fact
    missing = [fact_id for fact_id in selection.selected_fact_ids if fact_id not in facts_by_id]
    if missing:
        raise ValueError("ingredient shortcut gate selected fact missing")
    fact_type_hashes = {}
    for fact_id in selection.selected_fact_ids:
        fact_type = _normalized_lean_expr(facts_by_id[fact_id].type_expr)
        fact_type_hashes[fact_id] = text_sha256(fact_type)
    checks = [
        "recipe_shortcut_policy_bound",
        "selected_facts_loaded",
        "source_fact_catalog_scanned",
        "no_source_fact_type_exact_match",
    ]
    details: dict[str, Any] = {
        "declared_shortcut_checks": list(shortcut_checks),
        "selected_fact_count": len(selection.selected_fact_ids),
        "selected_fact_ids": list(selection.selected_fact_ids),
        "selected_fact_type_sha256s": fact_type_hashes,
        "selected_selector_id": selection.selected_selector_id,
        "selected_recipe_id": selection.selected_recipe_id,
        "source_fact_count": len(facts),
        "source_fact_type_catalog_sha256": canonical_sha256(
            {"fact_types": sorted(fact_type_catalog, key=lambda item: item["fact_id"])}
        ),
        "source_oracle_mode": "exact_type_catalog_v1",
        "theorem_type_expr_sha256": text_sha256(theorem_type),
    }
    runner = "source-oracle-exact-match-v1"
    if source_subterm_oracle:
        checks.append("no_source_fact_type_subterm_match")
        details["source_subterm_oracle_mode"] = "normalized_type_substring_v1"
        details["source_subterm_match_count"] = subterm_match_count
        runner = "source-oracle-subterm-v1"
    if source_numeric_skeleton_oracle:
        checks.append("no_source_fact_numeric_skeleton_match")
        details["source_numeric_skeleton_match_count"] = numeric_skeleton_match_count
        details["source_numeric_skeleton_oracle_mode"] = "decimal_token_skeleton_v1"
        details["theorem_numeric_skeleton_sha256"] = text_sha256(theorem_numeric_skeleton)
        runner = "source-oracle-semantic-v1"
    if source_shape_skeleton_oracle:
        checks.append("no_source_fact_type_shape_skeleton_match")
        details["source_shape_skeleton_match_count"] = shape_skeleton_match_count
        details["source_shape_skeleton_oracle_mode"] = "identifier_decimal_token_skeleton_v1"
        details["theorem_shape_skeleton_sha256"] = text_sha256(theorem_shape_skeleton)
        runner = "source-oracle-semantic-v1"
    if source_token_multiset_oracle:
        checks.append("no_source_fact_token_multiset_match")
        details["source_token_multiset_match_count"] = token_multiset_match_count
        details["source_token_multiset_oracle_mode"] = "identifier_operator_multiset_v1"
        details["theorem_token_multiset_sha256"] = canonical_sha256({"tokens": list(theorem_token_multiset)})
        runner = "source-oracle-semantic-v1"
    if shortcut_tactics:
        checks.extend(
            (
                "shortcut_tactics_checked",
                *(f"no_{tactic}_shortcut" for tactic in shortcut_tactics),
                f"shortcut_tactics_reason:{shortcut_tactic_gate['verify_reason']}",
            )
        )
        details["shortcut_tactic_gate"] = shortcut_tactic_gate
        runner = "source-oracle-shortcut-tactics-v1"
    return ingredient_gate_receipt(
        receipt_kind="shortcut_gate",
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        selection_receipt_sha256=selection_receipt_sha256,
        runner=runner,
        checks=tuple(checks),
        details=details,
    )


def _canonical_json_object(
    path: Path,
    *,
    invalid: str,
    noncanonical: str,
    path_invalid: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(path_invalid or invalid)
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(invalid) from e
    if not isinstance(payload, dict):
        raise ValueError(invalid)
    if raw != canonical_json_bytes(payload) + b"\n":
        raise ValueError(noncanonical)
    return payload, raw


def _require_ingredient_root(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("ingredient root path invalid")


def _require_ingredient_output_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("ingredient root path invalid")


def ingredient_manifest_component_hashes(root: Path) -> dict[str, str]:
    _require_ingredient_root(root)
    hashes = {}
    for field, relative_path in INGREDIENT_MANIFEST_COMPONENT_PATHS.items():
        path = root / relative_path
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"ingredient component path invalid: {field}")
        hashes[field] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def ingredient_root_mathlib_commit(root: Path) -> str:
    _require_ingredient_root(root)
    path = root / "mathlib_commit.txt"
    if path.is_symlink() or not path.is_file():
        raise ValueError("ingredient mathlib commit path invalid")
    try:
        root_mathlib_commit = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as e:
        raise ValueError("ingredient mathlib commit invalid") from e
    _validate_git_commit(root_mathlib_commit, "ingredient mathlib commit")
    return root_mathlib_commit


def ingredient_manifest_from_root(
    root: Path,
    *,
    lemma_corpus_snapshot_sha256: str,
    mathlib_commit: str | None = None,
) -> IngredientManifest:
    root_mathlib_commit = ingredient_root_mathlib_commit(root)
    if mathlib_commit is not None:
        pinned_mathlib_commit = mathlib_commit.strip()
        if pinned_mathlib_commit != mathlib_commit:
            raise ValueError("ingredient mathlib commit invalid")
        _validate_git_commit(pinned_mathlib_commit, "ingredient mathlib commit")
        if pinned_mathlib_commit != root_mathlib_commit:
            raise ValueError("ingredient mathlib commit mismatch")
    else:
        pinned_mathlib_commit = root_mathlib_commit
    return IngredientManifest(
        schema_version=1,
        mathlib_commit=pinned_mathlib_commit,
        lemma_corpus_snapshot_sha256=lemma_corpus_snapshot_sha256,
        **cast(Any, ingredient_manifest_component_hashes(root)),
    )


def ingredient_manifest_bytes(manifest: IngredientManifest) -> bytes:
    return canonical_json_bytes(manifest) + b"\n"


def write_mathlib_ingredient_extract(
    rows: Iterable[MathlibSnapshotRow],
    root: Path,
    *,
    definitions: Iterable[MathlibDefinitionLike] = (),
) -> dict[str, object]:
    _require_ingredient_output_root(root)
    materialized = tuple(rows)
    definition_materialized = tuple(definitions)
    all_rows: tuple[MathlibSnapshotRow | MathlibDefinitionLike, ...] = (
        *materialized,
        *definition_materialized,
    )
    if not all_rows:
        raise ValueError("ingredient mathlib extraction produced no rows")
    mathlib_commits = {row.mathlib_rev for row in all_rows}
    if len(mathlib_commits) != 1:
        raise ValueError("ingredient mathlib extraction requires one mathlib commit")
    mathlib_commit = all_rows[0].mathlib_rev
    _validate_git_commit(mathlib_commit, "ingredient mathlib commit")
    definition_rows = tuple(
        sorted(
            (_mathlib_definition_ingredient(row) for row in definition_materialized),
            key=lambda definition: definition.definition_id,
        )
    )
    fact_rows = tuple(
        sorted((_mathlib_fact_ingredient(row) for row in materialized), key=lambda fact: fact.fact_id)
    )
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"], definition_rows)
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], fact_rows)
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["source_theorems_sha256"], ())
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["source_lemmas_sha256"], ())
    _write_manifestable_empty_scaffold(root)
    mathlib_commit_path = _ingredient_write_file_path(root, "mathlib_commit.txt")
    mathlib_commit_path.parent.mkdir(parents=True, exist_ok=True)
    mathlib_commit_path.write_text(f"{mathlib_commit}\n", encoding="utf-8")
    source_license_counts = Counter(row.source_license for row in all_rows)
    difficulty_lane_counts = Counter(_difficulty_lane_for_depth(row.queue_depth) for row in all_rows)
    extraction_report = {
        "schema_version": 1,
        "mathlib_commit": mathlib_commit,
        "source_row_count": len(all_rows),
        "fact_count": len(fact_rows),
        "definition_count": len(definition_rows),
        "source_license_counts": dict(sorted(source_license_counts.items())),
    }
    quality_report = {
        "definition_count": len(definition_rows),
        "fact_count": len(fact_rows),
        "compatibility_edge_count": 0,
        "recipe_count": 0,
        "difficulty_lane_coverage": dict(sorted(difficulty_lane_counts.items())),
        "bridge_coverage": {},
        "estimated_theorem_space_size": 0,
        "shortcut_risk_distribution": {},
        "reserve_selector_health": {"ready": False},
    }
    _write_json(root, INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"], extraction_report)
    _write_json(root, INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"], quality_report)
    return {
        "definition_count": len(definition_rows),
        "fact_count": len(fact_rows),
        "mathlib_commit": mathlib_commit,
        "output": str(root),
    }


def build_empty_ingredient_compatibility(root: Path) -> dict[str, object]:
    _require_ingredient_output_root(root)
    _write_manifestable_empty_scaffold(root)
    recipes = _recipe_rules(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        shortcut_policy_checks=_shortcut_policy_checks(root),
    )
    if recipes:
        raise ValueError("ingredient paid compatibility build requires certified recipe generator")
    for field in INGREDIENT_EMPTY_COMPATIBILITY_JSONL_FIELDS:
        _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS[field], ())
    counts = ingredient_manifest_component_schema_counts(root)
    _sync_quality_report_counts(root, counts)
    return {
        "compatibility_edge_count": 0,
        "recipe_count": 0,
        "root": str(root),
        "status": "empty_scaffold",
    }


def build_ingredient_compatibility(root: Path) -> dict[str, object]:
    _require_ingredient_output_root(root)
    _write_manifestable_empty_scaffold(root)
    mathlib_commit = ingredient_root_mathlib_commit(root)
    shortcut_policy_checks = _shortcut_policy_checks(root)
    recipes = _recipe_rules(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        shortcut_policy_checks=shortcut_policy_checks,
    )
    recipe_ids = {recipe.recipe_id for recipe in recipes}
    definitions = _definition_rows_from_root(root, mathlib_commit=mathlib_commit, recipe_ids=recipe_ids)
    facts = _fact_rows_from_root(root, mathlib_commit=mathlib_commit, recipe_ids=recipe_ids)
    if not recipes:
        recipes = _write_bootstrap_recipe_artifacts(root, definitions, facts)
        if not recipes:
            return build_empty_ingredient_compatibility(root)
        recipe_ids = {recipe.recipe_id for recipe in recipes}
        definitions = _definition_rows_from_root(root, mathlib_commit=mathlib_commit, recipe_ids=recipe_ids)
        facts = _fact_rows_from_root(root, mathlib_commit=mathlib_commit, recipe_ids=recipe_ids)
    _parameter_sets(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], recipes=recipes)
    edges = tuple(
        sorted(
            (
                edge
                for recipe in recipes
                for edge in _compatibility_edges_for_recipe(recipe, definitions, facts)
            ),
            key=lambda edge: edge.edge_id,
        )
    )
    selectors = tuple(
        sorted(
            (
                RecipeSelector(
                    selector_id=f"{lane}_{recipe.recipe_id}_selector_v1",
                    difficulty_lane=lane,
                    recipe_ids=(recipe.recipe_id,),
                    ingredient_filters={"domains": list(recipe.domains)},
                )
                for recipe in recipes
                for lane in DIFFICULTY_LANES
            ),
            key=lambda selector: selector.selector_id,
        )
    )
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["compatibility_graph_sha256"], edges)
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["source_compatibility_sha256"], ())
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["definition_compatibility_sha256"], ())
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["bridge_catalog_sha256"], ())
    _write_ingredient_jsonl(root, INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"], selectors)
    counts = ingredient_manifest_component_schema_counts(root, mathlib_commit=mathlib_commit)
    quality_report = {
        **_ingredient_report_counts(root, counts),
        "difficulty_lane_coverage": {lane: len(recipes) for lane in DIFFICULTY_LANES},
        "bridge_coverage": {},
        "estimated_theorem_space_size": len(selectors),
        "shortcut_risk_distribution": {"paid_eligible": len(recipes)},
        "reserve_selector_health": {"ready": True},
    }
    _write_json(root, INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"], quality_report)
    return {
        "compatibility_edge_count": len(edges),
        "recipe_count": len(recipes),
        "root": str(root),
        "selector_count": len(selectors),
        "status": "paid_compatibility",
    }


def _definition_rows_from_root(
    root: Path,
    *,
    mathlib_commit: str,
    recipe_ids: set[str],
) -> tuple[DefinitionIngredient, ...]:
    return cast(
        tuple[DefinitionIngredient, ...],
        _jsonl_model_rows(
            root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
            "definitions_sha256",
            DefinitionIngredient,
            mathlib_commit=mathlib_commit,
            recipe_ids=recipe_ids,
        ),
    )


def _write_bootstrap_recipe_artifacts(
    root: Path,
    definitions: tuple[DefinitionIngredient, ...],
    facts: tuple[FactIngredient, ...],
) -> tuple[RecipeRule, ...]:
    definition_ids = {definition.definition_id for definition in definitions}
    recipes = []
    for spec in BOOTSTRAP_RECIPE_SPECS:
        if not set(spec.required_definitions).issubset(definition_ids):
            continue
        matching_fact = next(
            (
                fact
                for fact in facts
                if fact.kind in {"lemma", "theorem"}
                and fact.domain == spec.fact_domain
                and spec.fact_pattern in fact.fact_id
            ),
            None,
        )
        if matching_fact is None:
            continue
        recipes.append(
            RecipeRule(
                recipe_id=spec.recipe_id,
                version=1,
                domains=spec.domains,
                required_ingredient_classes=spec.required_ingredient_classes,
                required_definitions=spec.required_definitions,
                required_fact_kinds=(matching_fact.kind,),
                parameter_rule="finite_nat",
                soundness_template=spec.soundness_template,
                shortcut_checks=tuple(
                    check for check in INGREDIENT_SHORTCUT_CHECK_ORDER if check not in INGREDIENT_SHORTCUT_TACTIC_CHECKS
                ),
            )
        )
    recipes = sorted(recipes, key=lambda recipe: recipe.recipe_id)
    if not recipes:
        return ()
    _write_json(
        root,
        INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [recipe.model_dump(mode="json") for recipe in recipes]},
    )
    _write_json(root, INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {"Nat": ["2", "3"]})
    _write_json(
        root,
        INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": [recipe.recipe_id for recipe in recipes]},
    )
    _write_json(
        root,
        INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": list(INGREDIENT_SHORTCUT_CHECK_ORDER)},
    )
    for recipe in recipes:
        spec = BOOTSTRAP_RECIPE_BY_ID[recipe.recipe_id]
        theorem = f"theorem {spec.soundness_theorem} (n : Nat) : {spec.soundness_type_expr} := by"
        template_path = _ingredient_write_file_path(root, f"recipes/{spec.soundness_template}")
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(
            "\n".join(["import Mathlib", "", theorem, "  simp", ""]),
            encoding="utf-8",
        )
    return tuple(recipes)


def select_ingredient_receipt_from_root(
    root: Path,
    *,
    challenge_seed_sha256: str,
    difficulty_lane: DifficultyLane,
    mathlib_commit: str | None = None,
) -> IngredientSelectionReceipt:
    _require_ingredient_root(root)
    _validate_sha256(challenge_seed_sha256, "ingredient challenge seed sha256")
    root_mathlib_commit = ingredient_root_mathlib_commit(root)
    if mathlib_commit is not None and root_mathlib_commit != mathlib_commit:
        _validate_git_commit(mathlib_commit, "ingredient mathlib commit")
        raise ValueError("ingredient mathlib commit mismatch")
    expected_mathlib_commit = mathlib_commit or root_mathlib_commit
    component_schema_counts = ingredient_manifest_component_schema_counts(
        root,
        mathlib_commit=expected_mathlib_commit,
    )
    ingredient_repository_report_hashes(
        root,
        component_schema_counts=component_schema_counts,
        mathlib_commit=expected_mathlib_commit,
    )
    ingredient_recipe_artifact_hashes(root)
    shortcut_policy_checks = _shortcut_policy_checks(root)
    recipes = _recipe_rules(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        shortcut_policy_checks=shortcut_policy_checks,
    )
    recipe_ids = {recipe.recipe_id for recipe in recipes}
    definitions = cast(
        tuple[DefinitionIngredient, ...],
        _jsonl_model_rows(
            root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
            "definitions_sha256",
            DefinitionIngredient,
            mathlib_commit=expected_mathlib_commit,
            recipe_ids=recipe_ids,
        ),
    )
    facts = tuple(
        row
        for field in ("facts_sha256", "source_theorems_sha256", "source_lemmas_sha256")
        for row in cast(
            tuple[FactIngredient, ...],
            _jsonl_model_rows(
                root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field],
                field,
                FactIngredient,
                mathlib_commit=expected_mathlib_commit,
                recipe_ids=recipe_ids,
            ),
        )
    )
    compatibility_edges = tuple(
        row
        for field in ("compatibility_graph_sha256", "source_compatibility_sha256", "definition_compatibility_sha256")
        for row in cast(
            tuple[CompatibilityEdge, ...],
            _jsonl_model_rows(
                root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field],
                field,
                CompatibilityEdge,
                mathlib_commit=expected_mathlib_commit,
                recipe_ids=recipe_ids,
            ),
        )
    )
    bridges = cast(
        tuple[BridgeRule, ...],
        _jsonl_model_rows(
            root / INGREDIENT_MANIFEST_COMPONENT_PATHS["bridge_catalog_sha256"],
            "bridge_catalog_sha256",
            BridgeRule,
            mathlib_commit=expected_mathlib_commit,
            recipe_ids=recipe_ids,
        ),
    )
    selectors = cast(
        tuple[RecipeSelector, ...],
        _jsonl_model_rows(
            root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"],
            "recipe_selectors_sha256",
            RecipeSelector,
            mathlib_commit=expected_mathlib_commit,
            recipe_ids=recipe_ids,
        ),
    )
    return select_fixture_ingredients(
        challenge_seed_sha256=challenge_seed_sha256,
        difficulty_lane=difficulty_lane,
        selectors=selectors,
        recipes=recipes,
        definitions=definitions,
        facts=facts,
        compatibility_edges=compatibility_edges,
        bridges=bridges,
        parameter_sets=_parameter_sets(
            root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"],
            recipes=recipes,
        ),
    )


def verify_ingredient_task_against_root(
    task: LemmaTask,
    root: Path,
    *,
    manifest: IngredientManifest,
    ingredient_manifest_sha256: str,
    challenge_seed_sha256: str,
    difficulty_lane: DifficultyLane,
) -> IngredientGenerationReceipt:
    validate_ingredient_task_public_envelope(task, mathlib_commit=manifest.mathlib_commit)
    receipt = ingredient_generation_receipt_from_task(task)
    queue_position = task.queue_position
    if queue_position is None or queue_position < 0 or queue_position >= receipt.active_K:
        raise ValueError("ingredient task queue position mismatch")
    if task.queue_depth != 0:
        raise ValueError("ingredient task queue depth mismatch")
    if task.frontier_depth != 0:
        raise ValueError("ingredient task frontier depth mismatch")
    selection_seed_sha256 = ingredient_challenge_slot_seed_sha256(
        challenge_seed_sha256=challenge_seed_sha256,
        queue_position=queue_position,
        active_K=receipt.active_K,
    )
    expected_selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=selection_seed_sha256,
        difficulty_lane=difficulty_lane,
        mathlib_commit=manifest.mathlib_commit,
    )
    if receipt.selection != expected_selection:
        raise ValueError("ingredient task selection mismatch")
    expected_pins = {
        "ingredient_manifest_sha256": ingredient_manifest_sha256,
        "mathlib_commit": manifest.mathlib_commit,
        "recipe_bundle_sha256": manifest.recipe_bundle_sha256,
    }
    if manifest.lemma_corpus_snapshot_sha256 is not None:
        expected_pins["lemma_corpus_snapshot_sha256"] = manifest.lemma_corpus_snapshot_sha256
    for key, expected in expected_pins.items():
        if getattr(receipt, key) != expected:
            raise ValueError(f"ingredient task receipt mismatch: {key}")
    if (
        task.source_ref.name != receipt.selection.selected_recipe_id
        or task.source_ref.commit != receipt.ingredient_repo_commit
    ):
        raise ValueError("ingredient task source mismatch")
    validate_ingredient_statement_header(
        theorem_name=task.theorem_name,
        type_expr=task.type_expr,
        statement=task.statement,
    )
    validate_ingredient_task_public_metadata(task, receipt)
    return receipt


def _fact_rows_from_root(
    root: Path,
    *,
    mathlib_commit: str,
    recipe_ids: set[str] | None = None,
) -> tuple[FactIngredient, ...]:
    if recipe_ids is None:
        recipes = _recipe_rules(
            root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
            shortcut_policy_checks=_shortcut_policy_checks(root),
        )
        recipe_ids = {recipe.recipe_id for recipe in recipes}
    facts = tuple(
        row
        for field in ("facts_sha256", "source_theorems_sha256", "source_lemmas_sha256")
        for row in cast(
            tuple[FactIngredient, ...],
            _jsonl_model_rows(
                root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field],
                field,
                FactIngredient,
                mathlib_commit=mathlib_commit,
                recipe_ids=recipe_ids,
            ),
        )
    )
    _validate_fact_catalog_unique_ids(facts)
    return facts


def _selected_recipe_from_root(root: Path, selection: IngredientSelectionReceipt) -> RecipeRule:
    for recipe in _recipe_rules(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        shortcut_policy_checks=_shortcut_policy_checks(root),
    ):
        if recipe.recipe_id == selection.selected_recipe_id:
            return recipe
    raise ValueError("ingredient statement gate selected recipe missing")


def _normalized_lean_expr(value: str) -> str:
    return " ".join(value.split())


def verify_ingredient_generation_receipt_artifact(
    task: LemmaTask,
    receipt: IngredientGenerationReceipt,
    root: Path,
    *,
    manifest: IngredientManifest,
    ingredient_manifest_sha256: str,
    challenge_seed_sha256: str,
    difficulty_lane: DifficultyLane,
) -> IngredientGenerationReceipt:
    expected = verify_ingredient_task_against_root(
        task,
        root,
        manifest=manifest,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        challenge_seed_sha256=challenge_seed_sha256,
        difficulty_lane=difficulty_lane,
    )
    if receipt != expected:
        raise ValueError("ingredient generation receipt artifact mismatch")
    return receipt


def ingredient_generation_receipt_envelope(
    receipt: IngredientGenerationReceipt,
    *,
    signer_id: str | None = None,
    signature: str | None = None,
) -> IngredientGenerationReceiptEnvelope:
    return IngredientGenerationReceiptEnvelope(
        schema_version=1,
        generation_receipt_sha256=canonical_sha256(receipt),
        generation_receipt=receipt,
        signer_id=signer_id,
        signature=signature,
    )


def verify_ingredient_generation_receipt_envelope(
    task: LemmaTask,
    envelope: IngredientGenerationReceiptEnvelope,
    root: Path,
    *,
    manifest: IngredientManifest,
    ingredient_manifest_sha256: str,
    challenge_seed_sha256: str,
    difficulty_lane: DifficultyLane,
    signature_verifier: IngredientEnvelopeSignatureVerifier | None = None,
) -> IngredientGenerationReceiptEnvelope:
    if envelope.generation_receipt_sha256 != canonical_sha256(envelope.generation_receipt):
        raise ValueError("ingredient generation receipt envelope hash mismatch")
    signer_id = envelope.signer_id
    signature = envelope.signature
    if (signer_id is None) != (signature is None):
        raise ValueError("ingredient generation receipt envelope signature metadata mismatch")
    if signer_id is not None and signature is not None:
        _validate_envelope_metadata_token(signer_id)
        _validate_envelope_metadata_token(signature)
    if signature_verifier is not None:
        if signer_id is None or signature is None:
            raise ValueError("ingredient generation receipt envelope signature missing")
        try:
            accepted = signature_verifier.verify_envelope_signature(
                payload=ingredient_generation_receipt_envelope_signing_payload(envelope),
                signer_id=signer_id,
                signature=signature,
            )
        except Exception as e:
            raise ValueError("ingredient generation receipt envelope signature verification failed") from e
        if not accepted:
            raise ValueError("ingredient generation receipt envelope signature verification failed")
    verify_ingredient_generation_receipt_artifact(
        task,
        envelope.generation_receipt,
        root,
        manifest=manifest,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        challenge_seed_sha256=challenge_seed_sha256,
        difficulty_lane=difficulty_lane,
    )
    return envelope


def verify_ingredient_generation_receipt_envelope_quorum(
    task: LemmaTask,
    envelopes: Sequence[IngredientGenerationReceiptEnvelope],
    root: Path,
    *,
    manifest: IngredientManifest,
    ingredient_manifest_sha256: str,
    challenge_seed_sha256: str,
    difficulty_lane: DifficultyLane,
    quorum: int = 1,
    signature_verifier: IngredientEnvelopeSignatureVerifier | None = None,
) -> tuple[IngredientGenerationReceiptEnvelope, ...]:
    if quorum < 1:
        raise ValueError("ingredient generation receipt envelope quorum malformed")
    if len(envelopes) < quorum:
        raise ValueError("ingredient generation receipt envelope quorum shortfall")
    envelope_hashes = [canonical_sha256(envelope) for envelope in envelopes]
    if len(set(envelope_hashes)) != len(envelope_hashes):
        raise ValueError("ingredient generation receipt envelope duplicate")
    verified = tuple(
        verify_ingredient_generation_receipt_envelope(
            task,
            envelope,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=ingredient_manifest_sha256,
            challenge_seed_sha256=challenge_seed_sha256,
            difficulty_lane=difficulty_lane,
            signature_verifier=signature_verifier,
        )
        for envelope in envelopes
    )
    signer_ids = [envelope.signer_id for envelope in verified if envelope.signer_id is not None]
    if quorum > 1 and len(signer_ids) != len(verified):
        raise ValueError("ingredient generation receipt envelope signer metadata required for quorum")
    if len(set(signer_ids)) != len(signer_ids):
        raise ValueError("ingredient generation receipt envelope duplicate signer")
    return verified


def _validate_envelope_metadata_token(value: str) -> None:
    _validate_public_token(value, "ingredient generation receipt envelope signature metadata invalid")
    _reject_placeholder_public_token(value, "ingredient generation receipt envelope signature metadata placeholder")


def _validate_public_token(value: str, message: str) -> None:
    if not PUBLIC_TOKEN_RE.fullmatch(value):
        raise ValueError(message)


def _reject_placeholder_public_token(value: str, message: str) -> None:
    if set(value) == {"0"} or (value.startswith("0x") and set(value[2:] or "0") == {"0"}):
        raise ValueError(message)


def _write_manifestable_empty_scaffold(root: Path) -> None:
    from lemma.supply.novelty import NOVELTY_CACHE_VERSION

    for field in INGREDIENT_EMPTY_COMPATIBILITY_JSONL_FIELDS:
        _write_if_missing(root, INGREDIENT_MANIFEST_COMPONENT_PATHS[field], b"")
    json_defaults = {
        "recipe_bundle_sha256": {"schema_version": 1, "recipes": []},
        "difficulty_ladder_sha256": {
            "schema_version": 1,
            "difficulty_lanes": list(DIFFICULTY_LANES),
        },
        "difficulty_retarget_sha256": {
            "schema_version": 1,
            "retarget_mode": "manual_state_v1",
            "state_schema": "tempo_lane_v1",
        },
        "novelty_policy_sha256": {
            "schema_version": 1,
            "novelty_cache_version": NOVELTY_CACHE_VERSION,
            "supported_checks": list(INGREDIENT_NOVELTY_CHECK_ORDER),
        },
        "shortcut_policy_sha256": {
            "schema_version": 1,
            "supported_checks": list(INGREDIENT_SHORTCUT_CHECK_ORDER),
        },
        "reserve_selector_policy_sha256": {
            "schema_version": 1,
            "reserve_enabled": True,
            "selection_method": "hash_order_first_eligible",
        },
    }
    for field, payload in json_defaults.items():
        _write_json_if_missing(root, INGREDIENT_MANIFEST_COMPONENT_PATHS[field], payload)
    _write_json_if_missing(root, INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_json_if_missing(root, INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    soundness_template_dir = _ingredient_write_dir_path(root, "recipes/soundness_templates")
    soundness_template_dir.mkdir(parents=True, exist_ok=True)


def _write_json_if_missing(root: Path, relative_path: str, payload: dict[str, object]) -> None:
    _write_if_missing(root, relative_path, canonical_json_bytes(payload) + b"\n")


def _write_if_missing(root: Path, relative_path: str, content: bytes) -> None:
    path = _ingredient_write_file_path(root, relative_path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _mathlib_fact_ingredient(row: MathlibSnapshotRow) -> FactIngredient:
    metadata: dict[str, Any] = {
        "queue_depth": row.queue_depth,
        "usable_as_source_fact": True,
    }
    if row.source_line is not None:
        metadata["source_line"] = row.source_line
    if row.proof_sha256 is not None:
        metadata["proof_sha256"] = row.proof_sha256
    if row.topic is not None:
        metadata["topic"] = row.topic
    if row.subtopic is not None:
        metadata["subtopic"] = row.subtopic
    if row.difficulty_score is not None:
        metadata["difficulty_score"] = row.difficulty_score
    if row.direct_dependency_count is not None:
        metadata["direct_dependency_count"] = row.direct_dependency_count
    if row.dependency_depth is not None:
        metadata["dependency_depth"] = row.dependency_depth
    return FactIngredient(
        fact_id=row.theorem_name,
        lean_name=row.theorem_name,
        kind="theorem",
        domain=_mathlib_ingredient_domain(row),
        type_expr=row.type_expr,
        imports=row.imports,
        source_path=row.source_path,
        mathlib_commit=row.mathlib_rev,
        difficulty_hint=row.queue_depth,
        metadata=metadata,
    )


def _mathlib_definition_ingredient(row: MathlibDefinitionLike) -> DefinitionIngredient:
    return DefinitionIngredient(
        definition_id=row.definition_name,
        lean_name=row.definition_name,
        domain=_mathlib_ingredient_domain(row),
        type_signature=row.type_signature,
        imports=row.imports,
        source_path=row.source_path,
        mathlib_commit=row.mathlib_rev,
        metadata={"simp_risk": _definition_simp_risk(row.queue_depth)},
    )


def _mathlib_ingredient_domain(row: MathlibSnapshotRow | MathlibDefinitionLike) -> str:
    subtopic = getattr(row, "subtopic", None)
    if row.topic == "Data" and isinstance(subtopic, str) and INGREDIENT_LABEL_RE.fullmatch(subtopic):
        return subtopic
    return row.topic or "Mathlib"


def _definition_simp_risk(queue_depth: int) -> str:
    if queue_depth <= 1:
        return "low"
    if queue_depth <= 3:
        return "medium"
    return "high"


def _ingredient_write_file_path(root: Path, relative_path: str) -> Path:
    path = _ingredient_write_path(root, relative_path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("ingredient root artifact path invalid")
    return path


def _ingredient_write_dir_path(root: Path, relative_path: str) -> Path:
    path = _ingredient_write_path(root, relative_path)
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("ingredient root artifact path invalid")
    return path


def _ingredient_write_path(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("ingredient root artifact path invalid")
    parent = root
    for part in relative.parts[:-1]:
        parent /= part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError("ingredient root artifact path invalid")
    return root / relative


def _write_ingredient_jsonl(root: Path, relative_path: str, rows: Iterable[BaseModel]) -> None:
    path = _ingredient_write_file_path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))


def _write_json(root: Path, relative_path: str, payload: dict[str, object]) -> None:
    path = _ingredient_write_file_path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def _sync_quality_report_counts(root: Path, component_schema_counts: dict[str, int]) -> None:
    path = _ingredient_write_file_path(root, INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    counts = _ingredient_report_counts(root, component_schema_counts)
    payload.update(counts)
    payload.setdefault("difficulty_lane_coverage", {})
    payload.setdefault("bridge_coverage", {})
    payload.setdefault("estimated_theorem_space_size", 0)
    payload.setdefault("shortcut_risk_distribution", {})
    payload.setdefault("reserve_selector_health", {"ready": False})
    _write_json(root, INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"], payload)


def _difficulty_lane_for_depth(queue_depth: int) -> str:
    if queue_depth <= 1:
        return "easy"
    if queue_depth <= 3:
        return "medium"
    if queue_depth <= 6:
        return "hard"
    return "frontier"


def ingredient_manifest_component_schema_counts(root: Path, *, mathlib_commit: str | None = None) -> dict[str, int]:
    _require_ingredient_root(root)
    counts: dict[str, int] = {}
    _difficulty_ladder_lanes(root)
    _difficulty_retarget_policy(root)
    _novelty_policy_checks(root)
    _reserve_selector_policy(root)
    shortcut_policy_checks = _shortcut_policy_checks(root)
    recipes = _recipe_rules(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        shortcut_policy_checks=shortcut_policy_checks,
    )
    _recipe_bundle_recipe_ids(root, recipes)
    recipe_ids = {recipe.recipe_id for recipe in recipes}
    component_rows: dict[str, tuple[BaseModel, ...]] = {}
    for field, model in INGREDIENT_JSONL_COMPONENT_MODELS.items():
        rows = _jsonl_model_rows(
            root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field],
            field,
            model,
            mathlib_commit=mathlib_commit,
            recipe_ids=recipe_ids,
        )
        component_rows[field] = rows
        counts[field] = len(rows)
    for field in sorted(INGREDIENT_JSON_COMPONENT_FIELDS - {"recipe_bundle_sha256"}):
        _canonical_json_object(
            root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field],
            invalid=f"ingredient component invalid: {field}",
            noncanonical=f"ingredient component noncanonical: {field}",
        )
        counts[field] = 1
    counts["recipe_bundle_sha256"] = 1
    _validate_component_references(component_rows, recipes)
    return counts


def _jsonl_model_rows(
    path: Path,
    field: str,
    model: type[BaseModel],
    *,
    mathlib_commit: str | None,
    recipe_ids: set[str],
) -> tuple[BaseModel, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"ingredient component path invalid: {field}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as e:
        raise ValueError(f"ingredient component invalid: {field}") from e
    rows = []
    seen_ids: set[str] = set()
    previous_id: str | None = None
    id_field = INGREDIENT_JSONL_COMPONENT_ID_FIELDS[field]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ValueError(f"ingredient component invalid: {field}:{line_number}")
        try:
            payload = json.loads(line)
            row = model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as e:
            raise ValueError(f"ingredient component invalid: {field}:{line_number}") from e
        if line.encode("utf-8") != canonical_json_bytes(row):
            raise ValueError(f"ingredient component noncanonical: {field}:{line_number}")
        if mathlib_commit is not None and getattr(row, "mathlib_commit", mathlib_commit) != mathlib_commit:
            raise ValueError(f"ingredient component mathlib commit mismatch: {field}:{line_number}")
        for recipe_id in _component_recipe_id_values(row):
            _validate_public_label(
                recipe_id,
                f"ingredient component recipe reference invalid: {field}:{line_number}",
            )
        row_recipe_ids = _component_recipe_ids(row)
        if row_recipe_ids and not row_recipe_ids.issubset(recipe_ids):
            raise ValueError(f"ingredient component recipe reference missing: {field}:{line_number}")
        if isinstance(row, DefinitionIngredient):
            _validate_definition_ingredient(row, field=field, line_number=line_number, recipe_ids=recipe_ids)
        if isinstance(row, FactIngredient):
            _validate_fact_ingredient(row, field=field, line_number=line_number)
        if isinstance(row, RecipeSelector):
            _validate_recipe_selector(row, field=field, line_number=line_number)
        if isinstance(row, BridgeRule):
            _validate_bridge_rule(row, field=field, line_number=line_number)
        if isinstance(row, CompatibilityEdge):
            _validate_compatibility_edge(row, field=field, line_number=line_number)
        row_id = getattr(row, id_field)
        if row_id in seen_ids:
            raise ValueError(f"ingredient component id duplicate: {field}:{line_number}")
        if previous_id is not None and row_id < previous_id:
            raise ValueError(f"ingredient component id order invalid: {field}:{line_number}")
        seen_ids.add(row_id)
        previous_id = row_id
        rows.append(row)
    return tuple(rows)


def _difficulty_ladder_lanes(root: Path) -> tuple[str, ...]:
    payload, _raw = _canonical_json_object(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_ladder_sha256"],
        invalid="ingredient difficulty ladder invalid",
        noncanonical="ingredient difficulty ladder noncanonical",
    )
    _require_schema_version_1(payload, "ingredient difficulty ladder invalid")
    if "difficulty_lanes" not in payload:
        raise ValueError("ingredient difficulty ladder missing: difficulty_lanes")
    if set(payload) != INGREDIENT_DIFFICULTY_LADDER_KEYS:
        raise ValueError("ingredient difficulty ladder invalid")
    lanes = payload.get("difficulty_lanes")
    if not isinstance(lanes, list):
        raise ValueError("ingredient difficulty ladder invalid: difficulty_lanes")
    if not all(isinstance(lane, str) for lane in lanes):
        raise ValueError("ingredient difficulty ladder invalid: difficulty_lanes")
    duplicate = _first_duplicate(lanes)
    if duplicate is not None:
        raise ValueError(f"ingredient difficulty ladder duplicate: {duplicate}")
    unsupported = sorted(set(lanes) - set(DIFFICULTY_LANES))
    if unsupported:
        raise ValueError(f"ingredient difficulty ladder unsupported: {unsupported[0]}")
    missing = [lane for lane in DIFFICULTY_LANES if lane not in lanes]
    if missing:
        raise ValueError(f"ingredient difficulty ladder missing lane: {missing[0]}")
    if tuple(lanes) != DIFFICULTY_LANES:
        raise ValueError("ingredient difficulty ladder order invalid")
    return tuple(lanes)


def _recipe_bundle_recipe_ids(root: Path, recipes: Sequence[RecipeRule]) -> tuple[str, ...]:
    payload, _raw = _canonical_json_object(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        invalid="ingredient recipe bundle invalid",
        noncanonical="ingredient recipe bundle noncanonical",
    )
    _require_schema_version_1(payload, "ingredient recipe bundle invalid")
    if "recipes" not in payload:
        raise ValueError("ingredient recipe bundle missing: recipes")
    if set(payload) != INGREDIENT_RECIPE_BUNDLE_KEYS:
        raise ValueError("ingredient recipe bundle invalid")
    bundle_recipes = payload.get("recipes")
    if not isinstance(bundle_recipes, list):
        raise ValueError("ingredient recipe bundle invalid: recipes")
    if not all(isinstance(recipe_id, str) for recipe_id in bundle_recipes):
        raise ValueError("ingredient recipe bundle invalid: recipes")
    for recipe_id in bundle_recipes:
        _validate_public_label(recipe_id, "ingredient recipe bundle recipe invalid")
    duplicate = _first_duplicate(bundle_recipes)
    if duplicate is not None:
        raise ValueError(f"ingredient recipe bundle duplicate: {duplicate}")
    recipe_ids = tuple(recipe.recipe_id for recipe in recipes)
    unknown = [recipe_id for recipe_id in bundle_recipes if recipe_id not in recipe_ids]
    if unknown:
        raise ValueError(f"ingredient recipe bundle unknown recipe: {unknown[0]}")
    missing = [recipe_id for recipe_id in recipe_ids if recipe_id not in bundle_recipes]
    if missing:
        raise ValueError(f"ingredient recipe bundle missing recipe: {missing[0]}")
    if tuple(bundle_recipes) != recipe_ids:
        raise ValueError("ingredient recipe bundle order invalid")
    return tuple(bundle_recipes)


def _difficulty_retarget_policy(root: Path) -> str:
    payload, _raw = _canonical_json_object(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_retarget_sha256"],
        invalid="ingredient difficulty retarget policy invalid",
        noncanonical="ingredient difficulty retarget policy noncanonical",
    )
    _require_schema_version_1(payload, "ingredient difficulty retarget policy invalid")
    if "retarget_mode" not in payload:
        raise ValueError("ingredient difficulty retarget policy missing: retarget_mode")
    if "state_schema" not in payload:
        raise ValueError("ingredient difficulty retarget policy missing: state_schema")
    if set(payload) != INGREDIENT_DIFFICULTY_RETARGET_POLICY_KEYS:
        raise ValueError("ingredient difficulty retarget policy invalid")
    if payload.get("retarget_mode") != "manual_state_v1":
        raise ValueError("ingredient difficulty retarget policy mode unsupported")
    if payload.get("state_schema") != "tempo_lane_v1":
        raise ValueError("ingredient difficulty retarget policy state schema unsupported")
    return "manual_state_v1"


def _shortcut_policy_checks(root: Path) -> tuple[str, ...]:
    payload, _raw = _canonical_json_object(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        invalid="ingredient shortcut policy invalid",
        noncanonical="ingredient shortcut policy noncanonical",
    )
    _require_schema_version_1(payload, "ingredient shortcut policy invalid")
    if "supported_checks" not in payload:
        raise ValueError("ingredient shortcut policy missing: supported_checks")
    if set(payload) != INGREDIENT_SHORTCUT_POLICY_KEYS:
        raise ValueError("ingredient shortcut policy invalid")
    checks = payload.get("supported_checks")
    if not isinstance(checks, list):
        raise ValueError("ingredient shortcut policy invalid: supported_checks")
    if not all(isinstance(check, str) for check in checks):
        raise ValueError("ingredient shortcut policy invalid: supported_checks")
    duplicate = _first_duplicate(checks)
    if duplicate is not None:
        raise ValueError(f"ingredient shortcut policy duplicate: {duplicate}")
    unsupported = sorted(set(checks) - SUPPORTED_INGREDIENT_SHORTCUT_CHECKS)
    if unsupported:
        raise ValueError(f"ingredient shortcut policy unsupported: {unsupported[0]}")
    if tuple(checks) != _canonical_shortcut_checks(checks):
        raise ValueError("ingredient shortcut policy order invalid")
    return tuple(checks)


def _novelty_policy_checks(root: Path) -> tuple[str, ...]:
    from lemma.supply.novelty import NOVELTY_CACHE_VERSION

    payload, _raw = _canonical_json_object(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"],
        invalid="ingredient novelty policy invalid",
        noncanonical="ingredient novelty policy noncanonical",
    )
    _require_schema_version_1(payload, "ingredient novelty policy invalid")
    if "supported_checks" not in payload:
        raise ValueError("ingredient novelty policy missing: supported_checks")
    if set(payload) != INGREDIENT_NOVELTY_POLICY_KEYS:
        raise ValueError("ingredient novelty policy invalid")
    if payload.get("novelty_cache_version") != NOVELTY_CACHE_VERSION:
        raise ValueError("ingredient novelty policy cache version invalid")
    checks = payload.get("supported_checks")
    if not isinstance(checks, list):
        raise ValueError("ingredient novelty policy invalid: supported_checks")
    if not all(isinstance(check, str) for check in checks):
        raise ValueError("ingredient novelty policy invalid: supported_checks")
    duplicate = _first_duplicate(checks)
    if duplicate is not None:
        raise ValueError(f"ingredient novelty policy duplicate: {duplicate}")
    unsupported = sorted(set(checks) - SUPPORTED_INGREDIENT_NOVELTY_CHECKS)
    if unsupported:
        raise ValueError(f"ingredient novelty policy unsupported: {unsupported[0]}")
    if tuple(checks) != _canonical_novelty_checks(checks):
        raise ValueError("ingredient novelty policy order invalid")
    return tuple(checks)


def _reserve_selector_policy(root: Path) -> str:
    payload, _raw = _canonical_json_object(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["reserve_selector_policy_sha256"],
        invalid="ingredient reserve selector policy invalid",
        noncanonical="ingredient reserve selector policy noncanonical",
    )
    _require_schema_version_1(payload, "ingredient reserve selector policy invalid")
    if "reserve_enabled" not in payload:
        raise ValueError("ingredient reserve selector policy missing: reserve_enabled")
    if "selection_method" not in payload:
        raise ValueError("ingredient reserve selector policy missing: selection_method")
    if set(payload) != INGREDIENT_RESERVE_SELECTOR_POLICY_KEYS:
        raise ValueError("ingredient reserve selector policy invalid")
    if payload.get("reserve_enabled") is not True:
        raise ValueError("ingredient reserve selector policy disabled")
    if payload.get("selection_method") != "hash_order_first_eligible":
        raise ValueError("ingredient reserve selector policy selection method unsupported")
    return "hash_order_first_eligible"


def _validate_novelty_gate_details(root: Path, details: dict[str, Any]) -> None:
    from lemma.supply.novelty import NOVELTY_CACHE_VERSION

    policy_checks = _novelty_policy_checks(root)
    if "theorem_type_cache" not in policy_checks:
        raise ValueError("ingredient novelty policy check unavailable: theorem_type_cache")
    required = {
        "novelty_cache_entries",
        "novelty_cache_sha256",
        "novelty_cache_version",
        "novelty_policy_check",
        "novelty_statement_hash",
    }
    optional = {"novelty_family_cache_entries", "novelty_family_hash"}
    if set(details) != required and set(details) != required | optional:
        raise ValueError("ingredient statement gate novelty details invalid")
    novelty_cache_entries = details.get("novelty_cache_entries")
    if (
        details.get("novelty_policy_check") != "theorem_type_cache"
        or details.get("novelty_cache_version") != NOVELTY_CACHE_VERSION
        or type(novelty_cache_entries) is not int
        or novelty_cache_entries < 0
    ):
        raise ValueError("ingredient statement gate novelty details invalid")
    if "novelty_family_hash" in details:
        if "selection_family_cache" not in policy_checks:
            raise ValueError("ingredient novelty policy check unavailable: selection_family_cache")
        novelty_family_cache_entries = details.get("novelty_family_cache_entries")
        if (
            type(novelty_family_cache_entries) is not int
            or novelty_family_cache_entries < 0
        ):
            raise ValueError("ingredient statement gate novelty details invalid")
        _validate_non_placeholder_sha256(
            str(details.get("novelty_family_hash", "")),
            "ingredient novelty family hash",
        )
    _validate_non_placeholder_sha256(
        str(details.get("novelty_cache_sha256", "")),
        "ingredient novelty cache sha256",
    )
    _validate_non_placeholder_sha256(
        str(details.get("novelty_statement_hash", "")),
        "ingredient novelty statement hash",
    )


def _validate_triviality_gate_details(details: dict[str, Any]) -> None:
    if set(details) != {
        "baseline_solved",
        "runner",
        "triviality_budget_heartbeats",
        "triviality_probe_sha256",
        "triviality_reason",
        "triviality_stack",
        "verify_reason",
    }:
        raise ValueError("ingredient statement gate triviality details invalid")
    try:
        budget = _exact_public_int(details.get("triviality_budget_heartbeats"))
    except ValueError as e:
        raise ValueError("ingredient statement gate triviality details invalid") from e
    if (
        details.get("runner") != INGREDIENT_TRIVIALITY_GATE_RUNNER
        or details.get("baseline_solved") is not False
        or details.get("triviality_reason") != "baseline_failed"
        or details.get("verify_reason") != "compile_error"
        or budget < 1
        or details.get("triviality_stack") != [name for name, _body in _ingredient_triviality_stack()]
    ):
        raise ValueError("ingredient statement gate triviality details invalid")
    _validate_non_placeholder_sha256(
        str(details.get("triviality_probe_sha256", "")),
        "ingredient triviality probe sha256",
    )


def _validate_shortcut_tactic_gate_details(
    details: dict[str, Any],
    *,
    theorem_name: str,
    theorem_type_expr: str,
    imports: Sequence[str],
    expected_tactics: Sequence[str],
) -> None:
    if set(details) != {
        "runner",
        "shortcut_tactic_budget_heartbeats",
        "shortcut_tactic_probe_sha256",
        "shortcut_tactic_reason",
        "shortcut_tactic_solved",
        "shortcut_tactics",
        "verify_reason",
    }:
        raise ValueError("ingredient shortcut tactic details invalid")
    try:
        budget = _exact_public_int(details.get("shortcut_tactic_budget_heartbeats"))
    except ValueError as e:
        raise ValueError("ingredient shortcut tactic details invalid") from e
    try:
        expected = ingredient_shortcut_tactic_gate_details(
            theorem_name=theorem_name,
            theorem_type_expr=theorem_type_expr,
            imports=imports,
            tactics=expected_tactics,
            verify_reason=str(details.get("verify_reason", "")),
            max_heartbeats=budget,
        )
    except ValueError as e:
        raise ValueError("ingredient shortcut tactic details invalid") from e
    if details != expected:
        raise ValueError("ingredient shortcut tactic details invalid")
    _validate_non_placeholder_sha256(
        str(details.get("shortcut_tactic_probe_sha256", "")),
        "ingredient shortcut tactic probe sha256",
    )


def _first_duplicate(values: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _validate_fact_catalog_unique_ids(facts: Iterable[FactIngredient]) -> None:
    seen: set[str] = set()
    for fact in facts:
        if fact.fact_id in seen:
            raise ValueError(f"ingredient fact catalog id duplicate: {fact.fact_id}")
        seen.add(fact.fact_id)


def _recipe_rules(
    path: Path,
    *,
    shortcut_policy_checks: Iterable[str] | None = None,
) -> tuple[RecipeRule, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("ingredient recipe artifact path invalid: recipe_rules")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("ingredient recipe artifact invalid: recipe_rules") from e
    return _recipe_rules_from_payload(payload, shortcut_policy_checks=shortcut_policy_checks)


def _recipe_rules_from_payload(
    payload: object,
    *,
    shortcut_policy_checks: Iterable[str] | None = None,
) -> tuple[RecipeRule, ...]:
    if not isinstance(payload, dict) or set(payload) != {"recipes"} or not isinstance(payload["recipes"], list):
        raise ValueError("ingredient recipe artifact invalid: recipe_rules")
    recipes = []
    recipe_ids: set[str] = set()
    policy_checks = (
        SUPPORTED_INGREDIENT_SHORTCUT_CHECKS
        if shortcut_policy_checks is None
        else frozenset(shortcut_policy_checks)
    )
    try:
        for recipe in payload["recipes"]:
            row = RecipeRule.model_validate(recipe)
            _validate_public_label(row.recipe_id, "ingredient recipe id invalid: recipe_rules")
            if row.recipe_id in recipe_ids:
                raise ValueError(f"ingredient recipe id duplicate: recipe_rules:{row.recipe_id}")
            _validate_recipe_version(row)
            _validate_recipe_selection_fields(row)
            _validate_recipe_preconditions(row)
            _validate_recipe_difficulty_delta(row)
            _validate_recipe_ingredient_classes(row)
            _validate_recipe_parameter_rule(row)
            _validate_recipe_soundness_template(row)
            _validate_recipe_shortcut_checks(row, policy_checks=policy_checks)
            recipe_ids.add(row.recipe_id)
            recipes.append(row)
    except ValidationError as e:
        raise ValueError("ingredient recipe artifact invalid: recipe_rules") from e
    if tuple(recipe.recipe_id for recipe in recipes) != tuple(sorted(recipe.recipe_id for recipe in recipes)):
        raise ValueError("ingredient recipe order invalid: recipe_rules")
    return tuple(recipes)


def _validate_recipe_version(recipe: RecipeRule) -> int:
    if recipe.version != 1:
        raise ValueError(f"ingredient recipe version unsupported: recipe_rules:{recipe.recipe_id}:{recipe.version}")
    return recipe.version


def _validate_recipe_selection_fields(recipe: RecipeRule) -> None:
    if not recipe.domains:
        raise ValueError(f"ingredient recipe domains missing: recipe_rules:{recipe.recipe_id}")
    for domain in recipe.domains:
        _validate_domain_label(domain, f"ingredient recipe domain invalid: recipe_rules:{recipe.recipe_id}")
    duplicate_domain = _first_duplicate(recipe.domains)
    if duplicate_domain is not None:
        raise ValueError(f"ingredient recipe domain duplicate: recipe_rules:{recipe.recipe_id}:{duplicate_domain}")
    if recipe.domains != tuple(sorted(recipe.domains)):
        raise ValueError(f"ingredient recipe domain order invalid: recipe_rules:{recipe.recipe_id}")
    if not recipe.required_definitions:
        raise ValueError(f"ingredient recipe definitions missing: recipe_rules:{recipe.recipe_id}")
    duplicate_definition = _first_duplicate(recipe.required_definitions)
    if duplicate_definition is not None:
        raise ValueError(
            f"ingredient recipe definition duplicate: recipe_rules:{recipe.recipe_id}:{duplicate_definition}"
        )
    if recipe.required_definitions != tuple(sorted(recipe.required_definitions)):
        raise ValueError(f"ingredient recipe definition order invalid: recipe_rules:{recipe.recipe_id}")
    if not recipe.required_fact_kinds:
        raise ValueError(f"ingredient recipe fact kinds missing: recipe_rules:{recipe.recipe_id}")
    duplicate_fact_kind = _first_duplicate(recipe.required_fact_kinds)
    if duplicate_fact_kind is not None:
        raise ValueError(
            f"ingredient recipe fact kind duplicate: recipe_rules:{recipe.recipe_id}:{duplicate_fact_kind}"
        )
    if recipe.required_fact_kinds != tuple(sorted(recipe.required_fact_kinds)):
        raise ValueError(f"ingredient recipe fact kind order invalid: recipe_rules:{recipe.recipe_id}")


def _validate_recipe_preconditions(recipe: RecipeRule) -> tuple[str, ...]:
    if recipe.preconditions:
        raise ValueError(
            f"ingredient recipe precondition unsupported: recipe_rules:{recipe.recipe_id}:{recipe.preconditions[0]}"
        )
    return recipe.preconditions


def _validate_recipe_difficulty_delta(recipe: RecipeRule) -> int:
    if recipe.difficulty_delta != 0:
        raise ValueError(
            f"ingredient recipe difficulty delta unsupported: recipe_rules:{recipe.recipe_id}:{recipe.difficulty_delta}"
        )
    return recipe.difficulty_delta


def _validate_recipe_ingredient_classes(recipe: RecipeRule) -> tuple[str, ...]:
    if not recipe.required_ingredient_classes:
        raise ValueError(f"ingredient recipe ingredient classes missing: recipe_rules:{recipe.recipe_id}")
    for ingredient_class in recipe.required_ingredient_classes:
        _validate_public_label(
            ingredient_class,
            f"ingredient recipe ingredient class invalid: recipe_rules:{recipe.recipe_id}",
        )
    duplicate = _first_duplicate(recipe.required_ingredient_classes)
    if duplicate is not None:
        raise ValueError(
            f"ingredient recipe ingredient class duplicate: recipe_rules:{recipe.recipe_id}:{duplicate}"
        )
    if recipe.required_ingredient_classes != tuple(sorted(recipe.required_ingredient_classes)):
        raise ValueError(f"ingredient recipe ingredient class order invalid: recipe_rules:{recipe.recipe_id}")
    return recipe.required_ingredient_classes


def _validate_recipe_parameter_rule(recipe: RecipeRule) -> str:
    if recipe.parameter_rule not in SUPPORTED_INGREDIENT_PARAMETER_RULES:
        raise ValueError(
            f"ingredient recipe parameter rule unsupported: recipe_rules:{recipe.recipe_id}:{recipe.parameter_rule}"
        )
    return recipe.parameter_rule


def _validate_recipe_soundness_template(recipe: RecipeRule) -> str:
    prefix = "soundness_templates/"
    suffix = ".lean"
    if (
        not recipe.soundness_template.startswith(prefix)
        or not recipe.soundness_template.endswith(suffix)
        or "\\" in recipe.soundness_template
        or "://" in recipe.soundness_template
    ):
        raise ValueError(f"ingredient recipe soundness template invalid: recipe_rules:{recipe.recipe_id}")
    filename = recipe.soundness_template.removeprefix(prefix)
    stem = filename.removesuffix(suffix)
    if "/" in filename or not INGREDIENT_LABEL_RE.fullmatch(stem):
        raise ValueError(f"ingredient recipe soundness template invalid: recipe_rules:{recipe.recipe_id}")
    return recipe.soundness_template


def _validate_recipe_shortcut_checks(
    recipe: RecipeRule,
    *,
    policy_checks: Iterable[str] = SUPPORTED_INGREDIENT_SHORTCUT_CHECKS,
) -> tuple[str, ...]:
    if not recipe.shortcut_checks:
        raise ValueError(f"ingredient recipe shortcut checks missing: recipe_rules:{recipe.recipe_id}")
    duplicate = _first_duplicate(recipe.shortcut_checks)
    if duplicate is not None:
        raise ValueError(f"ingredient recipe shortcut check duplicate: recipe_rules:{recipe.recipe_id}:{duplicate}")
    unsupported = sorted(set(recipe.shortcut_checks) - SUPPORTED_INGREDIENT_SHORTCUT_CHECKS)
    if unsupported:
        raise ValueError(
            f"ingredient recipe shortcut check unsupported: recipe_rules:{recipe.recipe_id}:{unsupported[0]}"
        )
    if "source_oracle" not in recipe.shortcut_checks:
        raise ValueError(f"ingredient recipe shortcut check missing: recipe_rules:{recipe.recipe_id}:source_oracle")
    policy_missing = sorted(set(recipe.shortcut_checks) - set(policy_checks))
    if policy_missing:
        raise ValueError(
            f"ingredient recipe shortcut check not in policy: recipe_rules:{recipe.recipe_id}:{policy_missing[0]}"
        )
    if recipe.shortcut_checks != _canonical_shortcut_checks(recipe.shortcut_checks):
        raise ValueError(f"ingredient recipe shortcut check order invalid: recipe_rules:{recipe.recipe_id}")
    return recipe.shortcut_checks


def _validate_definition_ingredient(
    definition: DefinitionIngredient,
    *,
    field: str,
    line_number: int,
    recipe_ids: set[str],
) -> None:
    _validate_lean_identity(
        definition.definition_id,
        definition.lean_name,
        label="definition",
        field=field,
        line_number=line_number,
    )
    _validate_domain_label(definition.domain, f"ingredient definition domain invalid: {field}:{line_number}")
    _validate_mathlib_imports(
        definition.imports,
        label="definition",
        field=field,
        line_number=line_number,
    )
    _validate_mathlib_source_path(
        definition.source_path,
        label="definition",
        field=field,
        line_number=line_number,
    )
    unsupported_metadata = sorted(set(definition.metadata) - INGREDIENT_DEFINITION_METADATA_KEYS)
    if unsupported_metadata:
        raise ValueError(
            f"ingredient definition metadata unsupported: {field}:{line_number}:{unsupported_metadata[0]}"
        )
    simp_risk = definition.metadata.get("simp_risk")
    if simp_risk is not None:
        if not isinstance(simp_risk, str) or simp_risk not in SUPPORTED_INGREDIENT_SIMP_RISKS:
            raise ValueError(f"ingredient definition metadata invalid: {field}:{line_number}:simp_risk")
    allowed_recipes = definition.metadata.get("allowed_recipes")
    if allowed_recipes is not None:
        _validate_string_list_metadata(
            allowed_recipes,
            field=field,
            line_number=line_number,
            label="definition metadata",
            key="allowed_recipes",
        )
        allowed_recipe_list = cast(list[str], allowed_recipes)
        for recipe_id in allowed_recipe_list:
            _validate_public_label(
                recipe_id,
                f"ingredient definition metadata invalid: {field}:{line_number}:allowed_recipes",
            )
        if allowed_recipe_list != sorted(allowed_recipe_list):
            raise ValueError(f"ingredient definition metadata order invalid: {field}:{line_number}:allowed_recipes")
        missing = sorted(set(allowed_recipe_list) - recipe_ids)
        if missing:
            raise ValueError(f"ingredient definition metadata recipe missing: {field}:{line_number}:{missing[0]}")


def _validate_fact_ingredient(fact: FactIngredient, *, field: str, line_number: int) -> None:
    _validate_lean_identity(
        fact.fact_id,
        fact.lean_name,
        label="fact",
        field=field,
        line_number=line_number,
    )
    _validate_domain_label(fact.domain, f"ingredient fact domain invalid: {field}:{line_number}")
    _validate_mathlib_imports(
        fact.imports,
        label="fact",
        field=field,
        line_number=line_number,
    )
    _validate_mathlib_source_path(
        fact.source_path,
        label="fact",
        field=field,
        line_number=line_number,
    )
    unsupported_metadata = sorted(set(fact.metadata) - INGREDIENT_FACT_METADATA_KEYS)
    if unsupported_metadata:
        raise ValueError(f"ingredient fact metadata unsupported: {field}:{line_number}:{unsupported_metadata[0]}")
    for key in ("statement_family", "topic", "subtopic"):
        value = fact.metadata.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"ingredient fact metadata invalid: {field}:{line_number}:{key}")
            _validate_public_label(value, f"ingredient fact metadata invalid: {field}:{line_number}:{key}")
    usable_as_source_fact = fact.metadata.get("usable_as_source_fact")
    if usable_as_source_fact is not None and not isinstance(usable_as_source_fact, bool):
        raise ValueError(f"ingredient fact metadata invalid: {field}:{line_number}:usable_as_source_fact")
    for key in ("difficulty_score", "direct_dependency_count", "dependency_depth", "queue_depth"):
        _validate_nonnegative_int_metadata(fact.metadata.get(key), field=field, line_number=line_number, key=key)
    source_line = fact.metadata.get("source_line")
    if source_line is not None and (
        not isinstance(source_line, int) or isinstance(source_line, bool) or source_line < 1
    ):
        raise ValueError(f"ingredient fact metadata invalid: {field}:{line_number}:source_line")
    proof_sha256 = fact.metadata.get("proof_sha256")
    if proof_sha256 is not None:
        if (
            not isinstance(proof_sha256, str)
            or len(proof_sha256) != 64
            or any(char not in "0123456789abcdef" for char in proof_sha256)
            or proof_sha256 == "0" * 64
        ):
            raise ValueError(f"ingredient fact metadata invalid: {field}:{line_number}:proof_sha256")


def _validate_lean_identity(
    row_id: str,
    lean_name: str,
    *,
    label: str,
    field: str,
    line_number: int,
) -> None:
    if not LEAN_MODULE_RE.fullmatch(row_id):
        raise ValueError(f"ingredient {label} id invalid: {field}:{line_number}:{row_id}")
    if not LEAN_MODULE_RE.fullmatch(lean_name):
        raise ValueError(f"ingredient {label} lean name invalid: {field}:{line_number}:{lean_name}")
    if row_id != lean_name:
        raise ValueError(f"ingredient {label} lean name mismatch: {field}:{line_number}")


def _validate_domain_label(domain: str, message: str) -> None:
    _validate_public_label(domain, message)


def _validate_public_label(value: str, message: str) -> None:
    if not INGREDIENT_LABEL_RE.fullmatch(value):
        raise ValueError(f"{message}:{value}")


def validate_ingredient_active_task_id(active_task_id: str) -> None:
    _validate_public_label(active_task_id, "ingredient active task id invalid")
    if not active_task_id.startswith(INGREDIENT_ACTIVE_TASK_ID_PREFIX):
        raise ValueError("ingredient active task id namespace invalid")


def _validate_mathlib_imports(
    imports: tuple[str, ...],
    *,
    label: str,
    field: str,
    line_number: int,
) -> None:
    if not imports:
        raise ValueError(f"ingredient {label} imports missing: {field}:{line_number}")
    duplicate = _first_duplicate(imports)
    if duplicate is not None:
        raise ValueError(f"ingredient {label} import duplicate: {field}:{line_number}:{duplicate}")
    if imports != tuple(sorted(imports)):
        raise ValueError(f"ingredient {label} import order invalid: {field}:{line_number}")
    for module in imports:
        if (
            not module
            or module.strip() != module
            or not LEAN_MODULE_RE.fullmatch(module)
            or (module != "Mathlib" and not module.startswith("Mathlib."))
        ):
            raise ValueError(f"ingredient {label} import invalid: {field}:{line_number}:{module}")


def _validate_mathlib_source_path(source_path: str, *, label: str, field: str, line_number: int) -> None:
    if (
        not source_path
        or "\\" in source_path
        or "://" in source_path
        or source_path.startswith("/")
        or not source_path.startswith("Mathlib/")
        or not source_path.endswith(".lean")
    ):
        raise ValueError(f"ingredient {label} source path invalid: {field}:{line_number}")
    if any(part in ("", ".", "..") for part in source_path.split("/")):
        raise ValueError(f"ingredient {label} source path invalid: {field}:{line_number}")


def _validate_string_list_metadata(
    value: object,
    *,
    field: str,
    line_number: int,
    label: str,
    key: str,
) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"ingredient {label} invalid: {field}:{line_number}:{key}")
    duplicate = _first_duplicate(value)
    if duplicate is not None:
        raise ValueError(f"ingredient {label} duplicate: {field}:{line_number}:{key}:{duplicate}")


def _validate_nonnegative_int_metadata(value: object, *, field: str, line_number: int, key: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValueError(f"ingredient fact metadata invalid: {field}:{line_number}:{key}")


def _validate_recipe_selector(selector: RecipeSelector, *, field: str, line_number: int) -> None:
    _validate_public_label(selector.selector_id, f"ingredient selector id invalid: {field}:{line_number}")
    if not selector.recipe_ids:
        raise ValueError(f"ingredient selector recipe ids missing: {field}:{line_number}")
    duplicate_recipe = _first_duplicate(selector.recipe_ids)
    if duplicate_recipe is not None:
        raise ValueError(f"ingredient selector recipe id duplicate: {field}:{line_number}:{duplicate_recipe}")
    if selector.recipe_ids != tuple(sorted(selector.recipe_ids)):
        raise ValueError(f"ingredient selector recipe id order invalid: {field}:{line_number}")
    unsupported_filters = sorted(set(selector.ingredient_filters) - SUPPORTED_INGREDIENT_SELECTOR_FILTERS)
    if unsupported_filters:
        raise ValueError(f"ingredient selector filter unsupported: {field}:{line_number}:{unsupported_filters[0]}")
    domains = selector.ingredient_filters.get("domains")
    if domains is not None:
        if not isinstance(domains, list) or not domains or not all(isinstance(domain, str) for domain in domains):
            raise ValueError(f"ingredient selector filter invalid: {field}:{line_number}:domains")
        for domain in domains:
            _validate_domain_label(
                domain,
                f"ingredient selector filter domain invalid: {field}:{line_number}:domains",
            )
        duplicate_domain = _first_duplicate(domains)
        if duplicate_domain is not None:
            raise ValueError(f"ingredient selector filter duplicate: {field}:{line_number}:domains:{duplicate_domain}")
        if domains != sorted(domains):
            raise ValueError(f"ingredient selector filter order invalid: {field}:{line_number}:domains")
    max_simp_risk = selector.ingredient_filters.get("max_simp_risk")
    if max_simp_risk is not None and (
        not isinstance(max_simp_risk, str) or max_simp_risk not in SUPPORTED_INGREDIENT_SIMP_RISKS
    ):
        raise ValueError(f"ingredient selector filter invalid: {field}:{line_number}:max_simp_risk")
    min_dependency_depth = selector.ingredient_filters.get("min_dependency_depth")
    if min_dependency_depth is not None and (
        not isinstance(min_dependency_depth, int)
        or isinstance(min_dependency_depth, bool)
        or min_dependency_depth < 0
    ):
        raise ValueError(f"ingredient selector filter invalid: {field}:{line_number}:min_dependency_depth")


def _validate_bridge_rule(bridge: BridgeRule, *, field: str, line_number: int) -> None:
    _validate_public_label(bridge.bridge_id, f"ingredient bridge id invalid: {field}:{line_number}")
    unsupported_metadata = sorted(set(bridge.metadata) - INGREDIENT_BRIDGE_METADATA_KEYS)
    if unsupported_metadata:
        raise ValueError(f"ingredient bridge metadata unsupported: {field}:{line_number}:{unsupported_metadata[0]}")
    meaning = bridge.metadata.get("meaning")
    if meaning is not None and (not isinstance(meaning, str) or not meaning.strip()):
        raise ValueError(f"ingredient bridge metadata invalid: {field}:{line_number}:meaning")
    if isinstance(meaning, str):
        _validate_public_label(meaning, f"ingredient bridge metadata invalid: {field}:{line_number}:meaning")
    if not bridge.from_domain:
        raise ValueError(f"ingredient bridge from domain missing: {field}:{line_number}")
    if not bridge.to_domain:
        raise ValueError(f"ingredient bridge to domain missing: {field}:{line_number}")
    _validate_domain_label(bridge.from_domain, f"ingredient bridge domain invalid: {field}:{line_number}:from_domain")
    _validate_domain_label(bridge.to_domain, f"ingredient bridge domain invalid: {field}:{line_number}:to_domain")
    if bridge.from_domain == bridge.to_domain:
        raise ValueError(f"ingredient bridge domains not bridged: {field}:{line_number}:{bridge.from_domain}")
    if not bridge.safe_recipes:
        raise ValueError(f"ingredient bridge safe recipes missing: {field}:{line_number}")
    duplicate_recipe = _first_duplicate(bridge.safe_recipes)
    if duplicate_recipe is not None:
        raise ValueError(f"ingredient bridge safe recipe duplicate: {field}:{line_number}:{duplicate_recipe}")
    if bridge.safe_recipes != tuple(sorted(bridge.safe_recipes)):
        raise ValueError(f"ingredient bridge safe recipe order invalid: {field}:{line_number}")


def _validate_compatibility_edge(edge: CompatibilityEdge, *, field: str, line_number: int) -> None:
    _validate_public_label(edge.edge_id, f"ingredient compatibility edge id invalid: {field}:{line_number}")
    _validate_public_label(
        edge.ingredient_class,
        f"ingredient compatibility class invalid: {field}:{line_number}",
    )
    if edge.certification_receipt_sha256 == "0" * 64:
        raise ValueError(f"ingredient compatibility certification receipt placeholder: {field}:{line_number}")
    if not edge.difficulty_lanes:
        raise ValueError(f"ingredient compatibility difficulty lanes missing: {field}:{line_number}")
    duplicate_lane = _first_duplicate(edge.difficulty_lanes)
    if duplicate_lane is not None:
        raise ValueError(f"ingredient compatibility difficulty lane duplicate: {field}:{line_number}:{duplicate_lane}")
    canonical_lanes = tuple(lane for lane in DIFFICULTY_LANES if lane in edge.difficulty_lanes)
    if edge.difficulty_lanes != canonical_lanes:
        raise ValueError(f"ingredient compatibility difficulty lane order invalid: {field}:{line_number}")
    if not edge.allowed_domains:
        raise ValueError(f"ingredient compatibility allowed domains missing: {field}:{line_number}")
    for domain in edge.allowed_domains:
        _validate_domain_label(domain, f"ingredient compatibility allowed domain invalid: {field}:{line_number}")
    duplicate_domain = _first_duplicate(edge.allowed_domains)
    if duplicate_domain is not None:
        raise ValueError(f"ingredient compatibility allowed domain duplicate: {field}:{line_number}:{duplicate_domain}")
    if edge.allowed_domains != tuple(sorted(edge.allowed_domains)):
        raise ValueError(f"ingredient compatibility allowed domain order invalid: {field}:{line_number}")
    if not edge.allowed_fact_patterns:
        raise ValueError(f"ingredient compatibility fact patterns missing: {field}:{line_number}")
    for pattern in edge.allowed_fact_patterns:
        _validate_public_label(pattern, f"ingredient compatibility fact pattern invalid: {field}:{line_number}")
    duplicate_pattern = _first_duplicate(edge.allowed_fact_patterns)
    if duplicate_pattern is not None:
        raise ValueError(f"ingredient compatibility fact pattern duplicate: {field}:{line_number}:{duplicate_pattern}")
    if edge.allowed_fact_patterns != tuple(sorted(edge.allowed_fact_patterns)):
        raise ValueError(f"ingredient compatibility fact pattern order invalid: {field}:{line_number}")
    duplicate_definition = _first_duplicate(edge.allowed_definition_ids)
    if duplicate_definition is not None:
        raise ValueError(
            f"ingredient compatibility definition duplicate: {field}:{line_number}:{duplicate_definition}"
        )
    if edge.allowed_definition_ids != tuple(sorted(edge.allowed_definition_ids)):
        raise ValueError(f"ingredient compatibility definition order invalid: {field}:{line_number}")
    duplicate_bridge = _first_duplicate(edge.bridge_ids)
    if duplicate_bridge is not None:
        raise ValueError(f"ingredient compatibility bridge duplicate: {field}:{line_number}:{duplicate_bridge}")
    if edge.bridge_ids != tuple(sorted(edge.bridge_ids)):
        raise ValueError(f"ingredient compatibility bridge order invalid: {field}:{line_number}")


def _component_recipe_ids(row: BaseModel) -> set[str]:
    return set(_component_recipe_id_values(row))


def _component_recipe_id_values(row: BaseModel) -> tuple[str, ...]:
    if isinstance(row, CompatibilityEdge):
        return (row.recipe_id,)
    if isinstance(row, RecipeSelector):
        return row.recipe_ids
    if isinstance(row, BridgeRule):
        return row.safe_recipes
    return ()


def _validate_component_references(
    component_rows: dict[str, tuple[BaseModel, ...]],
    recipes: tuple[RecipeRule, ...],
) -> None:
    recipe_by_id = {recipe.recipe_id: recipe for recipe in recipes}
    definition_ids = {
        row.definition_id
        for row in component_rows.get("definitions_sha256", ())
        if isinstance(row, DefinitionIngredient)
    }
    fact_rows = tuple(
        row
        for field in ("facts_sha256", "source_theorems_sha256", "source_lemmas_sha256")
        for row in component_rows.get(field, ())
        if isinstance(row, FactIngredient)
    )
    _validate_fact_catalog_unique_ids(fact_rows)
    fact_kinds = {row.kind for row in fact_rows}
    bridge_ids = {
        row.bridge_id
        for row in component_rows.get("bridge_catalog_sha256", ())
        if isinstance(row, BridgeRule)
    }
    bridge_by_id = {
        row.bridge_id: row
        for row in component_rows.get("bridge_catalog_sha256", ())
        if isinstance(row, BridgeRule)
    }
    for recipe in recipes:
        if not set(recipe.required_definitions).issubset(definition_ids):
            raise ValueError(f"ingredient recipe definition reference missing: recipe_rules:{recipe.recipe_id}")
        if not set(recipe.required_fact_kinds).issubset(fact_kinds):
            raise ValueError(f"ingredient recipe fact kind missing: recipe_rules:{recipe.recipe_id}")
    for field in ("compatibility_graph_sha256", "source_compatibility_sha256", "definition_compatibility_sha256"):
        for line_number, row in enumerate(component_rows.get(field, ()), start=1):
            if not isinstance(row, CompatibilityEdge):
                continue
            recipe = recipe_by_id[row.recipe_id]
            if row.ingredient_class not in recipe.required_ingredient_classes:
                raise ValueError(f"ingredient compatibility class undeclared: {field}:{line_number}")
            undeclared_domain = next(
                (domain for domain in row.allowed_domains if domain not in recipe.domains),
                None,
            )
            if undeclared_domain is not None:
                raise ValueError(
                    f"ingredient compatibility domain undeclared: {field}:{line_number}:{undeclared_domain}"
                )
            if not set(row.allowed_definition_ids).issubset(definition_ids):
                raise ValueError(f"ingredient compatibility definition reference missing: {field}:{line_number}")
            if not set(row.bridge_ids).issubset(bridge_ids):
                raise ValueError(f"ingredient compatibility bridge reference missing: {field}:{line_number}")
            for bridge_id in row.bridge_ids:
                bridge = bridge_by_id[bridge_id]
                if row.recipe_id not in bridge.safe_recipes:
                    raise ValueError(f"ingredient compatibility bridge unsafe: {field}:{line_number}:{bridge_id}")
                if not _bridge_matches_recipe_domains(bridge, recipe):
                    raise ValueError(
                        f"ingredient compatibility bridge domain undeclared: {field}:{line_number}:{bridge_id}"
                    )


def ingredient_repository_report_hashes(
    root: Path,
    *,
    component_schema_counts: dict[str, int] | None = None,
    mathlib_commit: str | None = None,
) -> dict[str, str]:
    _require_ingredient_root(root)
    report_counts = _ingredient_report_counts(root, component_schema_counts) if component_schema_counts else None
    bridge_ids = _quality_report_bridge_ids(root) if component_schema_counts else None
    hashes = {}
    for report_id, relative_path in INGREDIENT_REPOSITORY_REPORT_PATHS.items():
        payload, raw = _canonical_json_object(
            root / relative_path,
            invalid=f"ingredient report invalid: {report_id}",
            noncanonical=f"ingredient report noncanonical: {report_id}",
            path_invalid=f"ingredient report path invalid: {report_id}",
        )
        if report_id == "extraction_report":
            _validate_extraction_report(payload, mathlib_commit=mathlib_commit)
        if report_id == "ingredient_quality_report":
            _validate_quality_report(payload, bridge_ids=bridge_ids)
        if report_counts:
            _validate_report_counts(report_id, payload, report_counts)
        hashes[f"{report_id}_sha256"] = hashlib.sha256(raw).hexdigest()
    return hashes


def _validate_extraction_report(payload: dict[str, object], *, mathlib_commit: str | None = None) -> None:
    missing = sorted(INGREDIENT_EXTRACTION_REPORT_KEYS - payload.keys())
    if missing:
        raise ValueError(f"ingredient extraction report missing: {', '.join(missing)}")
    unsupported = sorted(set(payload) - INGREDIENT_EXTRACTION_REPORT_KEYS)
    if unsupported:
        raise ValueError(f"ingredient extraction report unsupported: {unsupported[0]}")
    _require_schema_version_1(payload, "ingredient extraction report invalid: schema_version")
    report_mathlib_commit = payload.get("mathlib_commit")
    if not isinstance(report_mathlib_commit, str) or not GIT_COMMIT_RE.fullmatch(report_mathlib_commit):
        raise ValueError("ingredient extraction report invalid: mathlib_commit")
    _reject_placeholder_git_commit(
        report_mathlib_commit,
        "ingredient extraction report mathlib commit placeholder",
    )
    if mathlib_commit is not None and report_mathlib_commit != mathlib_commit:
        raise ValueError("ingredient extraction report mathlib commit mismatch")
    for key in ("source_row_count", "definition_count", "fact_count"):
        _validate_extraction_report_nonnegative_int(payload.get(key), key)
    _validate_extraction_report_count_map(payload.get("source_license_counts"), "source_license_counts")
    source_row_count = cast(int, payload["source_row_count"])
    if source_row_count != cast(int, payload["definition_count"]) + cast(int, payload["fact_count"]):
        raise ValueError("ingredient extraction report count mismatch: source_row_count")
    if sum(cast(dict[str, int], payload["source_license_counts"]).values()) != source_row_count:
        raise ValueError("ingredient extraction report count mismatch: source_license_counts")


def _validate_extraction_report_nonnegative_int(value: object, key: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"ingredient extraction report invalid: {key}")


def _validate_extraction_report_count_map(value: object, key: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"ingredient extraction report invalid: {key}")
    for item_key, item_value in value.items():
        if not isinstance(item_key, str) or not item_key:
            raise ValueError(f"ingredient extraction report invalid: {key}")
        if type(item_value) is not int or item_value < 0:
            raise ValueError(f"ingredient extraction report invalid: {key}:{item_key}")


def _quality_report_bridge_ids(root: Path) -> set[str]:
    shortcut_policy_checks = _shortcut_policy_checks(root)
    recipes = _recipe_rules(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        shortcut_policy_checks=shortcut_policy_checks,
    )
    recipe_ids = {recipe.recipe_id for recipe in recipes}
    return {
        row.bridge_id
        for row in cast(
            tuple[BridgeRule, ...],
            _jsonl_model_rows(
                root / INGREDIENT_MANIFEST_COMPONENT_PATHS["bridge_catalog_sha256"],
                "bridge_catalog_sha256",
                BridgeRule,
                mathlib_commit=None,
                recipe_ids=recipe_ids,
            ),
        )
    }


def _validate_quality_report(payload: dict[str, object], *, bridge_ids: set[str] | None = None) -> None:
    missing = sorted(INGREDIENT_QUALITY_REPORT_KEYS - payload.keys())
    if missing:
        raise ValueError(f"ingredient quality report missing: {', '.join(missing)}")
    unsupported = sorted(set(payload) - INGREDIENT_QUALITY_REPORT_KEYS)
    if unsupported:
        raise ValueError(f"ingredient quality report unsupported: {unsupported[0]}")
    for key in ("definition_count", "fact_count", "compatibility_edge_count", "recipe_count"):
        _validate_quality_report_nonnegative_int(payload.get(key), key)
    _validate_quality_report_count_map(
        payload.get("difficulty_lane_coverage"),
        "difficulty_lane_coverage",
        allowed_keys=DIFFICULTY_LANES,
    )
    _validate_quality_report_count_map(
        payload.get("bridge_coverage"),
        "bridge_coverage",
        allowed_keys=bridge_ids,
    )
    _validate_quality_report_nonnegative_int(
        payload.get("estimated_theorem_space_size"),
        "estimated_theorem_space_size",
    )
    if payload["estimated_theorem_space_size"] and (
        payload["recipe_count"] == 0 or payload["compatibility_edge_count"] == 0
    ):
        raise ValueError("ingredient quality report theorem space unavailable")
    _validate_quality_report_count_map(
        payload.get("shortcut_risk_distribution"),
        "shortcut_risk_distribution",
        allowed_keys=SUPPORTED_INGREDIENT_SHORTCUT_RISK_LABELS,
    )
    _validate_quality_report_bool_map(
        payload.get("reserve_selector_health"),
        "reserve_selector_health",
        allowed_keys={"ready"},
    )
    if payload["estimated_theorem_space_size"] and (
        not isinstance(payload["reserve_selector_health"], dict)
        or payload["reserve_selector_health"].get("ready") is not True
    ):
        raise ValueError("ingredient quality report reserve selector unavailable")


def _validate_quality_report_nonnegative_int(value: object, key: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"ingredient quality report invalid: {key}")


def _validate_quality_report_count_map(
    value: object,
    key: str,
    *,
    allowed_keys: Iterable[str] | None = None,
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"ingredient quality report invalid: {key}")
    allowed = set(allowed_keys) if allowed_keys is not None else None
    for item_key, item_value in value.items():
        if not isinstance(item_key, str) or not item_key:
            raise ValueError(f"ingredient quality report invalid: {key}")
        if allowed is not None and item_key not in allowed:
            raise ValueError(f"ingredient quality report unsupported: {key}:{item_key}")
        if type(item_value) is not int or item_value < 0:
            raise ValueError(f"ingredient quality report invalid: {key}:{item_key}")


def _validate_quality_report_bool_map(
    value: object,
    key: str,
    *,
    allowed_keys: Iterable[str] | None = None,
) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"ingredient quality report invalid: {key}")
    allowed = set(allowed_keys) if allowed_keys is not None else None
    for item_key, item_value in value.items():
        if not isinstance(item_key, str) or not item_key:
            raise ValueError(f"ingredient quality report invalid: {key}")
        if allowed is not None and item_key not in allowed:
            raise ValueError(f"ingredient quality report unsupported: {key}:{item_key}")
        if not isinstance(item_value, bool):
            raise ValueError(f"ingredient quality report invalid: {key}:{item_key}")


def _ingredient_report_counts(root: Path, component_schema_counts: dict[str, int]) -> dict[str, int]:
    return {
        "compatibility_edge_count": sum(
            component_schema_counts[field]
            for field in (
                "compatibility_graph_sha256",
                "source_compatibility_sha256",
                "definition_compatibility_sha256",
            )
        ),
        "definition_count": component_schema_counts["definitions_sha256"],
        "fact_count": sum(
            component_schema_counts[field]
            for field in ("facts_sha256", "source_theorems_sha256", "source_lemmas_sha256")
        ),
        "recipe_count": len(
            _recipe_rules(
                root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
                shortcut_policy_checks=_shortcut_policy_checks(root),
            )
        ),
    }


def _validate_report_counts(report_id: str, payload: dict[str, object], expected: dict[str, int]) -> None:
    for key, expected_value in expected.items():
        actual = payload.get(key)
        if actual is None:
            continue
        if type(actual) is not int or actual != expected_value:
            raise ValueError(f"ingredient report count mismatch: {report_id}:{key}")


def ingredient_recipe_artifact_hashes(root: Path) -> dict[str, str]:
    _require_ingredient_root(root)
    hashes = {}
    recipes: tuple[RecipeRule, ...] = ()
    shortcut_policy_checks = _shortcut_policy_checks(root)
    for artifact_id, relative_path in INGREDIENT_RECIPE_ARTIFACT_PATHS.items():
        payload, raw = _canonical_json_object(
            root / relative_path,
            invalid=f"ingredient recipe artifact invalid: {artifact_id}",
            noncanonical=f"ingredient recipe artifact noncanonical: {artifact_id}",
            path_invalid=f"ingredient recipe artifact path invalid: {artifact_id}",
        )
        if artifact_id == "recipe_rules":
            recipes = _recipe_rules_from_payload(
                payload,
                shortcut_policy_checks=shortcut_policy_checks,
            )
        elif artifact_id == "parameter_sets":
            _validate_parameter_sets(payload, recipes=recipes)
        else:
            _validate_recipe_artifact_payload(artifact_id, payload)
        hashes[f"{artifact_id}_sha256"] = hashlib.sha256(raw).hexdigest()
    template_dir = root / "recipes" / "soundness_templates"
    for path in sorted(template_dir.rglob("*")):
        relative_path = path.relative_to(root / "recipes").as_posix()
        if (
            path.is_symlink()
            or path.is_dir()
            or path.parent != template_dir
            or path.suffix != ".lean"
            or not path.is_file()
        ):
            raise ValueError(f"ingredient recipe soundness template unexpected: {relative_path}")
    templates = {
        path.relative_to(root / "recipes").as_posix(): path
        for path in sorted(template_dir.glob("*.lean"))
        if path.is_file()
    }
    if recipes and not templates:
        raise ValueError("ingredient soundness templates missing")
    required_templates = {recipe.soundness_template for recipe in recipes}
    for recipe in recipes:
        if recipe.soundness_template not in templates:
            raise ValueError(f"ingredient recipe soundness template missing: recipe_rules:{recipe.recipe_id}")
        try:
            template_text = templates[recipe.soundness_template].read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"ingredient recipe soundness template invalid: recipe_rules:{recipe.recipe_id}") from e
        _validate_soundness_template_source(recipe, template_text)
    unused_templates = sorted(set(templates) - required_templates)
    if unused_templates:
        raise ValueError(f"ingredient recipe soundness template unused: {unused_templates[0]}")
    for path in templates.values():
        hashes[f"soundness_template:{path.name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _validate_recipe_artifact_payload(artifact_id: str, payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"ingredient recipe artifact invalid: {artifact_id}")


def _validate_parameter_sets(payload: object, *, recipes: Sequence[RecipeRule]) -> None:
    if (
        not isinstance(payload, dict)
        or not all(isinstance(name, str) and isinstance(values, list) for name, values in payload.items())
    ):
        raise ValueError("ingredient recipe artifact invalid: parameter_sets")
    required_parameter_sets = (
        {"Bool" for recipe in recipes if recipe.parameter_rule == "finite_bool"}
        | {"Int" for recipe in recipes if recipe.parameter_rule == "finite_int"}
        | {"Nat" for recipe in recipes if recipe.parameter_rule == "finite_nat"}
    )
    unsupported = sorted(set(payload) - required_parameter_sets)
    if unsupported:
        raise ValueError(f"ingredient recipe parameter set unsupported: parameter_sets:{unsupported[0]}")
    for recipe in recipes:
        if recipe.parameter_rule == "finite_nat":
            values = payload.get("Nat")
            if not isinstance(values, list) or not values:
                raise ValueError(f"ingredient recipe parameter set missing: recipe_rules:{recipe.recipe_id}:Nat")
            if not all(isinstance(value, str) and NAT_PARAMETER_RE.fullmatch(value) for value in values):
                raise ValueError(f"ingredient recipe parameter set invalid: recipe_rules:{recipe.recipe_id}:Nat")
        if recipe.parameter_rule == "finite_bool":
            values = payload.get("Bool")
            if not isinstance(values, list) or not values:
                raise ValueError(f"ingredient recipe parameter set missing: recipe_rules:{recipe.recipe_id}:Bool")
            if not all(isinstance(value, str) and value in {"false", "true"} for value in values):
                raise ValueError(f"ingredient recipe parameter set invalid: recipe_rules:{recipe.recipe_id}:Bool")
        if recipe.parameter_rule == "finite_int":
            values = payload.get("Int")
            if not isinstance(values, list) or not values:
                raise ValueError(f"ingredient recipe parameter set missing: recipe_rules:{recipe.recipe_id}:Int")
            if not all(isinstance(value, str) and INT_PARAMETER_RE.fullmatch(value) for value in values):
                raise ValueError(f"ingredient recipe parameter set invalid: recipe_rules:{recipe.recipe_id}:Int")
    if any(not values for values in payload.values()):
        raise ValueError("ingredient recipe artifact invalid: parameter_sets")
    for name, values in payload.items():
        duplicate = _first_duplicate(values)
        if duplicate is not None:
            raise ValueError(f"ingredient recipe parameter set duplicate: parameter_sets:{name}:{duplicate}")
        if tuple(values) != _canonical_parameter_values(name, values):
            raise ValueError(f"ingredient recipe parameter set order invalid: parameter_sets:{name}")


def _canonical_parameter_values(name: str, values: Sequence[str]) -> tuple[str, ...]:
    if name in {"Int", "Nat"}:
        return tuple(sorted(values, key=int))
    if name == "Bool":
        return tuple(sorted(values, key=BOOL_PARAMETER_ORDER.__getitem__))
    return tuple(values)


def _validate_soundness_template_source(recipe: RecipeRule, source: str) -> None:
    match = SOUNDNESS_TEMPLATE_FORBIDDEN_TOKEN_RE.search(source)
    if match is not None:
        token = match.group(1) or match.group(0)
        raise ValueError(
            f"ingredient recipe soundness template forbidden token: recipe_rules:{recipe.recipe_id}:{token}"
        )
    if not _soundness_template_has_declaration(source):
        raise ValueError(
            f"ingredient recipe soundness template declaration missing: recipe_rules:{recipe.recipe_id}"
        )
    imports = tuple(
        line.strip().removeprefix("import ").strip()
        for line in source.splitlines()
        if line.strip().startswith("import ")
    )
    if imports:
        try:
            validate_ingredient_imports(imports)
        except ValueError as e:
            raise ValueError(
                f"ingredient recipe soundness template import invalid: recipe_rules:{recipe.recipe_id}"
            ) from e


def _soundness_template_has_declaration(source: str) -> bool:
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--") or line.startswith("import "):
            continue
        if line.startswith(("theorem ", "lemma ")):
            return True
    return False


def _parameter_sets(path: Path, *, recipes: Sequence[RecipeRule]) -> dict[str, tuple[Any, ...]]:
    payload, _ = _canonical_json_object(
        path,
        invalid="ingredient recipe artifact invalid: parameter_sets",
        noncanonical="ingredient recipe artifact noncanonical: parameter_sets",
        path_invalid="ingredient recipe artifact path invalid: parameter_sets",
    )
    _validate_parameter_sets(payload, recipes=recipes)
    return {name: tuple(values) for name, values in payload.items() if isinstance(values, list)}


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} invalid")


def _validate_non_placeholder_sha256(value: str, label: str) -> None:
    _validate_sha256(value, label)
    _reject_placeholder_sha256(value, f"{label} placeholder")


def _validate_git_commit(value: str, label: str) -> None:
    if not GIT_COMMIT_RE.fullmatch(value):
        raise ValueError(f"{label} invalid")
    _reject_placeholder_git_commit(value, f"{label} placeholder")


def ingredient_novelty_family_hash(selection: IngredientSelectionReceipt) -> str:
    return canonical_sha256(
        {
            "version": "lemma-ingredient-novelty-family-v1",
            "recipe_id": selection.selected_recipe_id,
            "definition_ids": list(selection.selected_definition_ids),
            "fact_ids": list(selection.selected_fact_ids),
            "bridge_ids": list(selection.selected_bridge_ids),
            "selected_parameters": selection.selected_parameters,
        }
    )


def ingredient_challenge_seed_sha256(
    *,
    netuid: int,
    tempo: int,
    epoch_seed: str,
    ingredient_manifest_sha256: str,
    recipe_bundle_sha256: str,
    difficulty_state_sha256: str,
) -> str:
    try:
        netuid_value = _exact_public_int(netuid)
        tempo_value = _exact_public_int(tempo)
    except ValueError as e:
        raise ValueError("ingredient challenge seed public integer invalid") from e
    if netuid_value < 0 or tempo_value < 0:
        raise ValueError("ingredient challenge seed public integer invalid")
    _validate_public_epoch_seed(epoch_seed, "ingredient challenge seed epoch seed invalid")
    _validate_non_placeholder_sha256(
        ingredient_manifest_sha256,
        "ingredient challenge seed manifest sha256",
    )
    _validate_non_placeholder_sha256(
        recipe_bundle_sha256,
        "ingredient challenge seed recipe bundle sha256",
    )
    _validate_non_placeholder_sha256(
        difficulty_state_sha256,
        "ingredient challenge seed difficulty state sha256",
    )
    return canonical_sha256(
        {
            "version": "lemma-ingredient-challenge-v1",
            "netuid": netuid_value,
            "tempo": tempo_value,
            "epoch_seed": epoch_seed,
            "ingredient_manifest_sha256": ingredient_manifest_sha256,
            "recipe_bundle_sha256": recipe_bundle_sha256,
            "difficulty_state_sha256": difficulty_state_sha256,
        }
    )


def ingredient_challenge_slot_seed_sha256(
    *,
    challenge_seed_sha256: str,
    queue_position: int,
    active_K: int,
) -> str:
    _validate_non_placeholder_sha256(
        challenge_seed_sha256,
        "ingredient challenge slot seed challenge sha256",
    )
    try:
        queue_position_value = _exact_public_int(queue_position)
        active_k_value = _exact_public_int(active_K)
    except ValueError as e:
        raise ValueError("ingredient challenge slot seed public integer invalid") from e
    if active_k_value < 1 or queue_position_value < 0 or queue_position_value >= active_k_value:
        raise ValueError("ingredient challenge slot seed public integer invalid")
    if active_k_value == 1:
        return challenge_seed_sha256
    return canonical_sha256(
        {
            "version": "lemma-ingredient-challenge-slot-v1",
            "challenge_seed_sha256": challenge_seed_sha256,
            "queue_position": queue_position_value,
            "active_K": active_k_value,
        }
    )


def build_ingredient_generation_receipt(
    *,
    tempo: int,
    epoch_seed: str,
    ingredient_manifest_sha256: str,
    lemma_corpus_snapshot_sha256: str,
    ingredient_repo_commit: str,
    mathlib_commit: str,
    recipe_bundle_sha256: str,
    difficulty_state_sha256: str,
    selection: IngredientSelectionReceipt,
    active_task_id: str,
    active_target_sha256: str,
    theorem_statement: str,
    gate_receipt: IngredientGateReceipt,
    shortcut_receipt: IngredientGateReceipt,
    active_K: int = 1,
) -> IngredientGenerationReceipt:
    _validate_public_epoch_seed(epoch_seed, "ingredient generation receipt epoch seed invalid")
    theorem_statement_sha256 = text_sha256(theorem_statement)
    selection_receipt_sha256 = canonical_sha256(selection)
    gate_receipt_sha256 = _verified_generation_child_receipt_sha256(
        gate_receipt,
        receipt_kind="statement_gate",
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        selection_receipt_sha256=selection_receipt_sha256,
        label="gate",
    )
    shortcut_receipt_sha256 = _verified_generation_child_receipt_sha256(
        shortcut_receipt,
        receipt_kind="shortcut_gate",
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        selection_receipt_sha256=selection_receipt_sha256,
        label="shortcut",
    )
    return _ingredient_generation_receipt_from_hashes(
        tempo=tempo,
        active_K=active_K,
        epoch_seed_sha256=text_sha256(epoch_seed),
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        lemma_corpus_snapshot_sha256=lemma_corpus_snapshot_sha256,
        ingredient_repo_commit=ingredient_repo_commit,
        mathlib_commit=mathlib_commit,
        recipe_bundle_sha256=recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
        selection=selection,
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        gate_receipt_sha256=gate_receipt_sha256,
        shortcut_receipt_sha256=shortcut_receipt_sha256,
    )


def _verified_generation_child_receipt_sha256(
    receipt: IngredientGateReceipt,
    *,
    receipt_kind: str,
    active_task_id: str,
    active_target_sha256: str,
    theorem_statement_sha256: str,
    ingredient_manifest_sha256: str,
    selection_receipt_sha256: str,
    label: str,
) -> str:
    if receipt.receipt_kind != receipt_kind or receipt.status != "passed":
        raise ValueError(f"ingredient generation receipt {label} receipt mismatch")
    expected = {
        "active_task_id": active_task_id,
        "active_target_sha256": active_target_sha256,
        "theorem_statement_sha256": theorem_statement_sha256,
        "ingredient_manifest_sha256": ingredient_manifest_sha256,
        "selection_receipt_sha256": selection_receipt_sha256,
    }
    if any(getattr(receipt, key) != value for key, value in expected.items()):
        raise ValueError(f"ingredient generation receipt {label} receipt mismatch")
    return canonical_sha256(receipt)


def _ingredient_generation_receipt_from_hashes(
    *,
    tempo: int,
    active_K: int,
    epoch_seed_sha256: str,
    ingredient_manifest_sha256: str,
    lemma_corpus_snapshot_sha256: str,
    ingredient_repo_commit: str,
    mathlib_commit: str,
    recipe_bundle_sha256: str,
    difficulty_state_sha256: str,
    selection: IngredientSelectionReceipt,
    active_task_id: str,
    active_target_sha256: str,
    theorem_statement_sha256: str,
    gate_receipt_sha256: str,
    shortcut_receipt_sha256: str,
) -> IngredientGenerationReceipt:
    return IngredientGenerationReceipt(
        schema_version=1,
        tempo=tempo,
        active_K=active_K,
        epoch_seed_sha256=epoch_seed_sha256,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        lemma_corpus_snapshot_sha256=lemma_corpus_snapshot_sha256,
        ingredient_repo_commit=ingredient_repo_commit,
        mathlib_commit=mathlib_commit,
        recipe_bundle_sha256=recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
        selection=selection,
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        gate_receipt_sha256=gate_receipt_sha256,
        shortcut_receipt_sha256=shortcut_receipt_sha256,
    )


def _validate_public_epoch_seed(value: object, message: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(message)
    return value


def build_ingredient_task(
    *,
    receipt: IngredientGenerationReceipt,
    theorem_name: str,
    type_expr: str,
    statement: str,
    title: str = "",
    imports: tuple[str, ...] = ("Mathlib",),
    source_license: str = INGREDIENT_TASK_SOURCE_LICENSE,
    lean_toolchain: str = DEFAULT_TOOLCHAIN,
    policy: str = INGREDIENT_TASK_SUBMISSION_POLICY,
    queue_position: int = 0,
    queue_depth: int = 0,
    frontier_depth: int = 0,
) -> LemmaTask:
    validate_ingredient_theorem_name(theorem_name)
    validate_ingredient_imports(imports)
    validate_ingredient_task_title(title)
    validate_ingredient_task_source_license(source_license)
    validate_ingredient_task_submission_policy(policy)
    validate_ingredient_task_lean_toolchain(lean_toolchain)
    validate_ingredient_statement_header(
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=statement,
    )
    if receipt.theorem_statement_sha256 != text_sha256(statement):
        raise ValueError("ingredient receipt theorem_statement_sha256 mismatch")
    task = LemmaTask(
        id=receipt.active_task_id,
        title=title,
        source_stream="ingredient",
        source_ref=SourceRef(
            kind="ingredient",
            name=receipt.selection.selected_recipe_id,
            commit=receipt.ingredient_repo_commit,
        ),
        source_license=source_license,
        imports=imports,
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=statement,
        submission_stub=ingredient_submission_stub(theorem_name, type_expr, imports),
        lean_toolchain=lean_toolchain,
        mathlib_rev=receipt.mathlib_commit,
        policy=policy,
        queue_position=queue_position,
        queue_depth=queue_depth,
        frontier_depth=frontier_depth,
        difficulty_band=receipt.selection.difficulty_lane,
        metadata=_task_metadata(receipt),
    )
    if task.target_sha256 != receipt.active_target_sha256:
        raise ValueError("ingredient receipt active_target_sha256 mismatch")
    return task


def build_fixture_ingredient_task(
    *,
    receipt: IngredientGenerationReceipt,
    theorem_name: str,
    type_expr: str,
    statement: str,
    title: str = "",
    imports: tuple[str, ...] = ("Mathlib",),
    source_license: str = INGREDIENT_TASK_SOURCE_LICENSE,
    lean_toolchain: str = DEFAULT_TOOLCHAIN,
    policy: str = INGREDIENT_TASK_SUBMISSION_POLICY,
    queue_position: int = 0,
    queue_depth: int = 0,
    frontier_depth: int = 0,
) -> LemmaTask:
    return build_ingredient_task(
        receipt=receipt,
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=statement,
        title=title,
        imports=imports,
        source_license=source_license,
        lean_toolchain=lean_toolchain,
        policy=policy,
        queue_position=queue_position,
        queue_depth=queue_depth,
        frontier_depth=frontier_depth,
    )


def ingredient_generation_receipt_from_task(task: LemmaTask) -> IngredientGenerationReceipt:
    """Rebuild the canonical ingredient generation receipt from public task fields."""
    metadata = task.metadata
    return IngredientGenerationReceipt(
        schema_version=1,
        tempo=_metadata_int(metadata, "tempo"),
        active_K=_metadata_active_k(metadata),
        epoch_seed_sha256=_metadata_str(metadata, "epoch_seed_sha256"),
        ingredient_manifest_sha256=_metadata_str(metadata, "ingredient_manifest_sha256"),
        lemma_corpus_snapshot_sha256=_metadata_str(metadata, "lemma_corpus_snapshot_sha256"),
        ingredient_repo_commit=_metadata_str(metadata, "ingredient_repo_commit"),
        mathlib_commit=_metadata_str(metadata, "mathlib_commit"),
        recipe_bundle_sha256=_metadata_str(metadata, "recipe_bundle_sha256"),
        difficulty_state_sha256=_metadata_str(metadata, "difficulty_state_sha256"),
        selection=IngredientSelectionReceipt(
            selected_selector_id=_metadata_str(metadata, "selector_id"),
            selected_recipe_id=_metadata_str(metadata, "recipe_id"),
            selected_definition_ids=_metadata_str_tuple(metadata, "definition_ids"),
            selected_fact_ids=_metadata_str_tuple(metadata, "fact_ids"),
            selected_bridge_ids=_metadata_str_tuple(metadata, "bridge_ids"),
            selected_parameters=_metadata_selected_parameters(metadata),
            difficulty_lane=cast(DifficultyLane, _metadata_str(metadata, "difficulty_lane")),
            selection_seed_sha256=_metadata_str(metadata, "selection_seed_sha256"),
        ),
        active_task_id=task.id,
        active_target_sha256=task.target_sha256,
        theorem_statement_sha256=text_sha256(task.statement),
        gate_receipt_sha256=_metadata_str(metadata, "gate_receipt_sha256"),
        shortcut_receipt_sha256=_metadata_str(metadata, "shortcut_receipt_sha256"),
    )


def expected_ingredient_generation_receipt_sha256(task: LemmaTask) -> str:
    return canonical_sha256(ingredient_generation_receipt_from_task(task))


def expected_ingredient_novelty_family_hash(task: LemmaTask) -> str:
    return ingredient_novelty_family_hash(ingredient_generation_receipt_from_task(task).selection)


def build_fixture_ingredient_registry(
    *,
    netuid: int,
    tempo: int,
    epoch_seed: str,
    ingredient_manifest_sha256: str,
    lemma_corpus_snapshot_sha256: str,
    ingredient_repo_commit: str,
    mathlib_commit: str,
    recipe_bundle_sha256: str,
    difficulty_state_sha256: str,
    difficulty_lane: DifficultyLane,
    selectors: tuple[RecipeSelector, ...],
    recipes: tuple[RecipeRule, ...],
    definitions: tuple[DefinitionIngredient, ...],
    facts: tuple[FactIngredient, ...],
    compatibility_edges: tuple[CompatibilityEdge, ...],
    theorem_name: str,
    type_expr: str,
    statement: str,
    active_task_id: str,
    gate_receipt_sha256: str,
    shortcut_receipt_sha256: str,
    bridges: tuple[BridgeRule, ...] = (),
    parameter_sets: dict[str, tuple[Any, ...]] | None = None,
    title: str = "",
    imports: tuple[str, ...] = ("Mathlib",),
    source_license: str = INGREDIENT_TASK_SOURCE_LICENSE,
    lean_toolchain: str = DEFAULT_TOOLCHAIN,
    policy: str = INGREDIENT_TASK_SUBMISSION_POLICY,
    active_K: int = 1,
) -> TaskRegistry:
    challenge_seed = ingredient_challenge_seed_sha256(
        netuid=netuid,
        tempo=tempo,
        epoch_seed=epoch_seed,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        recipe_bundle_sha256=recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
    )
    selection = select_fixture_ingredients(
        challenge_seed_sha256=challenge_seed,
        difficulty_lane=difficulty_lane,
        selectors=selectors,
        recipes=recipes,
        definitions=definitions,
        facts=facts,
        compatibility_edges=compatibility_edges,
        bridges=bridges,
        parameter_sets=parameter_sets,
    )
    receipt = _ingredient_generation_receipt_from_hashes(
        tempo=tempo,
        active_K=active_K,
        epoch_seed_sha256=text_sha256(epoch_seed),
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        lemma_corpus_snapshot_sha256=lemma_corpus_snapshot_sha256,
        ingredient_repo_commit=ingredient_repo_commit,
        mathlib_commit=mathlib_commit,
        recipe_bundle_sha256=recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
        selection=selection,
        active_task_id=active_task_id,
        active_target_sha256=_target_sha256(
            task_id=active_task_id,
            theorem_name=theorem_name,
            type_expr=type_expr,
            statement=statement,
            imports=imports,
            lean_toolchain=lean_toolchain,
            mathlib_commit=mathlib_commit,
        ),
        theorem_statement_sha256=text_sha256(statement),
        gate_receipt_sha256=gate_receipt_sha256,
        shortcut_receipt_sha256=shortcut_receipt_sha256,
    )
    task = build_fixture_ingredient_task(
        receipt=receipt,
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=statement,
        title=title,
        imports=imports,
        source_license=source_license,
        lean_toolchain=lean_toolchain,
        policy=policy,
    )
    return task_registry_from_tasks((task,))


def verify_fixture_ingredient_selection(
    task: LemmaTask,
    *,
    challenge_seed_sha256: str,
    difficulty_lane: DifficultyLane,
    selectors: tuple[RecipeSelector, ...],
    recipes: tuple[RecipeRule, ...],
    definitions: tuple[DefinitionIngredient, ...],
    facts: tuple[FactIngredient, ...],
    compatibility_edges: tuple[CompatibilityEdge, ...],
    bridges: tuple[BridgeRule, ...] = (),
    parameter_sets: dict[str, tuple[Any, ...]] | None = None,
) -> IngredientSelectionReceipt:
    """Recompute fixture ingredient selection and verify task metadata."""
    selection = select_fixture_ingredients(
        challenge_seed_sha256=challenge_seed_sha256,
        difficulty_lane=difficulty_lane,
        selectors=selectors,
        recipes=recipes,
        definitions=definitions,
        facts=facts,
        compatibility_edges=compatibility_edges,
        bridges=bridges,
        parameter_sets=parameter_sets,
    )
    expected = _selection_metadata(selection)
    if task.source_stream != "ingredient":
        raise ValueError("ingredient selection metadata mismatch: source_stream")
    if task.source_ref.kind != "ingredient" or task.source_ref.name != selection.selected_recipe_id:
        raise ValueError("ingredient selection metadata mismatch: source_ref")
    for key, value in expected.items():
        if task.metadata.get(key) != value:
            raise ValueError(f"ingredient selection metadata mismatch: {key}")
    return selection


def _task_metadata(receipt: IngredientGenerationReceipt) -> dict[str, Any]:
    receipt_sha256 = canonical_sha256(receipt)
    return {
        "supply_mode": "ingredient",
        "tempo": receipt.tempo,
        "active_K": receipt.active_K,
        "epoch_seed_sha256": receipt.epoch_seed_sha256,
        "ingredient_manifest_sha256": receipt.ingredient_manifest_sha256,
        "lemma_corpus_snapshot_sha256": receipt.lemma_corpus_snapshot_sha256,
        "ingredient_repo_commit": receipt.ingredient_repo_commit,
        "mathlib_commit": receipt.mathlib_commit,
        "recipe_bundle_sha256": receipt.recipe_bundle_sha256,
        "difficulty_state_sha256": receipt.difficulty_state_sha256,
        **_selection_metadata(receipt.selection),
        "generation_receipt_sha256": receipt_sha256,
        "gate_receipt_sha256": receipt.gate_receipt_sha256,
        "shortcut_receipt_sha256": receipt.shortcut_receipt_sha256,
        "theorem_statement_sha256": receipt.theorem_statement_sha256,
        "active_target_sha256": receipt.active_target_sha256,
    }


def _selection_metadata(selection: IngredientSelectionReceipt) -> dict[str, Any]:
    ingredient_ids = [
        *selection.selected_definition_ids,
        *selection.selected_fact_ids,
        *selection.selected_bridge_ids,
    ]
    return {
        "difficulty_lane": selection.difficulty_lane,
        "selector_id": selection.selected_selector_id,
        "recipe_id": selection.selected_recipe_id,
        "ingredient_ids": ingredient_ids,
        "ingredient_count": len(ingredient_ids),
        "hidden_lemma_count": 0,
        "novelty_family_hash": ingredient_novelty_family_hash(selection),
        "definition_ids": list(selection.selected_definition_ids),
        "fact_ids": list(selection.selected_fact_ids),
        "bridge_ids": list(selection.selected_bridge_ids),
        "selected_parameters": selection.selected_parameters,
        "selection_seed_sha256": selection.selection_seed_sha256,
    }


def _metadata_int(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"ingredient task {key} metadata malformed")
    return value


def _metadata_active_k(metadata: dict[str, Any]) -> int:
    value = metadata.get("active_K")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("ingredient task active_K metadata malformed")
    return value


def _metadata_selected_parameters(metadata: dict[str, Any]) -> dict[str, Any]:
    return validate_ingredient_selected_parameters(
        metadata.get("selected_parameters"),
        message="ingredient task selected_parameters metadata malformed",
    )


def _metadata_str(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ingredient task {key} metadata malformed")
    return value


def _metadata_str_tuple(metadata: dict[str, Any], key: str) -> tuple[str, ...]:
    value = metadata.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"ingredient task {key} metadata malformed")
    return tuple(value)


def ingredient_submission_stub(theorem_name: str, type_expr: str, imports: tuple[str, ...]) -> str:
    validate_ingredient_theorem_name(theorem_name)
    validate_ingredient_type_expr(type_expr)
    validate_ingredient_imports(imports)
    return "\n".join(
        [
            *(f"import {module}" for module in imports),
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : {type_expr} := by",
            "  sorry",
            "",
            "end Submission",
            "",
        ]
    )


def validate_ingredient_theorem_name(theorem_name: str) -> None:
    if not LEAN_IDENTIFIER_RE.fullmatch(theorem_name):
        raise ValueError("ingredient theorem name invalid")


def validate_ingredient_type_expr(type_expr: str) -> None:
    if not type_expr or type_expr != _normalized_lean_expr(type_expr):
        raise ValueError("ingredient theorem type expression not canonical")


def validate_ingredient_task_title(title: str) -> None:
    if title:
        raise ValueError("ingredient task title must be empty")


def validate_ingredient_task_source_license(source_license: str) -> None:
    if source_license != INGREDIENT_TASK_SOURCE_LICENSE:
        raise ValueError("ingredient task source license mismatch")


def validate_ingredient_task_submission_policy(policy: str) -> None:
    if policy != INGREDIENT_TASK_SUBMISSION_POLICY:
        raise ValueError("ingredient task submission policy mismatch")


def validate_ingredient_task_lean_toolchain(lean_toolchain: str) -> None:
    if lean_toolchain != DEFAULT_TOOLCHAIN:
        raise ValueError("ingredient task lean toolchain mismatch")


def validate_ingredient_task_public_envelope(task: LemmaTask, *, mathlib_commit: str) -> None:
    if task.source_stream != "ingredient" or task.source_ref.kind != "ingredient":
        raise ValueError("ingredient task source mismatch")
    if task.source_ref.url is not None or task.source_ref.path is not None:
        raise ValueError("ingredient task source_ref url/path must be empty")
    validate_ingredient_task_source_license(task.source_license)
    if task.task_version != 1:
        raise ValueError("ingredient task version mismatch")
    validate_ingredient_task_title(task.title)
    if task.triviality_status != "unknown":
        raise ValueError("ingredient task triviality status mismatch")
    if task.activation_status != "paid":
        raise ValueError("ingredient task activation status mismatch")
    if task.active_epoch is not None or task.expires_epoch is not None or "created_at_block" in task.metadata:
        raise ValueError("ingredient task lifecycle window mismatch")
    validate_ingredient_task_submission_policy(task.policy)
    if task.verifier_id != LEAN_VERIFIER_ID or task.verifier_version != LEAN_VERIFIER_VERSION:
        raise ValueError("ingredient task verifier identity mismatch")
    validate_ingredient_task_lean_toolchain(task.lean_toolchain)
    validate_ingredient_imports(task.imports)
    if task.mathlib_rev != mathlib_commit:
        raise ValueError("ingredient task mathlib commit mismatch")


def validate_ingredient_task_public_metadata(task: LemmaTask, receipt: IngredientGenerationReceipt) -> None:
    queue_position = task.queue_position
    if queue_position is None or queue_position < 0 or queue_position >= receipt.active_K:
        raise ValueError("ingredient task queue position mismatch")
    if task.queue_depth != 0:
        raise ValueError("ingredient task queue depth mismatch")
    if task.frontier_depth != 0:
        raise ValueError("ingredient task frontier depth mismatch")
    if task.difficulty_band != receipt.selection.difficulty_lane:
        raise ValueError("ingredient task difficulty band mismatch")
    expected = _task_metadata(receipt)
    if set(task.metadata) != set(expected):
        raise ValueError("ingredient task metadata schema mismatch")
    for key, value in expected.items():
        if task.metadata.get(key) != value:
            raise ValueError(f"ingredient task metadata mismatch: {key}")


def validate_ingredient_statement_header(*, theorem_name: str, type_expr: str, statement: str) -> None:
    validate_ingredient_theorem_name(theorem_name)
    validate_ingredient_type_expr(type_expr)
    expected = f"theorem {theorem_name} : {type_expr} := by\n  sorry"
    if statement == expected:
        return
    expected_header = expected.partition("\n")[0]
    actual_header = statement.replace("\r\n", "\n").replace("\r", "\n").partition("\n")[0]
    if actual_header != expected_header:
        raise ValueError("ingredient theorem statement header mismatch")
    raise ValueError("ingredient theorem statement body invalid")


def validate_ingredient_imports(imports: Sequence[str]) -> None:
    if not imports:
        raise ValueError("ingredient imports missing")
    duplicate = _first_duplicate(imports)
    if duplicate is not None:
        raise ValueError(f"ingredient import duplicate: {duplicate}")
    if tuple(imports) != tuple(sorted(imports)):
        raise ValueError("ingredient import order invalid")
    for module in imports:
        if (
            not isinstance(module, str)
            or not module
            or module.strip() != module
            or not LEAN_MODULE_RE.fullmatch(module)
            or (module != "Mathlib" and not module.startswith("Mathlib."))
        ):
            raise ValueError(f"ingredient import invalid: {module}")


def _target_sha256(
    *,
    task_id: str,
    theorem_name: str,
    type_expr: str,
    statement: str,
    imports: tuple[str, ...],
    lean_toolchain: str,
    mathlib_commit: str,
) -> str:
    return problem_target_sha256(
        Problem(
            id=task_id,
            theorem_name=theorem_name,
            type_expr=type_expr,
            split="ingredient",
            lean_toolchain=lean_toolchain,
            mathlib_rev=mathlib_commit,
            imports=imports,
            extra={"challenge_full": statement},
        )
    )


def select_fixture_ingredients(
    *,
    challenge_seed_sha256: str,
    difficulty_lane: DifficultyLane,
    selectors: tuple[RecipeSelector, ...],
    recipes: tuple[RecipeRule, ...],
    definitions: tuple[DefinitionIngredient, ...],
    facts: tuple[FactIngredient, ...],
    compatibility_edges: tuple[CompatibilityEdge, ...],
    bridges: tuple[BridgeRule, ...] = (),
    parameter_sets: dict[str, tuple[Any, ...]] | None = None,
) -> IngredientSelectionReceipt:
    for selector in _hash_ordered(
        (selector for selector in selectors if selector.difficulty_lane == difficulty_lane),
        seed=challenge_seed_sha256,
        label="selector",
        key=lambda item: item.selector_id,
    ):
        receipt = _selection_for_selector(
            selector,
            challenge_seed_sha256=challenge_seed_sha256,
            recipes=recipes,
            definitions=definitions,
            facts=facts,
            compatibility_edges=compatibility_edges,
            bridges=bridges,
            parameter_sets=parameter_sets or {},
        )
        if receipt is not None:
            return receipt
    raise ValueError(f"no compatible ingredient selection for difficulty lane: {difficulty_lane}")


def _selection_for_selector(
    selector: RecipeSelector,
    *,
    challenge_seed_sha256: str,
    recipes: tuple[RecipeRule, ...],
    definitions: tuple[DefinitionIngredient, ...],
    facts: tuple[FactIngredient, ...],
    compatibility_edges: tuple[CompatibilityEdge, ...],
    bridges: tuple[BridgeRule, ...],
    parameter_sets: dict[str, tuple[Any, ...]],
) -> IngredientSelectionReceipt | None:
    recipe_by_id = {recipe.recipe_id: recipe for recipe in recipes}
    for recipe_id in _hash_ordered(
        selector.recipe_ids,
        seed=challenge_seed_sha256,
        label=f"{selector.selector_id}:recipe",
        key=str,
    ):
        recipe = recipe_by_id.get(recipe_id)
        if recipe is None or not _recipe_matches_selector(recipe, selector, definitions):
            continue
        edges = tuple(
            edge
            for edge in compatibility_edges
            if edge.recipe_id == recipe.recipe_id and selector.difficulty_lane in edge.difficulty_lanes
        )
        if not edges:
            continue
        definition_ids = _selected_definition_ids(recipe, definitions, edges)
        if definition_ids is None:
            continue
        fact_ids = _selected_fact_ids(
            recipe,
            facts,
            edges,
            challenge_seed_sha256,
            min_dependency_depth=_selector_min_dependency_depth(selector),
        )
        if fact_ids is None:
            continue
        bridge_ids = _selected_bridge_ids(recipe, bridges, edges)
        if bridge_ids is None:
            continue
        return IngredientSelectionReceipt(
            selected_selector_id=selector.selector_id,
            selected_recipe_id=recipe.recipe_id,
            selected_definition_ids=definition_ids,
            selected_fact_ids=fact_ids,
            selected_bridge_ids=bridge_ids,
            selected_parameters=_selected_parameters(recipe, parameter_sets, challenge_seed_sha256),
            difficulty_lane=selector.difficulty_lane,
            selection_seed_sha256=challenge_seed_sha256,
        )
    return None


def _recipe_matches_selector(
    recipe: RecipeRule,
    selector: RecipeSelector,
    definitions: tuple[DefinitionIngredient, ...],
) -> bool:
    domains = selector.ingredient_filters.get("domains")
    if domains is not None:
        allowed = set(cast(list[str], domains))
        if not set(recipe.domains).issubset(allowed):
            return False
    max_simp_risk = selector.ingredient_filters.get("max_simp_risk")
    if max_simp_risk is None:
        return True
    if not isinstance(max_simp_risk, str) or max_simp_risk not in INGREDIENT_SIMP_RISK_ORDER:
        return False
    by_id = {definition.definition_id: definition for definition in definitions}
    return all(
        _definition_within_simp_risk(by_id.get(definition_id), max_simp_risk)
        for definition_id in recipe.required_definitions
    )


def _definition_within_simp_risk(definition: DefinitionIngredient | None, max_simp_risk: str) -> bool:
    if definition is None:
        return False
    simp_risk = definition.metadata.get("simp_risk", "high")
    return (
        isinstance(simp_risk, str)
        and simp_risk in INGREDIENT_SIMP_RISK_ORDER
        and INGREDIENT_SIMP_RISK_ORDER[simp_risk] <= INGREDIENT_SIMP_RISK_ORDER[max_simp_risk]
    )


def _selected_definition_ids(
    recipe: RecipeRule,
    definitions: tuple[DefinitionIngredient, ...],
    edges: tuple[CompatibilityEdge, ...],
) -> tuple[str, ...] | None:
    by_id = {definition.definition_id: definition for definition in definitions}
    allowed = {item for edge in edges for item in edge.allowed_definition_ids} or set(recipe.required_definitions)
    selected = []
    for definition_id in recipe.required_definitions:
        definition = by_id.get(definition_id)
        if (
            definition is None
            or definition_id not in allowed
            or definition.domain not in recipe.domains
            or not _definition_allowed_for_recipe(definition, recipe.recipe_id)
        ):
            return None
        selected.append(definition_id)
    return tuple(selected)


def _definition_allowed_for_recipe(definition: DefinitionIngredient, recipe_id: str) -> bool:
    allowed_recipes = definition.metadata.get("allowed_recipes")
    return allowed_recipes is None or (
        isinstance(allowed_recipes, list) and recipe_id in allowed_recipes
    )


def _selected_fact_ids(
    recipe: RecipeRule,
    facts: tuple[FactIngredient, ...],
    edges: tuple[CompatibilityEdge, ...],
    challenge_seed_sha256: str,
    *,
    min_dependency_depth: int | None = None,
) -> tuple[str, ...] | None:
    selected: list[str] = []
    allowed_domains = {domain for edge in edges for domain in edge.allowed_domains} or set(recipe.domains)
    patterns = tuple(pattern for edge in edges for pattern in edge.allowed_fact_patterns)
    for kind in recipe.required_fact_kinds:
        candidates = tuple(
            fact
            for fact in facts
            if fact.kind == kind
            and fact.fact_id not in selected
            and fact.domain in allowed_domains
            and fact.domain in recipe.domains
            and fact.metadata.get("usable_as_source_fact", True) is True
            and _fact_meets_min_dependency_depth(fact, min_dependency_depth)
            and _fact_matches_patterns(fact, patterns)
        )
        ordered = _hash_ordered(
            candidates,
            seed=challenge_seed_sha256,
            label=f"{recipe.recipe_id}:fact:{kind}:{len(selected)}",
            key=lambda item: item.fact_id,
        )
        if not ordered:
            return None
        selected.append(ordered[0].fact_id)
    return tuple(selected)


def _selector_min_dependency_depth(selector: RecipeSelector) -> int | None:
    value = selector.ingredient_filters.get("min_dependency_depth")
    return value if type(value) is int and value >= 0 else None


def _fact_meets_min_dependency_depth(fact: FactIngredient, min_dependency_depth: int | None) -> bool:
    if min_dependency_depth is None:
        return True
    dependency_depth = fact.metadata.get("dependency_depth", 0)
    return type(dependency_depth) is int and dependency_depth >= min_dependency_depth


def _selected_bridge_ids(
    recipe: RecipeRule,
    bridges: tuple[BridgeRule, ...],
    edges: tuple[CompatibilityEdge, ...],
) -> tuple[str, ...] | None:
    bridge_by_id = {bridge.bridge_id: bridge for bridge in bridges}
    selected = []
    for bridge_id in sorted({item for edge in edges for item in edge.bridge_ids}):
        bridge = bridge_by_id.get(bridge_id)
        if (
            bridge is None
            or recipe.recipe_id not in bridge.safe_recipes
            or not _bridge_matches_recipe_domains(bridge, recipe)
        ):
            return None
        selected.append(bridge_id)
    return tuple(selected)


def _bridge_matches_recipe_domains(bridge: BridgeRule, recipe: RecipeRule) -> bool:
    domains = set(recipe.domains)
    return bridge.from_domain in domains and bridge.to_domain in domains


def _selected_parameters(
    recipe: RecipeRule,
    parameter_sets: dict[str, tuple[Any, ...]],
    challenge_seed_sha256: str,
) -> dict[str, Any]:
    if recipe.parameter_rule == "none":
        return {}
    if recipe.parameter_rule == "finite_nat":
        values = parameter_sets.get("Nat")
        if not values:
            raise ValueError(f"ingredient recipe parameter set missing: recipe_rules:{recipe.recipe_id}:Nat")
        return {
            "Nat": _hash_ordered(
                values,
                seed=challenge_seed_sha256,
                label=f"{recipe.recipe_id}:parameter:Nat",
                key=lambda value: canonical_sha256({"value": value}),
            )[0]
        }
    if recipe.parameter_rule == "finite_bool":
        values = parameter_sets.get("Bool")
        if not values:
            raise ValueError(f"ingredient recipe parameter set missing: recipe_rules:{recipe.recipe_id}:Bool")
        return {
            "Bool": _hash_ordered(
                values,
                seed=challenge_seed_sha256,
                label=f"{recipe.recipe_id}:parameter:Bool",
                key=lambda value: canonical_sha256({"value": value}),
            )[0]
        }
    if recipe.parameter_rule == "finite_int":
        values = parameter_sets.get("Int")
        if not values:
            raise ValueError(f"ingredient recipe parameter set missing: recipe_rules:{recipe.recipe_id}:Int")
        return {
            "Int": _hash_ordered(
                values,
                seed=challenge_seed_sha256,
                label=f"{recipe.recipe_id}:parameter:Int",
                key=lambda value: canonical_sha256({"value": value}),
            )[0]
        }
    raise ValueError(
        f"ingredient recipe parameter rule unsupported: recipe_rules:{recipe.recipe_id}:{recipe.parameter_rule}"
    )


def _compatibility_edges_for_recipe(
    recipe: RecipeRule,
    definitions: tuple[DefinitionIngredient, ...],
    facts: tuple[FactIngredient, ...],
) -> tuple[CompatibilityEdge, ...]:
    definitions_by_id = {definition.definition_id: definition for definition in definitions}
    if not set(recipe.required_definitions).issubset(definitions_by_id):
        raise ValueError(f"ingredient compatibility recipe definition missing: {recipe.recipe_id}")
    patterns = tuple(sorted({definition_id.rsplit(".", 1)[-1] for definition_id in recipe.required_definitions}))
    if not patterns:
        raise ValueError(f"ingredient compatibility recipe fact pattern missing: {recipe.recipe_id}")
    recipe_domains = set(recipe.domains)
    for fact_kind in recipe.required_fact_kinds:
        if not any(
            fact.kind == fact_kind
            and fact.domain in recipe_domains
            and _fact_matches_patterns(fact, patterns)
            for fact in facts
        ):
            raise ValueError(f"ingredient compatibility recipe fact missing: {recipe.recipe_id}:{fact_kind}")
    allowed_domains = tuple(
        sorted(
            {
                definition.domain
                for definition in definitions_by_id.values()
                if definition.definition_id in recipe.required_definitions and definition.domain in recipe_domains
            }
            | {
                fact.domain
                for fact in facts
                if fact.kind in recipe.required_fact_kinds
                and fact.domain in recipe_domains
                and _fact_matches_patterns(fact, patterns)
            }
        )
    )
    if not allowed_domains:
        raise ValueError(f"ingredient compatibility recipe domain missing: {recipe.recipe_id}")
    return tuple(
        CompatibilityEdge(
            edge_id=f"{recipe.recipe_id}.{ingredient_class}.edge_v1",
            recipe_id=recipe.recipe_id,
            ingredient_class=ingredient_class,
            allowed_domains=allowed_domains,
            allowed_definition_ids=recipe.required_definitions,
            allowed_fact_patterns=patterns,
            difficulty_lanes=DIFFICULTY_LANES,
            certification_receipt_sha256=canonical_sha256(
                {
                    "allowed_definition_ids": list(recipe.required_definitions),
                    "allowed_domains": list(allowed_domains),
                    "allowed_fact_patterns": list(patterns),
                    "difficulty_lanes": list(DIFFICULTY_LANES),
                    "ingredient_class": ingredient_class,
                    "recipe_id": recipe.recipe_id,
                }
            ),
        )
        for ingredient_class in recipe.required_ingredient_classes
    )


def _fact_matches_patterns(fact: FactIngredient, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    return any(pattern in {fact.fact_id, fact.lean_name} or pattern in fact.fact_id for pattern in patterns)


def _hash_ordered(
    values: Iterable[HashOrderValue],
    *,
    seed: str,
    label: str,
    key: Callable[[HashOrderValue], str],
) -> list[HashOrderValue]:
    return sorted(
        values,
        key=lambda value: canonical_sha256({"seed": seed, "label": label, "key": key(value)}),
    )

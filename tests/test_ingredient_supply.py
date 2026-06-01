"""Ingredient-mode supply contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from bittensor_wallet import Keypair
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.problems.base import Problem
from lemma.submissions import LemmaSubmission, build_submission
from lemma.supply.ingredients import (
    DIFFICULTY_LANES,
    INGREDIENT_MANIFEST_COMPONENT_PATHS,
    INGREDIENT_RECIPE_ARTIFACT_PATHS,
    INGREDIENT_REPOSITORY_REPORT_PATHS,
    BridgeRule,
    CompatibilityEdge,
    DefinitionIngredient,
    FactIngredient,
    IngredientGateReceipt,
    IngredientGenerationReceipt,
    IngredientGenerationReceiptEnvelope,
    IngredientManifest,
    IngredientSelectionReceipt,
    IngredientTaskArtifactManifest,
    RecipeRule,
    RecipeSelector,
    Ss58IngredientEnvelopeSignatureVerifier,
    build_fixture_ingredient_registry,
    build_fixture_ingredient_task,
    build_ingredient_compatibility,
    build_ingredient_generation_receipt,
    canonical_json_bytes,
    canonical_sha256,
    expected_ingredient_generation_receipt_sha256,
    expected_ingredient_novelty_family_hash,
    ingredient_challenge_seed_sha256,
    ingredient_challenge_slot_seed_sha256,
    ingredient_difficulty_state_context,
    ingredient_generation_receipt_envelope,
    ingredient_generation_receipt_envelope_signing_payload,
    ingredient_generation_receipt_from_task,
    ingredient_manifest_bytes,
    ingredient_manifest_component_hashes,
    ingredient_manifest_component_schema_counts,
    ingredient_manifest_from_root,
    ingredient_novelty_family_hash,
    ingredient_novelty_gate_details,
    ingredient_recipe_artifact_hashes,
    ingredient_repository_report_hashes,
    ingredient_root_mathlib_commit,
    ingredient_shortcut_gate_receipt,
    ingredient_shortcut_tactic_gate_details,
    ingredient_shortcut_tactic_probe_script,
    ingredient_shortcut_tactics_for_selection,
    ingredient_soundness_witness_probe_script,
    ingredient_statement_gate_receipt,
    ingredient_triviality_gate_details,
    ingredient_triviality_probe_script,
    realize_ingredient_theorem_statement,
    select_fixture_ingredients,
    select_ingredient_receipt_from_root,
    text_sha256,
    verify_fixture_ingredient_selection,
    verify_ingredient_generation_receipt_artifact,
    verify_ingredient_generation_receipt_envelope,
    verify_ingredient_generation_receipt_envelope_quorum,
    verify_ingredient_task_against_root,
)
from lemma.supply.novelty import NOVELTY_CACHE_VERSION, novelty_cache_from_hashes, read_novelty_cache, statement_hash
from lemma.task_supply import write_registry
from lemma.tasks import LemmaTask, TaskRegistry, problem_target_sha256
from lemma.validator import active_epoch_seed, active_tasks_for_validation, task_registry_for_validation, validate_once
from pydantic import ValidationError


def _sha(char: str = "a") -> str:
    return char * 64


def _manifest_json(*, mathlib_commit: str = "abc123", recipe_bundle_sha256: str = "2" * 64) -> str:
    return (
        IngredientManifest(
            schema_version=1,
            mathlib_commit=mathlib_commit,
            lemma_corpus_snapshot_sha256=_sha("f"),
            definitions_sha256=_sha("a"),
            facts_sha256=_sha("1"),
            source_theorems_sha256=_sha("3"),
            source_lemmas_sha256=_sha("4"),
            compatibility_graph_sha256=_sha("5"),
            source_compatibility_sha256=_sha("6"),
            definition_compatibility_sha256=_sha("7"),
            bridge_catalog_sha256=_sha("8"),
            recipe_selectors_sha256=_sha("9"),
            recipe_bundle_sha256=recipe_bundle_sha256,
            difficulty_ladder_sha256=_sha("a"),
            difficulty_retarget_sha256=_sha("b"),
            novelty_policy_sha256=_sha("c"),
            shortcut_policy_sha256=_sha("d"),
            reserve_selector_policy_sha256=_sha("e"),
        ).model_dump_json()
        + "\n"
    )


def test_ingredient_manifest_rejects_created_at_side_channel() -> None:
    payload = json.loads(_manifest_json())
    payload["created_at"] = "2026-05-31T00:00:00Z"

    with pytest.raises(ValidationError, match="created_at"):
        IngredientManifest.model_validate(payload)


def test_ingredient_manifest_rejects_bool_schema_version() -> None:
    payload = json.loads(_manifest_json())
    payload["schema_version"] = True

    with pytest.raises(ValidationError, match="expected exact integer"):
        IngredientManifest.model_validate(payload)


def test_ingredient_manifest_rejects_non_commit_mathlib_pin() -> None:
    payload = json.loads(_manifest_json())
    payload["mathlib_commit"] = "private/path"

    with pytest.raises(ValidationError, match="mathlib_commit"):
        IngredientManifest.model_validate(payload)


def test_ingredient_manifest_rejects_placeholder_mathlib_pin() -> None:
    payload = json.loads(_manifest_json())
    payload["mathlib_commit"] = "0" * 6

    with pytest.raises(ValidationError, match="ingredient manifest mathlib commit placeholder"):
        IngredientManifest.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    (
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
    ),
)
def test_ingredient_manifest_rejects_placeholder_hashes(field: str) -> None:
    payload = json.loads(_manifest_json())
    payload[field] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient manifest sha256 placeholder"):
        IngredientManifest.model_validate(payload)


def _selection() -> IngredientSelectionReceipt:
    return IngredientSelectionReceipt(
        selected_selector_id="hard_selector",
        selected_recipe_id="list_length_v1",
        selected_definition_ids=("List.length",),
        selected_fact_ids=("List.length_map",),
        selected_bridge_ids=("List.length_to_Nat",),
        selected_parameters={"Nat": "2"},
        difficulty_lane="hard",
        selection_seed_sha256=_sha("1"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("selected_selector_id", "private/path"),
        ("selected_recipe_id", "private/path"),
        ("selected_definition_ids", ("private/path",)),
        ("selected_fact_ids", ("private/path",)),
        ("selected_bridge_ids", ("private/path",)),
    ),
)
def test_ingredient_selection_receipt_rejects_non_public_selected_ids(
    field: str,
    value: object,
) -> None:
    payload = _selection().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match="ingredient selection receipt"):
        IngredientSelectionReceipt.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "selected_definition_ids",
            ("List.length", "List.length"),
            "ingredient selection receipt selected_definition_ids duplicate: List.length",
        ),
        (
            "selected_fact_ids",
            ("List.length_map", "List.length_map"),
            "ingredient selection receipt selected_fact_ids duplicate: List.length_map",
        ),
        (
            "selected_bridge_ids",
            ("List.length_to_Nat", "List.length_to_Nat"),
            "ingredient selection receipt selected_bridge_ids duplicate: List.length_to_Nat",
        ),
    ),
)
def test_ingredient_selection_receipt_rejects_duplicate_selected_ids(
    field: str,
    value: tuple[str, ...],
    message: str,
) -> None:
    payload = _selection().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        IngredientSelectionReceipt.model_validate(payload)


@pytest.mark.parametrize(
    "selected_parameters",
    (
        {"private/path": "2"},
        {"Nat": 2},
        {"Nat": "02"},
        {"Int": "01"},
        {"Bool": "yes"},
        {"Nat": "2", "Bool": "true"},
    ),
)
def test_ingredient_selection_receipt_rejects_noncanonical_selected_parameters(
    selected_parameters: object,
) -> None:
    payload = _selection().model_dump(mode="json")
    payload["selected_parameters"] = selected_parameters

    with pytest.raises(ValidationError, match="ingredient selection receipt selected parameters invalid"):
        IngredientSelectionReceipt.model_validate(payload)


def test_ingredient_selection_receipt_rejects_placeholder_seed() -> None:
    payload = _selection().model_dump(mode="json")
    payload["selection_seed_sha256"] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient selection receipt seed placeholder"):
        IngredientSelectionReceipt.model_validate(payload)


def _recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length",),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _nat_add_zero_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="nat_add_zero_v1",
        version=1,
        domains=("Nat",),
        required_ingredient_classes=("nat_definition", "nat_fact"),
        required_definitions=("Nat.add",),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/nat_add_zero.lean",
        shortcut_checks=("source_oracle",),
    )


def _nat_mul_one_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="nat_mul_one_v1",
        version=1,
        domains=("Nat",),
        required_ingredient_classes=("nat_definition", "nat_fact"),
        required_definitions=("Nat.mul",),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/nat_mul_one.lean",
        shortcut_checks=("source_oracle",),
    )


def _append_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_append_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.append", "List.length"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_append_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _dedup_pair_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_dedup_pair_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.dedup",),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_dedup_pair_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _drop_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_drop_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.drop", "List.length"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_drop_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _filter_true_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_filter_true_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.filter", "List.length"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_filter_true_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _reverse_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_reverse_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length", "List.reverse"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_reverse_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _map_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_map_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length", "List.map"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_map_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _take_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_take_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length", "List.take"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_take_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _range_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_range_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length", "List.range"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_range_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _zip_length_recipe() -> RecipeRule:
    return RecipeRule(
        recipe_id="list_zip_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length", "List.zip"),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_zip_length.lean",
        shortcut_checks=("source_oracle",),
    )


def _selector() -> RecipeSelector:
    return RecipeSelector(
        selector_id="hard_list_length_selector_v1",
        difficulty_lane="hard",
        recipe_ids=("list_length_v1",),
        ingredient_filters={"domains": ["List", "Nat"]},
    )


def _definition(definition_id: str = "List.length") -> DefinitionIngredient:
    return DefinitionIngredient(
        definition_id=definition_id,
        lean_name=definition_id,
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib.Data.List.Basic",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    )


def _nat_definition(definition_id: str = "Nat.add") -> DefinitionIngredient:
    return DefinitionIngredient(
        definition_id=definition_id,
        lean_name=definition_id,
        domain="Nat",
        type_signature="Nat -> Nat -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/Nat/Basic.lean",
        mathlib_commit="abc123",
    )


def _fact(fact_id: str = "List.length_map", kind: str = "lemma") -> FactIngredient:
    return FactIngredient(
        fact_id=fact_id,
        lean_name=fact_id,
        kind=kind,
        domain="List",
        type_expr="...",
        imports=("Mathlib.Data.List.Basic",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
        difficulty_hint=1,
    )


def _nat_fact(fact_id: str = "Nat.add_zero", type_expr: str = "Nat.add 0 0 = 0") -> FactIngredient:
    return FactIngredient(
        fact_id=fact_id,
        lean_name=fact_id,
        kind="lemma",
        domain="Nat",
        type_expr=type_expr,
        imports=("Mathlib",),
        source_path="Mathlib/Data/Nat/Basic.lean",
        mathlib_commit="abc123",
        difficulty_hint=1,
    )


def test_fact_ingredient_rejects_bool_difficulty_hint() -> None:
    payload = _fact().model_dump(mode="json")
    payload["difficulty_hint"] = True

    with pytest.raises(ValidationError, match="expected exact integer"):
        FactIngredient.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload", "message"),
    (
        (
            DefinitionIngredient,
            _definition().model_dump(mode="json"),
            "ingredient definition mathlib commit placeholder",
        ),
        (FactIngredient, _fact().model_dump(mode="json"), "ingredient fact mathlib commit placeholder"),
    ),
)
def test_raw_ingredient_rows_reject_placeholder_mathlib_commit(
    model: type[DefinitionIngredient] | type[FactIngredient],
    payload: dict[str, object],
    message: str,
) -> None:
    payload["mathlib_commit"] = "0" * 6

    with pytest.raises(ValidationError, match=message):
        model.model_validate(payload)


@pytest.mark.parametrize("field", ("version", "difficulty_delta"))
def test_recipe_rule_rejects_bool_public_int_fields(field: str) -> None:
    payload = _recipe().model_dump(mode="json")
    payload[field] = True

    with pytest.raises(ValidationError, match="expected exact integer"):
        RecipeRule.model_validate(payload)


def _edge(*, bridge_ids: tuple[str, ...] = ("List.length_to_Nat",)) -> CompatibilityEdge:
    return CompatibilityEdge(
        edge_id="list_length_v1.edge.length",
        recipe_id="list_length_v1",
        ingredient_class="list_fact",
        allowed_domains=("List",),
        allowed_definition_ids=("List.length",),
        allowed_fact_patterns=("length",),
        bridge_ids=bridge_ids,
        difficulty_lanes=("hard",),
        certification_receipt_sha256=_sha("9"),
    )


def _write_ingredient_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def _write_ingredient_jsonl(path: Path, *rows: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))


def _difficulty_state_jsonl(*rows: dict[str, object]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def test_ingredient_difficulty_state_rejects_noncanonical_row() -> None:
    raw = json.dumps({"tempo": 42, "difficulty_lane": "hard"}).encode() + b"\n"

    with pytest.raises(ValueError, match="difficulty state row noncanonical"):
        ingredient_difficulty_state_context(raw, tempo=42)


def test_ingredient_difficulty_state_rejects_noncanonical_file_shape() -> None:
    raw = canonical_json_bytes({"tempo": 42, "difficulty_lane": "hard"})

    with pytest.raises(ValueError, match="difficulty state JSONL noncanonical"):
        ingredient_difficulty_state_context(raw, tempo=42)


def test_ingredient_difficulty_state_rejects_duplicate_tempos() -> None:
    raw = _difficulty_state_jsonl(
        {"tempo": 41, "difficulty_lane": "easy"},
        {"tempo": 41, "difficulty_lane": "hard"},
    )

    with pytest.raises(ValueError, match="difficulty state tempo duplicated"):
        ingredient_difficulty_state_context(raw, tempo=41)


def test_ingredient_difficulty_state_rejects_unsorted_tempos() -> None:
    raw = _difficulty_state_jsonl(
        {"tempo": 42, "difficulty_lane": "hard"},
        {"tempo": 41, "difficulty_lane": "easy"},
    )

    with pytest.raises(ValueError, match="difficulty state tempo order invalid"):
        ingredient_difficulty_state_context(raw, tempo=42)


def _write_selection_ingredient_repo(root: Path) -> tuple[FactIngredient, ...]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mathlib_commit.txt").write_text("abc123\n", encoding="utf-8")
    facts = (_fact("List.length_map"), _fact("List.length_reverse"))
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"], _definition())
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], *facts)
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["source_theorems_sha256"])
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["source_lemmas_sha256"])
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["compatibility_graph_sha256"], _edge())
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["source_compatibility_sha256"])
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definition_compatibility_sha256"])
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["bridge_catalog_sha256"],
        BridgeRule(
            bridge_id="List.length_to_Nat",
            from_domain="List",
            to_domain="Nat",
            safe_recipes=("list_length_v1",),
        ),
    )
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"], _selector())
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": ["list_length_v1"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_retarget_sha256"],
        {
            "schema_version": 1,
            "retarget_mode": "manual_state_v1",
            "state_schema": "tempo_lane_v1",
        },
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["reserve_selector_policy_sha256"],
        {
            "schema_version": 1,
            "reserve_enabled": True,
            "selection_method": "hash_order_first_eligible",
        },
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_ladder_sha256"],
        {"schema_version": 1, "difficulty_lanes": list(DIFFICULTY_LANES)},
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"],
        {
            "schema_version": 1,
            "novelty_cache_version": NOVELTY_CACHE_VERSION,
            "supported_checks": ["theorem_type_cache", "selection_family_cache"],
        },
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json")]},
    )
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {"Nat": ["2", "3"]})
    template = root / "recipes" / "soundness_templates" / "list_length.lean"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        "import Mathlib\n\n"
        "theorem list_length_soundness (n : Nat) : List.length (List.replicate n 0) = n := by\n"
        "  simp\n",
        encoding="utf-8",
    )
    report_counts = {
        "definition_count": 1,
        "fact_count": len(facts),
        "compatibility_edge_count": 1,
        "recipe_count": 1,
    }
    _write_ingredient_json(
        root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"],
        {
            "schema_version": 1,
            "mathlib_commit": "abc123",
            "source_row_count": report_counts["definition_count"] + report_counts["fact_count"],
            "definition_count": report_counts["definition_count"],
            "fact_count": report_counts["fact_count"],
            "source_license_counts": {"Apache-2.0": report_counts["definition_count"] + report_counts["fact_count"]},
        },
    )
    _write_ingredient_json(
        root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"],
        {
            **report_counts,
            "difficulty_lane_coverage": {"hard": 1},
            "bridge_coverage": {"List.length_to_Nat": 1},
            "estimated_theorem_space_size": 4,
            "shortcut_risk_distribution": {"paid_eligible": 1},
            "reserve_selector_health": {"ready": True},
        },
    )
    return facts


def _write_selection_repo_fact_count(root: Path, fact_count: int) -> None:
    extraction_report = json.loads(
        (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text(encoding="utf-8")
    )
    extraction_report["source_row_count"] = extraction_report["definition_count"] + fact_count
    extraction_report["fact_count"] = fact_count
    extraction_report["source_license_counts"] = {"Apache-2.0": extraction_report["source_row_count"]}
    _write_ingredient_json(root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"], extraction_report)
    quality_report = json.loads(
        (root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).read_text(encoding="utf-8")
    )
    quality_report["fact_count"] = fact_count
    _write_ingredient_json(root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"], quality_report)


def test_ingredient_manifest_from_root_rejects_placeholder_mathlib_commit(tmp_path: Path) -> None:
    root = tmp_path / "ingredients"
    _write_selection_ingredient_repo(root)
    (root / "mathlib_commit.txt").write_text("000000\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ingredient mathlib commit placeholder"):
        ingredient_manifest_from_root(root, lemma_corpus_snapshot_sha256=_sha("f"))


def test_ingredient_root_mathlib_commit_rejects_symlink_pin(tmp_path: Path) -> None:
    root = tmp_path / "ingredients"
    _write_selection_ingredient_repo(root)
    path = root / "mathlib_commit.txt"
    external_path = tmp_path / "mathlib_commit.external.txt"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(ValueError, match="ingredient mathlib commit path invalid"):
        ingredient_root_mathlib_commit(root)


def test_select_ingredient_receipt_from_root_rejects_symlink_mathlib_pin(tmp_path: Path) -> None:
    root = tmp_path / "ingredients"
    _write_selection_ingredient_repo(root)
    path = root / "mathlib_commit.txt"
    external_path = tmp_path / "mathlib_commit.external.txt"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(ValueError, match="ingredient mathlib commit path invalid"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("difficulty_ladder_sha256", "ingredient difficulty ladder invalid"),
        ("recipe_bundle_sha256", "ingredient recipe bundle invalid"),
        ("difficulty_retarget_sha256", "ingredient difficulty retarget policy invalid"),
        ("shortcut_policy_sha256", "ingredient shortcut policy invalid"),
        ("novelty_policy_sha256", "ingredient novelty policy invalid"),
        ("reserve_selector_policy_sha256", "ingredient reserve selector policy invalid"),
    ),
)
def test_public_json_artifacts_reject_bool_schema_version(
    tmp_path: Path, field: str, message: str
) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = True
    _write_ingredient_json(path, payload)

    with pytest.raises(ValueError, match=message):
        ingredient_manifest_component_schema_counts(root, mathlib_commit="abc123")


def test_ingredient_manifest_component_hashes_rejects_symlink_component(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    external_path = tmp_path / "definitions.external.jsonl"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(ValueError, match="ingredient component path invalid: definitions_sha256"):
        ingredient_manifest_component_hashes(root)


def test_ingredient_component_schema_counts_rejects_symlink_jsonl_component(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    external_path = tmp_path / "definitions.external.jsonl"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(ValueError, match="ingredient component path invalid: definitions_sha256"):
        ingredient_manifest_component_schema_counts(root, mathlib_commit="abc123")


def test_select_ingredient_receipt_from_root_rejects_symlink_jsonl_component(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    external_path = tmp_path / "definitions.external.jsonl"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(ValueError, match="ingredient component path invalid: definitions_sha256"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


@pytest.mark.parametrize("artifact_id", tuple(INGREDIENT_RECIPE_ARTIFACT_PATHS))
def test_ingredient_recipe_artifact_hashes_rejects_symlink_artifact(
    tmp_path: Path, artifact_id: str
) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    path = root / INGREDIENT_RECIPE_ARTIFACT_PATHS[artifact_id]
    external_path = tmp_path / f"{artifact_id}.external.json"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(ValueError, match=f"ingredient recipe artifact path invalid: {artifact_id}"):
        ingredient_recipe_artifact_hashes(root)


def test_ingredient_recipe_artifact_hashes_rejects_symlink_soundness_template(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    path = root / "recipes" / "soundness_templates" / "list_length.lean"
    external_path = tmp_path / "list_length.external.lean"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(
        ValueError,
        match="ingredient recipe soundness template unexpected: soundness_templates/list_length.lean",
    ):
        ingredient_recipe_artifact_hashes(root)


@pytest.mark.parametrize("report_id", tuple(INGREDIENT_REPOSITORY_REPORT_PATHS))
def test_ingredient_repository_report_hashes_rejects_symlink_report(
    tmp_path: Path, report_id: str
) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    component_counts = ingredient_manifest_component_schema_counts(root, mathlib_commit="abc123")
    path = root / INGREDIENT_REPOSITORY_REPORT_PATHS[report_id]
    external_path = tmp_path / f"{report_id}.external.json"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    with pytest.raises(ValueError, match=f"ingredient report path invalid: {report_id}"):
        ingredient_repository_report_hashes(
            root,
            component_schema_counts=component_counts,
            mathlib_commit="abc123",
        )


def test_ingredient_component_schema_counts_rejects_cross_catalog_fact_id_duplicate(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["source_lemmas_sha256"], _fact())

    with pytest.raises(ValueError, match="ingredient fact catalog id duplicate: List.length_map"):
        ingredient_manifest_component_schema_counts(root, mathlib_commit="abc123")


def test_extraction_report_rejects_bool_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    component_counts = ingredient_manifest_component_schema_counts(root, mathlib_commit="abc123")
    path = root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = True
    _write_ingredient_json(path, payload)

    with pytest.raises(ValueError, match="ingredient extraction report invalid: schema_version"):
        ingredient_repository_report_hashes(
            root,
            component_schema_counts=component_counts,
            mathlib_commit="abc123",
        )


def _target_sha256(*, theorem_name: str, type_expr: str, statement: str) -> str:
    return problem_target_sha256(
        Problem(
            id="lemma.ingredient.list_length",
            theorem_name=theorem_name,
            type_expr=type_expr,
            split="ingredient",
            lean_toolchain="leanprover/lean4:v4.30.0-rc2",
            mathlib_rev="abc123",
            extra={"challenge_full": statement},
        )
    )


def _generation_gate_receipt(
    *,
    receipt_kind: str,
    active_target_sha256: str,
    theorem_statement_sha256: str,
    ingredient_manifest_sha256: str = _sha("1"),
    active_task_id: str = "lemma.ingredient.list_length",
    selection_receipt_sha256: str | None = None,
) -> IngredientGateReceipt:
    return IngredientGateReceipt(
        schema_version=1,
        receipt_kind=receipt_kind,
        active_task_id=active_task_id,
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        ingredient_manifest_sha256=ingredient_manifest_sha256,
        selection_receipt_sha256=selection_receipt_sha256 or canonical_sha256(_selection()),
        status="passed",
        runner=f"fixture-{receipt_kind.replace('_', '-')}",
        checks=("metadata_bound",),
    )


def _receipt_for_statement(statement: str) -> IngredientGenerationReceipt:
    active_target_sha256 = _target_sha256(
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
    )
    theorem_statement_sha256 = text_sha256(statement)
    return build_ingredient_generation_receipt(
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit="abc123",
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
        selection=_selection(),
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=active_target_sha256,
        theorem_statement=statement,
        gate_receipt=_generation_gate_receipt(
            receipt_kind="statement_gate",
            active_target_sha256=active_target_sha256,
            theorem_statement_sha256=theorem_statement_sha256,
        ),
        shortcut_receipt=_generation_gate_receipt(
            receipt_kind="shortcut_gate",
            active_target_sha256=active_target_sha256,
            theorem_statement_sha256=theorem_statement_sha256,
        ),
    )


def _ingredient_validation_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[LemmaSettings, TaskRegistry, TaskRegistry, LemmaTask]:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(_manifest_json(recipe_bundle_sha256=_sha("2")), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    manifest_sha256 = text_sha256(manifest.read_text(encoding="utf-8"))
    difficulty_state_sha256 = text_sha256(difficulty_state.read_text(encoding="utf-8"))
    seed_settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_supply_mode="ingredient",
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
        netuid=467,
    )
    epoch_seed = active_epoch_seed(seed_settings, tempo=42, epoch_randomness="epoch-seed")
    registry = build_fixture_ingredient_registry(
        netuid=467,
        tempo=42,
        epoch_seed=epoch_seed,
        ingredient_manifest_sha256=manifest_sha256,
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit="abc123",
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=difficulty_state_sha256,
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(),),
        bridges=(
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        parameter_sets={"Nat": ("2", "3")},
        theorem_name="generated_list_length",
        type_expr="True",
        statement="theorem generated_list_length : True := by\n  sorry",
        active_task_id="lemma.ingredient.list_length",
        gate_receipt_sha256=_sha("6"),
        shortcut_receipt_sha256=_sha("7"),
    )
    cache_dir = tmp_path / "active-cache"
    write_registry(registry.tasks, cache_dir / "tempo-42.registry.json")
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_supply_mode="ingredient",
        active_task_count=1,
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        active_registry_cache_dir=cache_dir,
        ingredient_manifest_json=manifest,
        ingredient_manifest_sha256_expected=manifest_sha256,
        ingredient_repo_commit="abc123",
        ingredient_recipe_bundle_sha256_expected=_sha("2"),
        ingredient_difficulty_state_jsonl=difficulty_state,
        netuid=467,
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
        lean_use_docker=False,
    )
    monkeypatch.setattr("lemma.validator.resolve_active_epoch_randomness", lambda settings, *, tempo: "epoch-seed")
    loaded = task_registry_for_validation(settings, tempo=42)
    return settings, registry, loaded, loaded.tasks[0]


def _ingredient_proof() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem generated_list_length : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def _committed_ingredient_submission(
    task: LemmaTask,
    *,
    solver_hotkey: str,
    commit_block: int,
    commit_extrinsic_hash: str,
    commit_extrinsic_index: int | None = None,
    commit_event_index: int | None = None,
    proof_script: str | None = None,
) -> LemmaSubmission:
    base = build_submission(task, solver_hotkey=solver_hotkey, proof_script=proof_script or _ingredient_proof())
    return LemmaSubmission.model_validate(
        {
            **base.model_dump(),
            "timelock_ciphertext": f"ciphertext-{solver_hotkey}",
            "drand_round": 77,
            "commit_block": commit_block,
            "commit_extrinsic_index": commit_extrinsic_index,
            "commit_event_index": commit_event_index,
            "commit_extrinsic_hash": commit_extrinsic_hash,
            "signature_payload_sha256": "",
        }
    )


def test_ingredient_models_accept_minimal_valid_rows() -> None:
    manifest = IngredientManifest(
        schema_version=1,
        mathlib_commit="abc123",
        definitions_sha256=_sha("1"),
        facts_sha256=_sha("2"),
        source_theorems_sha256=_sha("3"),
        source_lemmas_sha256=_sha("4"),
        compatibility_graph_sha256=_sha("3"),
        source_compatibility_sha256=_sha("5"),
        definition_compatibility_sha256=_sha("6"),
        bridge_catalog_sha256=_sha("4"),
        recipe_selectors_sha256=_sha("7"),
        recipe_bundle_sha256=_sha("5"),
        difficulty_ladder_sha256=_sha("8"),
        difficulty_retarget_sha256=_sha("9"),
        novelty_policy_sha256=_sha("7"),
        shortcut_policy_sha256=_sha("8"),
        reserve_selector_policy_sha256=_sha("9"),
    )
    definition = DefinitionIngredient(
        definition_id="List.length",
        lean_name="List.length",
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib.Data.List.Basic",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    )
    fact = FactIngredient(
        fact_id="List.length_map",
        lean_name="List.length_map",
        kind="lemma",
        domain="List",
        type_expr="...",
        imports=("Mathlib.Data.List.Basic",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
        difficulty_hint=1,
    )
    recipe = RecipeRule(
        recipe_id="list_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length",),
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template="soundness_templates/list_length.lean",
        shortcut_checks=("source_oracle",),
    )
    selector = RecipeSelector(
        selector_id="hard_list_length_selector_v1",
        difficulty_lane="hard",
        recipe_ids=("list_length_v1",),
        ingredient_filters={"domains": ["List", "Nat"]},
    )
    bridge = BridgeRule(
        bridge_id="List.length_to_Nat",
        from_domain="List",
        to_domain="Nat",
        safe_recipes=("list_length_v1",),
    )
    edge = CompatibilityEdge(
        edge_id="list_length_v1.edge.length",
        recipe_id=recipe.recipe_id,
        ingredient_class="list_fact",
        allowed_domains=("List",),
        difficulty_lanes=("hard",),
        certification_receipt_sha256=_sha("9"),
    )

    assert manifest.schema_version == 1
    assert definition.definition_id == "List.length"
    assert fact.kind == "lemma"
    assert selector.selection_method == "hash_order_first_eligible"
    assert bridge.safe_recipes == ("list_length_v1",)
    assert edge.difficulty_lanes == ("hard",)


def test_generation_receipt_hash_is_canonical() -> None:
    receipt = IngredientGenerationReceipt(
        schema_version=1,
        tempo=42,
        active_K=1,
        epoch_seed_sha256=_sha("a"),
        ingredient_manifest_sha256=_sha("1"),
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit="abc123",
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
        selection=_selection(),
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        gate_receipt_sha256=_sha("6"),
        shortcut_receipt_sha256=_sha("7"),
    )
    reordered = receipt.model_dump(mode="json")
    reordered = {key: reordered[key] for key in reversed(reordered)}

    assert canonical_sha256(receipt) == canonical_sha256(reordered)


def test_ingredient_challenge_seed_uses_only_public_epoch_inputs() -> None:
    seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )

    assert seed == ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )
    assert seed != ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=43,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )
    assert seed != ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="different-epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("netuid", True, "ingredient challenge seed public integer invalid"),
        ("tempo", -1, "ingredient challenge seed public integer invalid"),
        ("epoch_seed", "", "ingredient challenge seed epoch seed invalid"),
        ("epoch_seed", " epoch-seed", "ingredient challenge seed epoch seed invalid"),
        (
            "ingredient_manifest_sha256",
            _sha("0"),
            "ingredient challenge seed manifest sha256 placeholder",
        ),
        (
            "recipe_bundle_sha256",
            "not-a-sha",
            "ingredient challenge seed recipe bundle sha256 invalid",
        ),
        (
            "difficulty_state_sha256",
            _sha("0"),
            "ingredient challenge seed difficulty state sha256 placeholder",
        ),
    ),
)
def test_ingredient_challenge_seed_rejects_invalid_public_inputs(
    field: str,
    value: object,
    message: str,
) -> None:
    kwargs = {
        "netuid": 467,
        "tempo": 42,
        "epoch_seed": "epoch-seed",
        "ingredient_manifest_sha256": _sha("1"),
        "recipe_bundle_sha256": _sha("2"),
        "difficulty_state_sha256": _sha("3"),
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        ingredient_challenge_seed_sha256(**kwargs)


def test_ingredient_challenge_slot_seed_is_slot_indexed_for_dynamic_k() -> None:
    challenge_seed = _sha("1")

    assert (
        ingredient_challenge_slot_seed_sha256(
            challenge_seed_sha256=challenge_seed,
            queue_position=0,
            active_K=1,
        )
        == challenge_seed
    )
    assert ingredient_challenge_slot_seed_sha256(
        challenge_seed_sha256=challenge_seed,
        queue_position=0,
        active_K=2,
    ) != ingredient_challenge_slot_seed_sha256(
        challenge_seed_sha256=challenge_seed,
        queue_position=1,
        active_K=2,
    )


@pytest.mark.parametrize(
    ("queue_position", "active_k"),
    ((True, 2), (-1, 2), (2, 2), (0, 0), (0, True)),
)
def test_ingredient_challenge_slot_seed_rejects_invalid_public_inputs(
    queue_position: object,
    active_k: object,
) -> None:
    with pytest.raises(ValueError, match="ingredient challenge slot seed public integer invalid"):
        ingredient_challenge_slot_seed_sha256(
            challenge_seed_sha256=_sha("1"),
            queue_position=queue_position,
            active_K=active_k,
        )


def test_corpus_snapshot_changes_challenge_seed_through_manifest_hash() -> None:
    first_manifest = IngredientManifest(
        schema_version=1,
        mathlib_commit="abc123",
        lemma_corpus_snapshot_sha256=_sha("f"),
        definitions_sha256=_sha("a"),
        facts_sha256=_sha("1"),
        source_theorems_sha256=_sha("3"),
        source_lemmas_sha256=_sha("4"),
        compatibility_graph_sha256=_sha("5"),
        source_compatibility_sha256=_sha("6"),
        definition_compatibility_sha256=_sha("7"),
        bridge_catalog_sha256=_sha("8"),
        recipe_selectors_sha256=_sha("9"),
        recipe_bundle_sha256=_sha("2"),
        difficulty_ladder_sha256=_sha("a"),
        difficulty_retarget_sha256=_sha("b"),
        novelty_policy_sha256=_sha("c"),
        shortcut_policy_sha256=_sha("d"),
        reserve_selector_policy_sha256=_sha("e"),
    )
    second_manifest = first_manifest.model_copy(update={"lemma_corpus_snapshot_sha256": _sha("e")})

    first_seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=canonical_sha256(first_manifest),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )
    second_seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=canonical_sha256(second_manifest),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )

    assert first_seed != second_seed


def test_ingredient_generation_receipt_rejects_placeholder_epoch_seed() -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload["epoch_seed_sha256"] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient generation receipt epoch seed placeholder"):
        IngredientGenerationReceipt.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    (
        "active_target_sha256",
        "difficulty_state_sha256",
        "gate_receipt_sha256",
        "ingredient_manifest_sha256",
        "lemma_corpus_snapshot_sha256",
        "recipe_bundle_sha256",
        "shortcut_receipt_sha256",
        "theorem_statement_sha256",
    ),
)
def test_ingredient_generation_receipt_rejects_placeholder_hashes(field: str) -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload[field] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient generation receipt sha256 placeholder"):
        IngredientGenerationReceipt.model_validate(payload)


def test_ingredient_novelty_family_hash_uses_public_selection_family() -> None:
    selection = _selection()

    family_hash = ingredient_novelty_family_hash(selection)

    assert family_hash == ingredient_novelty_family_hash(selection)
    assert family_hash != ingredient_novelty_family_hash(
        selection.model_copy(update={"selected_parameters": {"Nat": "3"}})
    )
    assert family_hash == ingredient_novelty_family_hash(
        selection.model_copy(update={"selection_seed_sha256": _sha("0")})
    )


def test_strict_novelty_cache_reads_selection_family_rows(tmp_path: Path) -> None:
    family_hash = ingredient_novelty_family_hash(_selection())
    path = tmp_path / "novelty-cache.jsonl"
    path.write_bytes(
        canonical_json_bytes({"statement_hash": statement_hash("True")})
        + b"\n"
        + canonical_json_bytes({"novelty_family_hash": family_hash})
        + b"\n"
    )

    cache = read_novelty_cache(path, strict_statement_hash_rows=True)

    assert cache.contains(statement_hash("True"))
    assert cache.contains_family(family_hash)


def test_novelty_cache_rejects_symlink_path(tmp_path: Path) -> None:
    path = tmp_path / "novelty-cache.jsonl"
    path.write_bytes(canonical_json_bytes({"statement_hash": statement_hash("True")}) + b"\n")
    symlink_path = tmp_path / "novelty-cache-link.jsonl"
    symlink_path.symlink_to(path)

    with pytest.raises(ValueError, match="novelty cache path invalid"):
        read_novelty_cache(symlink_path, strict_statement_hash_rows=True)


def test_build_generation_receipt_hashes_epoch_seed_and_statement() -> None:
    statement = "theorem generated_list_length : True := by\n  trivial"
    theorem_statement_sha256 = text_sha256(statement)
    gate_receipt = _generation_gate_receipt(
        receipt_kind="statement_gate",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=theorem_statement_sha256,
    )
    shortcut_receipt = _generation_gate_receipt(
        receipt_kind="shortcut_gate",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=theorem_statement_sha256,
    )
    receipt = build_ingredient_generation_receipt(
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit="abc123",
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
        selection=_selection(),
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement=statement,
        gate_receipt=gate_receipt,
        shortcut_receipt=shortcut_receipt,
    )

    assert receipt.active_K == 1
    assert receipt.epoch_seed_sha256 == text_sha256("epoch-seed")
    assert receipt.lemma_corpus_snapshot_sha256 == _sha("f")
    assert receipt.theorem_statement_sha256 == theorem_statement_sha256
    assert receipt.selection == _selection()
    assert receipt.gate_receipt_sha256 == canonical_sha256(gate_receipt)
    assert receipt.shortcut_receipt_sha256 == canonical_sha256(shortcut_receipt)


def test_ingredient_task_round_trips_dynamic_slot_metadata() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement).model_copy(update={"active_K": 3})

    task = build_fixture_ingredient_task(
        receipt=receipt,
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
        queue_position=2,
        queue_depth=1,
        frontier_depth=1,
    )

    assert task.queue_position == 2
    assert task.queue_depth == 1
    assert task.frontier_depth == 1
    assert ingredient_generation_receipt_from_task(task) == receipt


@pytest.mark.parametrize(
    ("receipt_kind", "message"),
    (
        ("shortcut_gate", "ingredient generation receipt gate receipt mismatch"),
        ("statement_gate", "ingredient generation receipt shortcut receipt mismatch"),
    ),
)
def test_build_generation_receipt_rejects_child_receipt_kind_drift(
    receipt_kind: str,
    message: str,
) -> None:
    statement = "theorem generated_list_length : True := by\n  trivial"
    theorem_statement_sha256 = text_sha256(statement)
    gate_receipt = _generation_gate_receipt(
        receipt_kind=receipt_kind,
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=theorem_statement_sha256,
    )
    shortcut_receipt = _generation_gate_receipt(
        receipt_kind=receipt_kind,
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=theorem_statement_sha256,
    )

    with pytest.raises(ValueError, match=message):
        build_ingredient_generation_receipt(
            tempo=42,
            epoch_seed="epoch-seed",
            ingredient_manifest_sha256=_sha("1"),
            lemma_corpus_snapshot_sha256=_sha("f"),
            ingredient_repo_commit="abc123",
            mathlib_commit="abc123",
            recipe_bundle_sha256=_sha("2"),
            difficulty_state_sha256=_sha("3"),
            selection=_selection(),
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("4"),
            theorem_statement=statement,
            gate_receipt=gate_receipt,
            shortcut_receipt=shortcut_receipt,
        )


def test_build_generation_receipt_rejects_child_receipt_context_drift() -> None:
    statement = "theorem generated_list_length : True := by\n  trivial"
    theorem_statement_sha256 = text_sha256(statement)
    gate_receipt = _generation_gate_receipt(
        receipt_kind="statement_gate",
        active_target_sha256=_sha("5"),
        theorem_statement_sha256=theorem_statement_sha256,
    )
    shortcut_receipt = _generation_gate_receipt(
        receipt_kind="shortcut_gate",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=theorem_statement_sha256,
    )

    with pytest.raises(ValueError, match="ingredient generation receipt gate receipt mismatch"):
        build_ingredient_generation_receipt(
            tempo=42,
            epoch_seed="epoch-seed",
            ingredient_manifest_sha256=_sha("1"),
            lemma_corpus_snapshot_sha256=_sha("f"),
            ingredient_repo_commit="abc123",
            mathlib_commit="abc123",
            recipe_bundle_sha256=_sha("2"),
            difficulty_state_sha256=_sha("3"),
            selection=_selection(),
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("4"),
            theorem_statement=statement,
            gate_receipt=gate_receipt,
            shortcut_receipt=shortcut_receipt,
        )


@pytest.mark.parametrize("epoch_seed", ("", " epoch-seed"))
def test_build_generation_receipt_rejects_invalid_epoch_seed(epoch_seed: str) -> None:
    statement = "theorem generated_list_length : True := by\n  trivial"
    theorem_statement_sha256 = text_sha256(statement)
    with pytest.raises(ValueError, match="ingredient generation receipt epoch seed invalid"):
        build_ingredient_generation_receipt(
            tempo=42,
            epoch_seed=epoch_seed,
            ingredient_manifest_sha256=_sha("1"),
            lemma_corpus_snapshot_sha256=_sha("f"),
            ingredient_repo_commit="abc123",
            mathlib_commit="abc123",
            recipe_bundle_sha256=_sha("2"),
            difficulty_state_sha256=_sha("3"),
            selection=_selection(),
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("4"),
            theorem_statement=statement,
            gate_receipt=_generation_gate_receipt(
                receipt_kind="statement_gate",
                active_target_sha256=_sha("4"),
                theorem_statement_sha256=theorem_statement_sha256,
            ),
            shortcut_receipt=_generation_gate_receipt(
                receipt_kind="shortcut_gate",
                active_target_sha256=_sha("4"),
                theorem_statement_sha256=theorem_statement_sha256,
            ),
        )


@pytest.mark.parametrize("field", ("schema_version", "tempo", "active_K"))
def test_ingredient_generation_receipt_rejects_bool_public_int_fields(field: str) -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload[field] = True

    with pytest.raises(ValidationError, match="expected exact integer"):
        IngredientGenerationReceipt.model_validate(payload)


def test_ingredient_gate_receipt_rejects_bool_schema_version() -> None:
    payload = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        ingredient_manifest_sha256=_sha("1"),
        selection_receipt_sha256=_sha("2"),
        status="passed",
        runner="declared-public-artifact",
        checks=("metadata_bound",),
    ).model_dump(mode="json")
    payload["schema_version"] = True

    with pytest.raises(ValidationError, match="expected exact integer"):
        IngredientGateReceipt.model_validate(payload)


def test_ingredient_gate_receipt_rejects_non_namespace_active_task_id() -> None:
    payload = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        ingredient_manifest_sha256=_sha("1"),
        selection_receipt_sha256=_sha("2"),
        status="passed",
        runner="declared-public-artifact",
        checks=("metadata_bound",),
    ).model_dump(mode="json")
    payload["active_task_id"] = "operator.note"

    with pytest.raises(ValidationError, match="ingredient active task id namespace invalid"):
        IngredientGateReceipt.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    (
        "active_target_sha256",
        "ingredient_manifest_sha256",
        "selection_receipt_sha256",
        "theorem_statement_sha256",
    ),
)
def test_ingredient_gate_receipt_rejects_placeholder_hashes(field: str) -> None:
    payload = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        ingredient_manifest_sha256=_sha("1"),
        selection_receipt_sha256=_sha("2"),
        status="passed",
        runner="declared-public-artifact",
        checks=("metadata_bound",),
    ).model_dump(mode="json")
    payload[field] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient gate receipt sha256 placeholder"):
        IngredientGateReceipt.model_validate(payload)


def test_ingredient_gate_receipt_rejects_non_public_runner() -> None:
    payload = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        ingredient_manifest_sha256=_sha("1"),
        selection_receipt_sha256=_sha("2"),
        status="passed",
        runner="declared-public-artifact",
        checks=("metadata_bound",),
    ).model_dump(mode="json")
    payload["runner"] = "private/path"

    with pytest.raises(ValidationError, match="ingredient gate runner invalid"):
        IngredientGateReceipt.model_validate(payload)


def test_ingredient_gate_receipt_rejects_non_public_checks() -> None:
    payload = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        ingredient_manifest_sha256=_sha("1"),
        selection_receipt_sha256=_sha("2"),
        status="passed",
        runner="declared-public-artifact",
        checks=("metadata_bound",),
    ).model_dump(mode="json")

    payload["checks"] = []
    with pytest.raises(ValidationError, match="ingredient gate checks missing"):
        IngredientGateReceipt.model_validate(payload)

    payload["checks"] = ["metadata_bound", "private/path"]
    with pytest.raises(ValidationError, match="ingredient gate check invalid"):
        IngredientGateReceipt.model_validate(payload)


def test_ingredient_gate_receipt_rejects_duplicate_checks() -> None:
    payload = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        ingredient_manifest_sha256=_sha("1"),
        selection_receipt_sha256=_sha("2"),
        status="passed",
        runner="declared-public-artifact",
        checks=("metadata_bound",),
    ).model_dump(mode="json")
    payload["checks"] = ["metadata_bound", "metadata_bound"]

    with pytest.raises(ValidationError, match="ingredient gate check duplicate: metadata_bound"):
        IngredientGateReceipt.model_validate(payload)


def test_ingredient_gate_receipt_rejects_placeholder_runner_and_checks() -> None:
    payload = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        ingredient_manifest_sha256=_sha("1"),
        selection_receipt_sha256=_sha("2"),
        status="passed",
        runner="declared-public-artifact",
        checks=("metadata_bound",),
    ).model_dump(mode="json")

    payload["runner"] = "0"
    with pytest.raises(ValidationError, match="ingredient gate runner placeholder"):
        IngredientGateReceipt.model_validate(payload)

    payload["runner"] = "declared-public-artifact"
    payload["checks"] = ["metadata_bound", "0x0000"]
    with pytest.raises(ValidationError, match="ingredient gate check placeholder"):
        IngredientGateReceipt.model_validate(payload)


def test_ingredient_generation_receipt_envelope_rejects_bool_schema_version() -> None:
    payload = ingredient_generation_receipt_envelope(
        _receipt_for_statement("theorem generated_list_length : True := by\n  sorry")
    ).model_dump(mode="json")
    payload["schema_version"] = True

    with pytest.raises(ValidationError, match="expected exact integer"):
        IngredientGenerationReceiptEnvelope.model_validate(payload)


def test_ingredient_generation_receipt_envelope_rejects_placeholder_receipt_hash() -> None:
    payload = ingredient_generation_receipt_envelope(
        _receipt_for_statement("theorem generated_list_length : True := by\n  sorry")
    ).model_dump(mode="json")
    payload["generation_receipt_sha256"] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient generation receipt envelope sha256 placeholder"):
        IngredientGenerationReceiptEnvelope.model_validate(payload)


def test_ingredient_generation_receipt_envelope_rejects_receipt_hash_mismatch() -> None:
    payload = ingredient_generation_receipt_envelope(
        _receipt_for_statement("theorem generated_list_length : True := by\n  sorry")
    ).model_dump(mode="json")
    payload["generation_receipt_sha256"] = _sha("9")

    with pytest.raises(ValidationError, match="ingredient generation receipt envelope hash mismatch"):
        IngredientGenerationReceiptEnvelope.model_validate(payload)


@pytest.mark.parametrize("field", ("signer_id", "signature"))
def test_ingredient_generation_receipt_envelope_rejects_partial_signature_metadata(field: str) -> None:
    payload = ingredient_generation_receipt_envelope(
        _receipt_for_statement("theorem generated_list_length : True := by\n  sorry"),
        signer_id="signer.alpha",
        signature="sig.alpha",
    ).model_dump(mode="json")
    payload[field] = None

    with pytest.raises(
        ValidationError,
        match="ingredient generation receipt envelope signature metadata mismatch",
    ):
        IngredientGenerationReceiptEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (("signer_id", "signer/private"), ("signature", "sig/private")),
)
def test_ingredient_generation_receipt_envelope_rejects_non_public_metadata(
    field: str,
    value: str,
) -> None:
    payload = ingredient_generation_receipt_envelope(
        _receipt_for_statement("theorem generated_list_length : True := by\n  sorry"),
        signer_id="signer.alpha",
        signature="sig.alpha",
    ).model_dump(mode="json")
    payload[field] = value

    with pytest.raises(
        ValidationError,
        match="ingredient generation receipt envelope signature metadata invalid",
    ):
        IngredientGenerationReceiptEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (("signer_id", "0"), ("signature", "0"), ("signature", "0x0000")),
)
def test_ingredient_generation_receipt_envelope_rejects_placeholder_metadata(
    field: str,
    value: str,
) -> None:
    payload = ingredient_generation_receipt_envelope(
        _receipt_for_statement("theorem generated_list_length : True := by\n  sorry"),
        signer_id="signer.alpha",
        signature="sig.alpha",
    ).model_dump(mode="json")
    payload[field] = value

    with pytest.raises(
        ValidationError,
        match="ingredient generation receipt envelope signature metadata placeholder",
    ):
        IngredientGenerationReceiptEnvelope.model_validate(payload)


@pytest.mark.parametrize("field", ("schema_version", "netuid", "tempo"))
def test_ingredient_task_artifact_manifest_rejects_bool_public_int_fields(field: str) -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("1"),
        "generation_receipt_envelope_sha256": _sha("2"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("3")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("4")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("5")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("6")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("7")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("8"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("9")},
        },
    }
    payload[field] = True

    with pytest.raises(ValidationError, match="expected exact integer"):
        IngredientTaskArtifactManifest.model_validate(payload)


def test_ingredient_task_artifact_manifest_rejects_non_namespace_active_task_id() -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "operator.note",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("1"),
        "generation_receipt_envelope_sha256": _sha("2"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("3")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("4")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("5")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("6")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("7")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("8"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("9")},
        },
    }

    with pytest.raises(ValidationError, match="ingredient active task id namespace invalid"):
        IngredientTaskArtifactManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("selected_selector_id", "ingredient task artifact manifest selector invalid"),
        ("selected_recipe_id", "ingredient task artifact manifest recipe invalid"),
    ),
)
def test_ingredient_task_artifact_manifest_rejects_non_public_selection_metadata(
    field: str,
    message: str,
) -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("1"),
        "generation_receipt_envelope_sha256": _sha("2"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("3")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("4")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("5")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("6")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("7")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("8"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("9")},
        },
    }
    payload[field] = "private/path"

    with pytest.raises(ValidationError, match=message):
        IngredientTaskArtifactManifest.model_validate(payload)


@pytest.mark.parametrize("field", ("ingredient_repo_commit", "mathlib_commit"))
def test_ingredient_task_artifact_manifest_rejects_non_commit_provenance(field: str) -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("1"),
        "generation_receipt_envelope_sha256": _sha("2"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("3")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("4")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("5")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("6")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("7")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("8"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("9")},
        },
    }
    payload[field] = "private/path"

    with pytest.raises(ValidationError, match=field):
        IngredientTaskArtifactManifest.model_validate(payload)


@pytest.mark.parametrize("field", ("ingredient_repo_commit", "mathlib_commit"))
def test_ingredient_task_artifact_manifest_rejects_placeholder_provenance(field: str) -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("1"),
        "generation_receipt_envelope_sha256": _sha("2"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("3")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("4")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("5")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("6")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("7")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("8"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("9")},
        },
    }
    payload[field] = "0" * 6

    with pytest.raises(
        ValidationError,
        match=f"ingredient task artifact manifest {field} placeholder",
    ):
        IngredientTaskArtifactManifest.model_validate(payload)


@pytest.mark.parametrize("field", ("challenge_seed_sha256", "epoch_seed_sha256"))
def test_ingredient_task_artifact_manifest_rejects_placeholder_seed_hashes(field: str) -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("2"),
        "generation_receipt_envelope_sha256": _sha("3"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("4")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("5")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("6")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("7")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("8")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("9"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("a")},
        },
    }
    payload[field] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient task artifact manifest seed placeholder"):
        IngredientTaskArtifactManifest.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    (
        "active_target_sha256",
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
        "theorem_statement_sha256",
        "theorem_type_expr_sha256",
    ),
)
def test_ingredient_task_artifact_manifest_rejects_placeholder_hashes(field: str) -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("2"),
        "generation_receipt_envelope_sha256": _sha("3"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("4")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("5")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("6")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("7")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("8")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("9"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("a")},
        },
    }
    payload[field] = _sha("0")

    with pytest.raises(ValidationError, match="ingredient task artifact manifest sha256 placeholder"):
        IngredientTaskArtifactManifest.model_validate(payload)


def test_ingredient_task_artifact_manifest_rejects_placeholder_artifact_ref_hash() -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("2"),
        "generation_receipt_envelope_sha256": _sha("3"),
        "artifacts": {
            "task": {"path": "task.json", "sha256": _sha("0")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("5")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("6")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("7")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("8")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("9"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("a")},
        },
    }

    with pytest.raises(ValidationError, match="ingredient task artifact ref sha256 placeholder"):
        IngredientTaskArtifactManifest.model_validate(payload)


@pytest.mark.parametrize("path", ("operator-note/task.json", "../task.json", "/task.json", " task.json"))
def test_ingredient_task_artifact_manifest_rejects_pathlike_artifact_refs(path: str) -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("1"),
        "generation_receipt_envelope_sha256": _sha("2"),
        "artifacts": {
            "task": {"path": path, "sha256": _sha("3")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("4")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("5")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("6")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("7")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("8"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("9")},
        },
    }

    with pytest.raises(ValidationError, match="ingredient task artifact ref path invalid"):
        IngredientTaskArtifactManifest.model_validate(payload)


def test_ingredient_task_artifact_manifest_rejects_wrong_artifact_ref_filename() -> None:
    payload = {
        "schema_version": 1,
        "active_task_id": "lemma.ingredient.list_length",
        "active_target_sha256": _sha("target"),
        "theorem_statement_sha256": _sha("statement"),
        "selected_selector_id": "hard_selector",
        "selected_recipe_id": "list_length_v1",
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": {"Nat": "2"}}),
        "theorem_type_expr_sha256": text_sha256("True"),
        "novelty_family_hash": ingredient_novelty_family_hash(_selection()),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "def456",
        "recipe_bundle_sha256": _sha("2"),
        "netuid": 467,
        "tempo": 42,
        "epoch_seed_sha256": _sha("a"),
        "challenge_seed_sha256": _sha("b"),
        "difficulty_state_sha256": _sha("c"),
        "difficulty_lane": "hard",
        "ingredient_manifest_sha256": _sha("d"),
        "selection_receipt_sha256": _sha("e"),
        "gate_receipt_sha256": _sha("f"),
        "shortcut_receipt_sha256": _sha("1"),
        "generation_receipt_sha256": _sha("1"),
        "generation_receipt_envelope_sha256": _sha("2"),
        "artifacts": {
            "task": {"path": "operator-note.json", "sha256": _sha("3")},
            "selection_receipt": {"path": "selection-receipt.json", "sha256": _sha("4")},
            "gate_receipt": {"path": "gate-receipt.json", "sha256": _sha("5")},
            "shortcut_receipt": {"path": "shortcut-receipt.json", "sha256": _sha("6")},
            "generation_receipt": {"path": "generation-receipt.json", "sha256": _sha("7")},
            "generation_receipt_envelope": {
                "path": "generation-receipt-envelope.json",
                "sha256": _sha("8"),
            },
            "active_registry": {"path": "active-registry.json", "sha256": _sha("9")},
        },
    }

    with pytest.raises(ValidationError, match="ingredient task artifact path invalid: task"):
        IngredientTaskArtifactManifest.model_validate(payload)


def test_ingredient_generation_receipt_rejects_non_commit_repo_provenance() -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload["ingredient_repo_commit"] = "private/path"

    with pytest.raises(ValidationError, match="ingredient_repo_commit"):
        IngredientGenerationReceipt.model_validate(payload)


def test_ingredient_generation_receipt_rejects_non_commit_mathlib_provenance() -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload["mathlib_commit"] = "private/path"

    with pytest.raises(ValidationError, match="mathlib_commit"):
        IngredientGenerationReceipt.model_validate(payload)


@pytest.mark.parametrize("field", ("ingredient_repo_commit", "mathlib_commit"))
def test_ingredient_generation_receipt_rejects_placeholder_commit_provenance(field: str) -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload[field] = "0" * 6

    with pytest.raises(ValidationError, match=f"ingredient generation receipt {field} placeholder"):
        IngredientGenerationReceipt.model_validate(payload)


def test_ingredient_generation_receipt_rejects_non_label_active_task_id() -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload["active_task_id"] = "private/path"

    with pytest.raises(ValidationError, match="active_task_id"):
        IngredientGenerationReceipt.model_validate(payload)


def test_ingredient_generation_receipt_rejects_non_namespace_active_task_id() -> None:
    payload = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry").model_dump(mode="json")
    payload["active_task_id"] = "operator.note"

    with pytest.raises(ValidationError, match="ingredient active task id namespace invalid"):
        IngredientGenerationReceipt.model_validate(payload)


def test_build_fixture_ingredient_task_attaches_receipt_metadata() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement)

    task = build_fixture_ingredient_task(
        receipt=receipt,
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
    )

    assert task.id == receipt.active_task_id
    assert task.title == ""
    assert task.source_stream == "ingredient"
    assert task.source_ref.kind == "ingredient"
    assert task.source_ref.name == "list_length_v1"
    assert task.source_ref.commit == "abc123"
    assert task.target_sha256 == receipt.active_target_sha256
    assert task.difficulty_band == "hard"
    assert task.metadata["supply_mode"] == "ingredient"
    assert task.metadata["lemma_corpus_snapshot_sha256"] == receipt.lemma_corpus_snapshot_sha256
    assert task.metadata["generation_receipt_sha256"] == canonical_sha256(receipt)
    assert task.metadata["generation_receipt_sha256"] == expected_ingredient_generation_receipt_sha256(task)
    assert task.metadata["ingredient_ids"] == ["List.length", "List.length_map", "List.length_to_Nat"]
    assert task.metadata["ingredient_count"] == 3
    assert task.metadata["hidden_lemma_count"] == 0
    assert task.metadata["novelty_family_hash"] == expected_ingredient_novelty_family_hash(task)
    assert task.metadata["definition_ids"] == ["List.length"]
    assert task.metadata["fact_ids"] == ["List.length_map"]
    assert task.metadata["bridge_ids"] == ["List.length_to_Nat"]


@pytest.mark.parametrize(
    ("key", "message"),
    (
        ("tempo", "ingredient task tempo metadata malformed"),
        ("active_K", "ingredient task active_K metadata malformed"),
    ),
)
def test_ingredient_generation_receipt_from_task_rejects_bool_public_int_metadata(
    key: str, message: str
) -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    task = build_fixture_ingredient_task(
        receipt=_receipt_for_statement(statement),
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
    )
    drifted = task.model_copy(update={"metadata": {**task.metadata, key: True}})

    with pytest.raises(ValueError, match=message):
        ingredient_generation_receipt_from_task(drifted)


@pytest.mark.parametrize(
    "selected_parameters",
    [[], {" Nat": "2"}, {"private/path": "2"}, {"Nat": "02"}, {"Bool": "yes"}, {"Nat": object()}],
)
def test_ingredient_generation_receipt_from_task_rejects_malformed_public_parameters(
    selected_parameters: object,
) -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    task = build_fixture_ingredient_task(
        receipt=_receipt_for_statement(statement),
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
    )
    drifted = task.model_copy(
        update={"metadata": {**task.metadata, "selected_parameters": selected_parameters}}
    )

    with pytest.raises(ValueError, match="ingredient task selected_parameters metadata malformed"):
        ingredient_generation_receipt_from_task(drifted)


def test_build_fixture_ingredient_task_rejects_receipt_mismatch() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement).model_copy(update={"theorem_statement_sha256": _sha("5")})

    with pytest.raises(ValueError, match="theorem_statement_sha256 mismatch"):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="generated_list_length",
            type_expr="True",
            statement=statement,
        )


def test_build_fixture_ingredient_task_rejects_non_identifier_theorem_name() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement)

    with pytest.raises(ValueError, match="ingredient theorem name invalid"):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="private/path",
            type_expr="True",
            statement=statement,
        )


def test_build_fixture_ingredient_task_rejects_noncanonical_type_expr() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement)

    with pytest.raises(ValueError, match="ingredient theorem type expression not canonical"):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="generated_list_length",
            type_expr=" True ",
            statement=statement,
        )


@pytest.mark.parametrize(
    ("statement", "message"),
    (
        ("theorem generated_list_length :  True := by\n  sorry", "ingredient theorem statement header mismatch"),
        ("theorem generated_list_length : True := by\n    sorry", "ingredient theorem statement body invalid"),
    ),
)
def test_build_fixture_ingredient_task_rejects_noncanonical_statement_skeleton(
    statement: str,
    message: str,
) -> None:
    receipt = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry")

    with pytest.raises(ValueError, match=message):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="generated_list_length",
            type_expr="True",
            statement=statement,
        )


def test_build_fixture_ingredient_task_rejects_title() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement)

    with pytest.raises(ValueError, match="ingredient task title must be empty"):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="generated_list_length",
            type_expr="True",
            statement=statement,
            title="Generated List Length",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("source_license", "MIT", "ingredient task source license mismatch"),
        ("policy", "strict_envelope", "ingredient task submission policy mismatch"),
        ("lean_toolchain", "private/toolchain", "ingredient task lean toolchain mismatch"),
    ),
)
def test_build_fixture_ingredient_task_rejects_fixed_public_envelope_drift(
    field: str,
    value: str,
    message: str,
) -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement)
    kwargs = {
        "receipt": receipt,
        "theorem_name": "generated_list_length",
        "type_expr": "True",
        "statement": statement,
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        build_fixture_ingredient_task(**kwargs)


def test_build_fixture_ingredient_task_rejects_statement_header_mismatch() -> None:
    statement = "theorem other_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement)

    with pytest.raises(ValueError, match="ingredient theorem statement header mismatch"):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="generated_list_length",
            type_expr="True",
            statement=statement,
        )


def test_build_fixture_ingredient_task_rejects_statement_extra_declaration() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry\n\naxiom hidden_hint : False"
    receipt = _receipt_for_statement(statement)

    with pytest.raises(ValueError, match="ingredient theorem statement body invalid"):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="generated_list_length",
            type_expr="True",
            statement=statement,
        )


def test_build_fixture_ingredient_task_rejects_non_public_import() -> None:
    statement = "theorem generated_list_length : True := by\n  sorry"
    receipt = _receipt_for_statement(statement)

    with pytest.raises(ValueError, match="ingredient import invalid: Private.OperatorHints"):
        build_fixture_ingredient_task(
            receipt=receipt,
            theorem_name="generated_list_length",
            type_expr="True",
            statement=statement,
            imports=("Private.OperatorHints",),
        )


def test_build_fixture_ingredient_registry_is_deterministic_one_task_path() -> None:
    kwargs = {
        "netuid": 467,
        "tempo": 42,
        "epoch_seed": "epoch-seed",
        "ingredient_manifest_sha256": _sha("1"),
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "abc123",
        "recipe_bundle_sha256": _sha("2"),
        "difficulty_state_sha256": _sha("3"),
        "difficulty_lane": "hard",
        "selectors": (_selector(),),
        "recipes": (_recipe(),),
        "definitions": (_definition(),),
        "facts": (_fact(),),
        "compatibility_edges": (_edge(),),
        "bridges": (
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        "parameter_sets": {"Nat": ("2", "3")},
        "theorem_name": "generated_list_length",
        "type_expr": "True",
        "statement": "theorem generated_list_length : True := by\n  sorry",
        "active_task_id": "lemma.ingredient.list_length",
        "gate_receipt_sha256": _sha("6"),
        "shortcut_receipt_sha256": _sha("7"),
    }

    registry = build_fixture_ingredient_registry(**kwargs)
    replayed = build_fixture_ingredient_registry(**kwargs)

    assert registry.sha256 == replayed.sha256
    assert len(registry.tasks) == 1
    task = registry.tasks[0]
    replayed_task = replayed.tasks[0]
    challenge_seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )
    replay_fields = (
        "id",
        "target_sha256",
        "statement",
        "metadata",
    )
    assert {field: getattr(task, field) for field in replay_fields} == {
        field: getattr(replayed_task, field) for field in replay_fields
    }
    assert task.source_stream == "ingredient"
    assert task.title == ""
    assert task.metadata["selection_seed_sha256"] == challenge_seed
    assert task.metadata["theorem_statement_sha256"] == text_sha256(kwargs["statement"])
    assert task.metadata["active_target_sha256"] == task.target_sha256
    assert task.metadata["generation_receipt_sha256"] == expected_ingredient_generation_receipt_sha256(task)
    assert task.metadata["active_K"] == 1
    assert task.metadata["recipe_id"] == "list_length_v1"


def test_fixture_ingredient_registry_epoch_seed_changes_selected_ingredients() -> None:
    kwargs = {
        "netuid": 467,
        "tempo": 42,
        "ingredient_manifest_sha256": _sha("1"),
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "abc123",
        "recipe_bundle_sha256": _sha("2"),
        "difficulty_state_sha256": _sha("3"),
        "difficulty_lane": "hard",
        "selectors": (_selector(),),
        "recipes": (_recipe(),),
        "definitions": (_definition(),),
        "facts": (_fact("List.length_map"), _fact("List.length_reverse")),
        "compatibility_edges": (_edge(bridge_ids=()),),
        "parameter_sets": {"Nat": ("2", "3")},
        "theorem_name": "generated_list_length",
        "type_expr": "True",
        "statement": "theorem generated_list_length : True := by\n  sorry",
        "active_task_id": "lemma.ingredient.list_length",
        "gate_receipt_sha256": _sha("6"),
        "shortcut_receipt_sha256": _sha("7"),
    }

    first = build_fixture_ingredient_registry(epoch_seed="epoch-seed", **kwargs)
    second = build_fixture_ingredient_registry(epoch_seed="different-epoch-seed", **kwargs)

    assert first.sha256 != second.sha256
    assert first.tasks[0].metadata["fact_ids"] != second.tasks[0].metadata["fact_ids"]
    assert first.tasks[0].metadata["selection_seed_sha256"] != second.tasks[0].metadata["selection_seed_sha256"]


def test_validator_recomputes_fixture_ingredient_selection_from_public_inputs() -> None:
    kwargs = {
        "netuid": 467,
        "tempo": 42,
        "epoch_seed": "epoch-seed",
        "ingredient_manifest_sha256": _sha("1"),
        "lemma_corpus_snapshot_sha256": _sha("f"),
        "ingredient_repo_commit": "abc123",
        "mathlib_commit": "abc123",
        "recipe_bundle_sha256": _sha("2"),
        "difficulty_state_sha256": _sha("3"),
        "difficulty_lane": "hard",
        "selectors": (_selector(),),
        "recipes": (_recipe(),),
        "definitions": (_definition(),),
        "facts": (_fact(),),
        "compatibility_edges": (_edge(),),
        "bridges": (
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        "parameter_sets": {"Nat": ("2", "3")},
        "theorem_name": "generated_list_length",
        "type_expr": "True",
        "statement": "theorem generated_list_length : True := by\n  sorry",
        "active_task_id": "lemma.ingredient.list_length",
        "gate_receipt_sha256": _sha("6"),
        "shortcut_receipt_sha256": _sha("7"),
    }
    task = build_fixture_ingredient_registry(**kwargs).tasks[0]
    challenge_seed = ingredient_challenge_seed_sha256(
        netuid=kwargs["netuid"],
        tempo=kwargs["tempo"],
        epoch_seed=kwargs["epoch_seed"],
        ingredient_manifest_sha256=kwargs["ingredient_manifest_sha256"],
        recipe_bundle_sha256=kwargs["recipe_bundle_sha256"],
        difficulty_state_sha256=kwargs["difficulty_state_sha256"],
    )

    selection = verify_fixture_ingredient_selection(
        task,
        challenge_seed_sha256=challenge_seed,
        difficulty_lane="hard",
        selectors=kwargs["selectors"],
        recipes=kwargs["recipes"],
        definitions=kwargs["definitions"],
        facts=kwargs["facts"],
        compatibility_edges=kwargs["compatibility_edges"],
        bridges=kwargs["bridges"],
        parameter_sets=kwargs["parameter_sets"],
    )

    assert selection.selected_recipe_id == "list_length_v1"
    assert selection.selected_definition_ids == ("List.length",)
    assert selection.selected_fact_ids == ("List.length_map",)
    assert task.metadata["selection_seed_sha256"] == challenge_seed


def test_validator_rejects_fixture_ingredient_selection_metadata_drift() -> None:
    task = build_fixture_ingredient_registry(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit="abc123",
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(),),
        bridges=(
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        parameter_sets={"Nat": ("2", "3")},
        theorem_name="generated_list_length",
        type_expr="True",
        statement="theorem generated_list_length : True := by\n  sorry",
        active_task_id="lemma.ingredient.list_length",
        gate_receipt_sha256=_sha("6"),
        shortcut_receipt_sha256=_sha("7"),
    ).tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "fact_ids": ["List.length_reverse"]}})

    with pytest.raises(ValueError, match="ingredient selection metadata mismatch: fact_ids"):
        verify_fixture_ingredient_selection(
            drifted,
            challenge_seed_sha256=task.metadata["selection_seed_sha256"],
            difficulty_lane="hard",
            selectors=(_selector(),),
            recipes=(_recipe(),),
            definitions=(_definition(),),
            facts=(_fact(),),
            compatibility_edges=(_edge(),),
            bridges=(
                BridgeRule(
                    bridge_id="List.length_to_Nat",
                    from_domain="List",
                    to_domain="Nat",
                    safe_recipes=("list_length_v1",),
                ),
            ),
            parameter_sets={"Nat": ("2", "3")},
        )


def test_ingredient_registry_cache_validates_one_committed_winner(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    settings, registry, loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    submission = _committed_ingredient_submission(
        task,
        solver_hotkey="miner-hotkey",
        commit_block=100,
        commit_extrinsic_hash="0xabc",
    )

    result = validate_once(
        settings,
        [submission],
        tempo=42,
        verify_submission=lambda task, submission: VerifyResult(
            passed=True,
            reason="ok",
            proof_term_hash="strong-proof-term-hash",
        ),
        no_set_weights=True,
        chain_authenticated_keys=frozenset({(task.id, submission.solver_hotkey, submission.proof_sha256)}),
    )

    assert loaded.tasks == registry.tasks
    assert result.summary.active_K == 1
    assert result.summary.accepted_unique_count == 1
    assert result.score.scores == {"miner-hotkey": 1.0}
    assert result.score.winners == {task.id: "miner-hotkey"}
    assert result.corpus_rows[0].source_stream == "ingredient"


def test_ingredient_validation_rejects_direct_registry_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, registry, _loaded, _task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted_registry = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=registry.tasks,
        sha256=_sha("e"),
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="current active-registry cache"):
        validate_once(settings, [], registry=drifted_registry, tempo=42, no_set_weights=True)


def test_ingredient_active_selection_rejects_direct_registry_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, registry, _loaded, _task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted_registry = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=registry.tasks,
        sha256=_sha("e"),
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="current active-registry cache"):
        active_tasks_for_validation(drifted_registry, settings, tempo=42)


def test_ingredient_active_selection_rejects_direct_registry_envelope_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, _registry, loaded, _task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted_registry = TaskRegistry(
        schema_version=loaded.schema_version,
        tasks=loaded.tasks,
        sha256=loaded.sha256,
        signature_status=loaded.signature_status,
        created_at="2026-01-01T00:00:00Z",
    )

    with pytest.raises(RuntimeError, match="current active-registry cache"):
        active_tasks_for_validation(drifted_registry, settings, tempo=42)


def test_ingredient_validation_rejects_stale_epoch_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted = task.model_copy(update={"metadata": {**task.metadata, "epoch_seed_sha256": _sha("0")}})
    drifted_registry = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=(drifted,),
        sha256=registry.sha256,
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="epoch_seed_sha256"):
        active_tasks_for_validation(drifted_registry, settings, tempo=42)


def test_ingredient_validation_rejects_stale_selection_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted = task.model_copy(update={"metadata": {**task.metadata, "selection_seed_sha256": _sha("0")}})
    drifted_registry = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=(drifted,),
        sha256=registry.sha256,
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="selection_seed_sha256"):
        active_tasks_for_validation(drifted_registry, settings, tempo=42)


def test_ingredient_validation_rejects_stale_receipt_tempo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted = task.model_copy(update={"metadata": {**task.metadata, "tempo": 41}})
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "generation_receipt_sha256": expected_ingredient_generation_receipt_sha256(drifted),
            }
        }
    )
    drifted_registry = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=(drifted,),
        sha256=registry.sha256,
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match=":tempo"):
        active_tasks_for_validation(drifted_registry, settings, tempo=42)


def test_ingredient_active_selection_rejects_zero_active_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted = task.model_copy(update={"queue_depth": settings.frontier_depth + 1})
    drifted_registry = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=(drifted,),
        sha256=registry.sha256,
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="active task count mismatch"):
        active_tasks_for_validation(drifted_registry, settings, tempo=42)


def test_ingredient_active_selection_rejects_multi_task_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    drifted_registry = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=(task, task),
        sha256=registry.sha256,
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="active task count mismatch"):
        active_tasks_for_validation(drifted_registry, settings, tempo=42)


def test_ingredient_validation_orders_by_commit_and_skips_duplicate_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, _registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    late = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-late",
        commit_block=20,
        commit_extrinsic_hash="1",
    )
    early = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-early",
        commit_block=10,
        commit_extrinsic_hash="0",
    )
    latest = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-latest",
        commit_block=30,
        commit_extrinsic_hash="2",
    )
    calls: list[str] = []

    result = validate_once(
        settings,
        [late, latest, early],
        tempo=42,
        verify_submission=lambda task, submission: (
            calls.append(submission.solver_hotkey)
            or VerifyResult(passed=True, reason="ok", proof_term_hash=f"term-{submission.solver_hotkey}")
        ),
        no_set_weights=True,
        chain_authenticated_keys=frozenset(
            (task.id, submission.solver_hotkey, submission.proof_sha256)
            for submission in (late, early, latest)
        ),
    )

    assert calls == ["hk-early"]
    assert result.summary.verified_count == 1
    assert result.summary.accepted_unique_count == 1
    assert result.score.winners == {task.id: "hk-early"}
    assert result.score.scores == {"hk-early": 1.0}


def test_ingredient_validation_does_not_use_hotkey_as_commit_tiebreaker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, _registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    hotkey_first = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-a",
        commit_block=10,
        commit_extrinsic_hash="1",
    )
    commitment_first = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-z",
        commit_block=10,
        commit_extrinsic_hash="0",
    )
    calls: list[str] = []

    result = validate_once(
        settings,
        [hotkey_first, commitment_first],
        tempo=42,
        verify_submission=lambda task, submission: (
            calls.append(submission.solver_hotkey)
            or VerifyResult(passed=True, reason="ok", proof_term_hash=f"term-{submission.solver_hotkey}")
        ),
        no_set_weights=True,
        chain_authenticated_keys=frozenset(
            (task.id, submission.solver_hotkey, submission.proof_sha256)
            for submission in (hotkey_first, commitment_first)
        ),
    )

    assert calls == ["hk-z"]
    assert result.score.winners == {task.id: "hk-z"}


def test_ingredient_validation_uses_chain_position_before_hash_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, _registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    hash_first = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-a",
        commit_block=10,
        commit_extrinsic_index=2,
        commit_event_index=0,
        commit_extrinsic_hash="0",
    )
    position_first = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-z",
        commit_block=10,
        commit_extrinsic_index=1,
        commit_event_index=9,
        commit_extrinsic_hash="1",
    )
    calls: list[str] = []

    result = validate_once(
        settings,
        [hash_first, position_first],
        tempo=42,
        verify_submission=lambda task, submission: (
            calls.append(submission.solver_hotkey)
            or VerifyResult(passed=True, reason="ok", proof_term_hash=f"term-{submission.solver_hotkey}")
        ),
        no_set_weights=True,
        chain_authenticated_keys=frozenset(
            (task.id, submission.solver_hotkey, submission.proof_sha256)
            for submission in (hash_first, position_first)
        ),
    )

    assert calls == ["hk-z"]
    assert result.score.winners == {task.id: "hk-z"}


def test_ingredient_validation_invalid_early_proof_does_not_block_later_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, _registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    invalid = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-invalid",
        commit_block=10,
        commit_extrinsic_hash="0",
    )
    valid = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-valid",
        commit_block=11,
        commit_extrinsic_hash="1",
        proof_script=_ingredient_proof().replace("  trivial", "  exact True.intro"),
    )
    calls: list[str] = []

    def verify(_task: LemmaTask, submission: LemmaSubmission) -> VerifyResult:
        calls.append(submission.solver_hotkey)
        if submission.solver_hotkey == "hk-invalid":
            return VerifyResult(passed=False, reason="compile_error")
        return VerifyResult(passed=True, reason="ok", proof_term_hash="term-valid")

    result = validate_once(
        settings,
        [valid, invalid],
        tempo=42,
        verify_submission=verify,
        no_set_weights=True,
        chain_authenticated_keys=frozenset(
            (task.id, submission.solver_hotkey, submission.proof_sha256) for submission in (invalid, valid)
        ),
    )

    assert calls == ["hk-invalid", "hk-valid"]
    assert result.summary.verified_count == 2
    assert result.summary.accepted_unique_count == 1
    assert result.score.winners == {task.id: "hk-valid"}


def test_ingredient_validation_skips_repeated_proof_payload_before_lean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, _registry, _loaded, task = _ingredient_validation_context(monkeypatch, tmp_path)
    invalid = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-invalid",
        commit_block=10,
        commit_extrinsic_hash="0",
    )
    duplicate = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-duplicate",
        commit_block=11,
        commit_extrinsic_hash="1",
    )
    valid = _committed_ingredient_submission(
        task,
        solver_hotkey="hk-valid",
        commit_block=12,
        commit_extrinsic_hash="2",
        proof_script=_ingredient_proof().replace("  trivial", "  exact True.intro"),
    )
    calls: list[str] = []

    def verify(_task: LemmaTask, submission: LemmaSubmission) -> VerifyResult:
        calls.append(submission.solver_hotkey)
        if submission.solver_hotkey == "hk-invalid":
            return VerifyResult(passed=False, reason="compile_error")
        return VerifyResult(passed=True, reason="ok", proof_term_hash="term-valid")

    result = validate_once(
        settings,
        [valid, duplicate, invalid],
        tempo=42,
        verify_submission=verify,
        no_set_weights=True,
        chain_authenticated_keys=frozenset(
            (task.id, submission.solver_hotkey, submission.proof_sha256)
            for submission in (invalid, duplicate, valid)
        ),
    )

    receipts = (settings.operator_data_dir / "verification-records.jsonl").read_text(encoding="utf-8")

    assert calls == ["hk-invalid", "hk-valid"]
    assert "duplicate_proof_payload" in receipts
    assert result.summary.verified_count == 2
    assert result.score.winners == {task.id: "hk-valid"}


def test_select_ingredient_receipt_from_root_uses_public_repo_artifacts(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    facts = _write_selection_ingredient_repo(root)
    seed = _sha("a")

    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    expected = select_fixture_ingredients(
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=facts,
        compatibility_edges=(_edge(),),
        bridges=(
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        parameter_sets={"Nat": ("2", "3")},
    )

    assert receipt == expected


def test_select_ingredient_receipt_from_root_uses_public_reserve_selector(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    seed = _sha("f")
    failing_id, reserve_id = sorted(
        ("reserve_alpha", "reserve_beta"),
        key=lambda selector_id: canonical_sha256({"seed": seed, "label": "selector", "key": selector_id}),
    )
    selectors = (
        RecipeSelector(
            selector_id=failing_id,
            difficulty_lane="hard",
            recipe_ids=("list_length_v1",),
            ingredient_filters={"domains": ["Nat"]},
        ),
        RecipeSelector(
            selector_id=reserve_id,
            difficulty_lane="hard",
            recipe_ids=("list_length_v1",),
            ingredient_filters={"domains": ["List", "Nat"]},
        ),
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"],
        *sorted(selectors, key=lambda selector: selector.selector_id),
    )

    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    assert receipt.selected_selector_id == reserve_id
    assert receipt.selected_recipe_id == "list_length_v1"
    assert receipt.selected_definition_ids == ("List.length",)
    assert receipt.selected_fact_ids


def test_realize_ingredient_theorem_statement_uses_public_selection(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_list_length",
    )

    selected_nat = receipt.selected_parameters["Nat"]
    assert type_expr == f"List.length (List.replicate {selected_nat} 0) = {selected_nat}"
    assert statement == f"theorem generated_list_length : {type_expr} := by\n  sorry"


def test_ingredient_soundness_witness_probe_script_uses_selected_parameter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    type_expr, _statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_list_length",
    )

    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_list_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert "sorry" not in proof_script
    assert f"_root_.list_length_soundness {receipt.selected_parameters['Nat']}" in proof_script
    assert f"theorem generated_list_length : {type_expr} := by" in proof_script


def test_nat_add_zero_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), _nat_add_zero_recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "nat_add_zero_v1",
            "selected_definition_ids": ("Nat.add",),
            "selected_fact_ids": ("Nat.add_zero",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_nat_add_zero",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_nat_add_zero",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "Nat.add 3 0 = 3"
    assert statement == f"theorem generated_nat_add_zero : {type_expr} := by\n  sorry"
    assert "_root_.nat_add_zero_soundness 3" in proof_script


def test_nat_mul_one_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), _nat_mul_one_recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "nat_mul_one_v1",
            "selected_definition_ids": ("Nat.mul",),
            "selected_fact_ids": ("Nat.mul_one",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_nat_mul_one",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_nat_mul_one",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "Nat.mul 3 1 = 3"
    assert statement == f"theorem generated_nat_mul_one : {type_expr} := by\n  sorry"
    assert "_root_.nat_mul_one_soundness 3" in proof_script


def test_reverse_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), _reverse_length_recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_reverse_length_v1",
            "selected_definition_ids": ("List.length", "List.reverse"),
            "selected_fact_ids": ("List.length_reverse",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_reverse_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_reverse_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.reverse (List.replicate 3 0)) = 3"
    assert statement == f"theorem generated_reverse_length : {type_expr} := by\n  sorry"
    assert "_root_.list_reverse_length_soundness 3" in proof_script


def test_append_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_append_length_recipe().model_dump(mode="json"), _recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_append_length_v1",
            "selected_definition_ids": ("List.append", "List.length"),
            "selected_fact_ids": ("List.length_append",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_append_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_append_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length ((List.replicate 3 0) ++ (List.replicate 3 1)) = 3 + 3"
    assert statement == f"theorem generated_append_length : {type_expr} := by\n  sorry"
    assert "_root_.list_append_length_soundness 3" in proof_script


def test_dedup_pair_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_dedup_pair_length_recipe().model_dump(mode="json"), _recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_dedup_pair_length_v1",
            "selected_definition_ids": ("List.dedup",),
            "selected_fact_ids": ("List.dedup_cons_of_mem",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_dedup_pair_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_dedup_pair_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.dedup [3, 3]) = 1"
    assert statement == f"theorem generated_dedup_pair_length : {type_expr} := by\n  sorry"
    assert "_root_.list_dedup_pair_length_soundness 3" in proof_script


def test_drop_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_drop_length_recipe().model_dump(mode="json"), _recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_drop_length_v1",
            "selected_definition_ids": ("List.drop", "List.length"),
            "selected_fact_ids": ("List.length_drop",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_drop_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_drop_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.drop 3 (List.replicate (3 + 3) 0)) = 3"
    assert statement == f"theorem generated_drop_length : {type_expr} := by\n  sorry"
    assert "_root_.list_drop_length_soundness 3" in proof_script


def test_filter_true_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_filter_true_length_recipe().model_dump(mode="json"), _recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_filter_true_length_v1",
            "selected_definition_ids": ("List.filter", "List.length"),
            "selected_fact_ids": ("List.length_filter",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_filter_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_filter_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.filter (fun _ : Nat => true) (List.replicate 3 0)) = 3"
    assert statement == f"theorem generated_filter_length : {type_expr} := by\n  sorry"
    assert "_root_.list_filter_true_length_soundness 3" in proof_script


def test_map_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), _map_length_recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_map_length_v1",
            "selected_definition_ids": ("List.length", "List.map"),
            "selected_fact_ids": ("List.length_map",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_map_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_map_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.map (fun x : Nat => x) (List.replicate 3 0)) = 3"
    assert statement == f"theorem generated_map_length : {type_expr} := by\n  sorry"
    assert "_root_.list_map_length_soundness 3" in proof_script


def test_take_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), _take_length_recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_take_length_v1",
            "selected_definition_ids": ("List.length", "List.take"),
            "selected_fact_ids": ("List.length_take",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_take_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_take_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.take 3 (List.replicate 3 0)) = 3"
    assert statement == f"theorem generated_take_length : {type_expr} := by\n  sorry"
    assert "_root_.list_take_length_soundness 3" in proof_script


def test_range_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), _range_length_recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_range_length_v1",
            "selected_definition_ids": ("List.length", "List.range"),
            "selected_fact_ids": ("List.length_range",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_range_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_range_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.range 3) = 3"
    assert statement == f"theorem generated_range_length : {type_expr} := by\n  sorry"
    assert "_root_.list_range_length_soundness 3" in proof_script


def test_zip_length_recipe_realization_and_witness(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), _zip_length_recipe().model_dump(mode="json")]},
    )
    receipt = _selection().model_copy(
        update={
            "selected_recipe_id": "list_zip_length_v1",
            "selected_definition_ids": ("List.length", "List.zip"),
            "selected_fact_ids": ("List.length_zip",),
            "selected_parameters": {"Nat": "3"},
        }
    )

    type_expr, statement = realize_ingredient_theorem_statement(
        root,
        selection=receipt,
        theorem_name="generated_zip_length",
    )
    proof_script = ingredient_soundness_witness_probe_script(
        root,
        selection=receipt,
        theorem_name="generated_zip_length",
        theorem_type_expr=type_expr,
        imports=("Mathlib",),
    )

    assert type_expr == "List.length (List.zip (List.replicate 3 0) (List.replicate 3 1)) = 3"
    assert statement == f"theorem generated_zip_length : {type_expr} := by\n  sorry"
    assert "_root_.list_zip_length_soundness 3" in proof_script


def test_build_ingredient_compatibility_bootstraps_public_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 1
    assert summary["selector_count"] == len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert recipes["recipes"][0]["shortcut_checks"] == [
        "source_oracle",
        "source_subterm_oracle",
        "source_numeric_skeleton_oracle",
        "source_shape_skeleton_oracle",
        "source_token_multiset_oracle",
    ]
    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    assert receipt.selected_recipe_id == "list_length_v1"
    template = root / "recipes" / "soundness_templates" / "list_length.lean"
    assert "theorem list_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_nat_add_zero_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"], _nat_definition())
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], _nat_fact())
    (root / "recipes" / "soundness_templates" / "list_length.lean").unlink()
    extraction_report = json.loads(
        (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text(encoding="utf-8")
    )
    extraction_report.update(
        {
            "definition_count": 1,
            "fact_count": 1,
            "source_license_counts": {"Apache-2.0": 2},
            "source_row_count": 2,
        }
    )
    _write_ingredient_json(root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"], extraction_report)
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 1
    assert summary["selector_count"] == len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["nat_add_zero_v1"]
    assert recipes["recipes"][0]["domains"] == ["Nat"]
    assert recipes["recipes"][0]["required_ingredient_classes"] == ["nat_definition", "nat_fact"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["nat_add_zero_v1"]
    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    assert receipt.selected_recipe_id == "nat_add_zero_v1"
    assert receipt.selected_definition_ids == ("Nat.add",)
    assert receipt.selected_fact_ids == ("Nat.add_zero",)
    template = root / "recipes" / "soundness_templates" / "nat_add_zero.lean"
    assert "theorem nat_add_zero_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_nat_mul_one_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _nat_definition("Nat.mul"),
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _nat_fact("Nat.mul_one", "Nat.mul 0 1 = 0"),
    )
    (root / "recipes" / "soundness_templates" / "list_length.lean").unlink()
    extraction_report = json.loads(
        (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text(encoding="utf-8")
    )
    extraction_report.update(
        {
            "definition_count": 1,
            "fact_count": 1,
            "source_license_counts": {"Apache-2.0": 2},
            "source_row_count": 2,
        }
    )
    _write_ingredient_json(root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"], extraction_report)
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 1
    assert summary["selector_count"] == len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["nat_mul_one_v1"]
    assert recipes["recipes"][0]["domains"] == ["Nat"]
    assert recipes["recipes"][0]["required_ingredient_classes"] == ["nat_definition", "nat_fact"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["nat_mul_one_v1"]
    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    assert receipt.selected_recipe_id == "nat_mul_one_v1"
    assert receipt.selected_definition_ids == ("Nat.mul",)
    assert receipt.selected_fact_ids == ("Nat.mul_one",)
    template = root / "recipes" / "soundness_templates" / "nat_mul_one.lean"
    assert "theorem nat_mul_one_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_theorem_fact_dedup_recipe(
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition("List.dedup"),
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact("List.dedup_cons_of_mem", kind="theorem"),
    )
    (root / "recipes" / "soundness_templates" / "list_length.lean").unlink()
    extraction_report = json.loads(
        (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text(encoding="utf-8")
    )
    extraction_report.update(
        {
            "definition_count": 1,
            "fact_count": 1,
            "source_license_counts": {"Apache-2.0": 2},
            "source_row_count": 2,
        }
    )
    _write_ingredient_json(root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"], extraction_report)
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 1
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_dedup_pair_length_v1"]
    assert recipes["recipes"][0]["required_fact_kinds"] == ["theorem"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_dedup_pair_length_v1"]
    receipt = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    assert receipt.selected_recipe_id == "list_dedup_pair_length_v1"
    assert receipt.selected_definition_ids == ("List.dedup",)
    assert receipt.selected_fact_ids == ("List.dedup_cons_of_mem",)
    template = root / "recipes" / "soundness_templates" / "list_dedup_pair_length.lean"
    assert "theorem list_dedup_pair_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_reverse_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition(),
        _definition("List.reverse"),
    )
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_length_v1", "list_reverse_length_v1"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_length_v1", "list_reverse_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_reverse_length.lean"
    assert "theorem list_reverse_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_append_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition("List.append"),
        _definition(),
    )
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], _fact("List.length_append"))
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_append_length_v1", "list_length_v1"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_append_length_v1", "list_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_append_length.lean"
    assert "theorem list_append_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_drop_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition("List.drop"),
        _definition(),
    )
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], _fact("List.length_drop"))
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_drop_length_v1", "list_length_v1"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_drop_length_v1", "list_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_drop_length.lean"
    assert "theorem list_drop_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_filter_true_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition("List.filter"),
        _definition(),
    )
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], _fact("List.length_filter"))
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == [
        "list_filter_true_length_v1",
        "list_length_v1",
    ]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_filter_true_length_v1", "list_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_filter_true_length.lean"
    assert "theorem list_filter_true_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_map_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition(),
        _definition("List.map"),
    )
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_length_v1", "list_map_length_v1"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_length_v1", "list_map_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_map_length.lean"
    assert "theorem list_map_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_take_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition(),
        _definition("List.take"),
    )
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], _fact("List.length_take"))
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_length_v1", "list_take_length_v1"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_length_v1", "list_take_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_take_length.lean"
    assert "theorem list_take_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_range_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition(),
        _definition("List.range"),
    )
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], _fact("List.length_range"))
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_length_v1", "list_range_length_v1"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_length_v1", "list_range_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_range_length.lean"
    assert "theorem list_range_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_build_ingredient_compatibility_bootstraps_zip_length_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition(),
        _definition("List.zip"),
    )
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], _fact("List.length_zip"))
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"], {"recipes": []})
    _write_ingredient_json(root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"], {})
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": []},
    )
    for field in (
        "compatibility_graph_sha256",
        "source_compatibility_sha256",
        "definition_compatibility_sha256",
        "bridge_catalog_sha256",
        "recipe_selectors_sha256",
    ):
        _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field])

    summary = build_ingredient_compatibility(root)

    assert summary["status"] == "paid_compatibility"
    assert summary["recipe_count"] == 2
    assert summary["selector_count"] == 2 * len(DIFFICULTY_LANES)
    recipes = json.loads((root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8"))
    assert [recipe["recipe_id"] for recipe in recipes["recipes"]] == ["list_length_v1", "list_zip_length_v1"]
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["recipes"] == ["list_length_v1", "list_zip_length_v1"]
    template = root / "recipes" / "soundness_templates" / "list_zip_length.lean"
    assert "theorem list_zip_length_soundness (n : Nat)" in template.read_text(encoding="utf-8")


def test_quality_report_rejects_unready_reserve_selector_for_theorem_space(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    report_path = root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["reserve_selector_health"] = {"ready": False}
    _write_ingredient_json(report_path, report)

    with pytest.raises(ValueError, match="ingredient quality report reserve selector unavailable"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


def test_select_ingredient_receipt_from_root_rejects_unusable_source_facts(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    facts = _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        *(fact.model_copy(update={"metadata": {"usable_as_source_fact": False}}) for fact in facts),
    )

    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


def test_select_ingredient_receipt_from_root_rejects_disallowed_definition_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    other_recipe = _recipe().model_copy(update={"recipe_id": "z_list_length_other_v1"})
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {"recipes": [_recipe().model_dump(mode="json"), other_recipe.model_dump(mode="json")]},
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"],
        {"schema_version": 1, "recipes": ["list_length_v1", "z_list_length_other_v1"]},
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition().model_copy(update={"metadata": {"allowed_recipes": ["z_list_length_other_v1"]}}),
    )
    report_path = root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["recipe_count"] = 2
    _write_ingredient_json(report_path, report)

    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


def test_select_ingredient_receipt_from_root_rejects_definition_above_selector_simp_risk(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"],
        _definition().model_copy(update={"metadata": {"simp_risk": "high"}}),
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"],
        _selector().model_copy(update={"ingredient_filters": {"domains": ["List", "Nat"], "max_simp_risk": "medium"}}),
    )

    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


def test_select_ingredient_receipt_from_root_rejects_fact_below_selector_dependency_depth(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    facts = _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        *(fact.model_copy(update={"metadata": {"dependency_depth": 1}}) for fact in facts),
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"],
        _selector().model_copy(
            update={"ingredient_filters": {"domains": ["List", "Nat"], "min_dependency_depth": 2}}
        ),
    )

    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


def test_ingredient_statement_gate_receipt_binds_soundness_template(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    receipt = ingredient_statement_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="True",
        runner="declared-public-artifact",
        checks=("statement_hash_bound", "target_hash_bound", "soundness_template_bound"),
    )

    template = root / "recipes" / "soundness_templates" / "list_length.lean"
    assert receipt.runner == "declared-public-artifact"
    assert receipt.details == {
        "selected_selector_id": selection.selected_selector_id,
        "selected_recipe_id": "list_length_v1",
        "selected_parameters": selection.selected_parameters,
        "selected_parameters_sha256": canonical_sha256({"selected_parameters": selection.selected_parameters}),
        "soundness_template": "soundness_templates/list_length.lean",
        "soundness_template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "theorem_type_expr_sha256": text_sha256("True"),
    }


def test_ingredient_statement_gate_receipt_rejects_symlink_soundness_template(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    template = root / "recipes" / "soundness_templates" / "list_length.lean"
    external_template = tmp_path / "external-template.lean"
    external_template.write_bytes(template.read_bytes())
    template.unlink()
    template.symlink_to(external_template)

    with pytest.raises(ValueError, match="ingredient statement gate soundness template path invalid"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=("statement_hash_bound", "target_hash_bound", "soundness_template_bound"),
        )


def test_ingredient_statement_gate_receipt_binds_triviality_gate(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    probe = ingredient_triviality_probe_script(
        theorem_name="generated_list_length",
        theorem_type_expr="True",
        imports=("Mathlib",),
    )
    triviality_details = ingredient_triviality_gate_details(
        theorem_name="generated_list_length",
        theorem_type_expr="True",
        imports=("Mathlib",),
        verify_reason="compile_error",
        max_heartbeats=200_000,
    )

    receipt = ingredient_statement_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="True",
        runner="lean-statement-gate",
        checks=(
            "lean_challenge_typechecked",
            "lean_verify_reason:ok",
            "statement_hash_bound",
            "target_hash_bound",
            "soundness_template_bound",
            "bounded_triviality_checked",
            "baseline_triviality_not_solved",
            "bounded_triviality_reason:compile_error",
        ),
        triviality_details=triviality_details,
    )

    assert receipt.details["triviality_gate"] == triviality_details
    assert triviality_details["triviality_probe_sha256"] == text_sha256(probe)
    assert triviality_details["triviality_stack"][0] == "decide"


def test_ingredient_triviality_probe_rejects_non_public_import() -> None:
    with pytest.raises(ValueError, match="ingredient import invalid: Private.OperatorHints"):
        ingredient_triviality_probe_script(
            theorem_name="generated_list_length",
            theorem_type_expr="True",
            imports=("Private.OperatorHints",),
        )


def test_ingredient_statement_gate_receipt_requires_gate_checks(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="soundness_template_bound"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=("statement_hash_bound", "target_hash_bound"),
        )
    with pytest.raises(ValueError, match="ingredient statement gate check unsupported"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "operator_note:private",
            ),
        )
    with pytest.raises(ValueError, match="ingredient theorem type expression not canonical"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr=" True ",
            runner="declared-public-artifact",
            checks=("statement_hash_bound", "target_hash_bound", "soundness_template_bound"),
        )
    with pytest.raises(ValueError, match="ingredient gate selection receipt mismatch"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=_sha("4"),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=("statement_hash_bound", "target_hash_bound", "soundness_template_bound"),
        )
    with pytest.raises(ValueError, match="ingredient statement gate check duplicate"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "statement_hash_bound",
            ),
        )
    with pytest.raises(ValueError, match="ingredient statement gate check invalid"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="lean-statement-gate",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "lean_challenge_typechecked",
                "lean_verify_reason:private/path",
            ),
        )
    with pytest.raises(ValueError, match="ingredient statement gate Lean reason invalid"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="lean-statement-gate",
            checks=(
                "lean_challenge_typechecked",
                "lean_verify_reason:operator_note",
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
            ),
        )
    with pytest.raises(ValueError, match="lean_challenge_typechecked"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="lean-statement-gate",
            checks=("statement_hash_bound", "target_hash_bound", "soundness_template_bound"),
        )
    with pytest.raises(ValueError, match="soundness template reason missing"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "soundness_template_typechecked",
            ),
        )
    with pytest.raises(ValueError, match="soundness_template_no_holes"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "soundness_template_typechecked",
                "soundness_template_verify_reason:ok",
            ),
        )
    with pytest.raises(ValueError, match="soundness template reason invalid"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "soundness_template_typechecked",
                "soundness_template_no_holes",
                "soundness_template_verify_reason:operator_note",
            ),
        )
    with pytest.raises(ValueError, match="novelty details missing"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
            ),
        )
    with pytest.raises(ValueError, match="novelty family details missing"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
                "selection_family_not_in_novelty_cache",
            ),
            novelty_details=ingredient_novelty_gate_details(
                theorem_type_expr="True",
                novelty_cache=novelty_cache_from_hashes(("0" * 64,)),
            ),
        )
    with pytest.raises(ValueError, match="novelty family check missing"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
            ),
            novelty_details=ingredient_novelty_gate_details(
                theorem_type_expr="True",
                novelty_cache=novelty_cache_from_hashes(("0" * 64,)),
                selection=selection,
            ),
        )
    with pytest.raises(ValueError, match="lean_challenge_typechecked"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
                "selection_family_not_in_novelty_cache",
            ),
            novelty_details=ingredient_novelty_gate_details(
                theorem_type_expr="True",
                novelty_cache=novelty_cache_from_hashes(("0" * 64,)),
                selection=selection,
            ),
        )
    with pytest.raises(ValueError, match="baseline_triviality_not_solved"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "bounded_triviality_checked",
                "bounded_triviality_reason:compile_error",
            ),
        )
    with pytest.raises(ValueError, match="triviality details missing"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "bounded_triviality_checked",
                "baseline_triviality_not_solved",
                "bounded_triviality_reason:compile_error",
            ),
        )


@pytest.mark.parametrize("verify_reason", ("ok", " compile_error "))
def test_ingredient_triviality_gate_details_rejects_invalid_reason(verify_reason: str) -> None:
    with pytest.raises(ValueError, match="reason invalid"):
        ingredient_triviality_gate_details(
            theorem_name="generated_list_length",
            theorem_type_expr="True",
            imports=("Mathlib",),
            verify_reason=verify_reason,
            max_heartbeats=200_000,
        )


@pytest.mark.parametrize("max_heartbeats", (0, True))
def test_ingredient_triviality_gate_details_rejects_invalid_budget(max_heartbeats: int) -> None:
    with pytest.raises(ValueError, match="budget invalid"):
        ingredient_triviality_gate_details(
            theorem_name="generated_list_length",
            theorem_type_expr="True",
            imports=("Mathlib",),
            verify_reason="compile_error",
            max_heartbeats=max_heartbeats,
        )


@pytest.mark.parametrize(
    ("update", "message"),
    (
        ({"triviality_probe_sha256": "0" * 64}, "ingredient triviality probe sha256 placeholder"),
        ({"verify_reason": "operator_note"}, "ingredient statement gate triviality details invalid"),
        ({"triviality_stack": ["operator_note"]}, "ingredient statement gate triviality details invalid"),
        ({"operator_note": "private"}, "ingredient statement gate triviality details invalid"),
    ),
)
def test_statement_gate_rejects_invalid_triviality_details(
    update: dict[str, object],
    message: str,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    triviality_details = ingredient_triviality_gate_details(
        theorem_name="generated_list_length",
        theorem_type_expr="True",
        imports=("Mathlib",),
        verify_reason="compile_error",
        max_heartbeats=200_000,
    )
    triviality_details.update(update)

    with pytest.raises(ValueError, match=message):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "bounded_triviality_checked",
                "baseline_triviality_not_solved",
                "bounded_triviality_reason:compile_error",
            ),
            triviality_details=triviality_details,
        )


def test_ingredient_novelty_gate_details_rejects_cached_theorem_type() -> None:
    stale = novelty_cache_from_hashes((statement_hash("True"),))

    with pytest.raises(ValueError, match="theorem type already in novelty cache"):
        ingredient_novelty_gate_details(theorem_type_expr="True", novelty_cache=stale)

    with pytest.raises(ValueError, match="ingredient theorem type expression not canonical"):
        ingredient_novelty_gate_details(theorem_type_expr=" True ", novelty_cache=novelty_cache_from_hashes(()))

    stale_family = novelty_cache_from_hashes(
        (),
        novelty_family_hashes=(ingredient_novelty_family_hash(_selection()),),
    )
    with pytest.raises(ValueError, match="selection family already in novelty cache"):
        ingredient_novelty_gate_details(
            theorem_type_expr="True",
            novelty_cache=stale_family,
            selection=_selection(),
        )

    fresh = novelty_cache_from_hashes(("0" * 64,))
    details = ingredient_novelty_gate_details(
        theorem_type_expr="True",
        novelty_cache=fresh,
        selection=_selection(),
    )

    assert details["novelty_cache_entries"] == 1
    assert details["novelty_family_cache_entries"] == 0
    assert details["novelty_family_hash"] == ingredient_novelty_family_hash(_selection())
    assert details["novelty_cache_version"] == NOVELTY_CACHE_VERSION
    assert details["novelty_policy_check"] == "theorem_type_cache"
    assert details["novelty_statement_hash"] == statement_hash("True")


def test_statement_gate_rejects_novelty_check_absent_from_policy(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"],
        {
            "schema_version": 1,
            "novelty_cache_version": NOVELTY_CACHE_VERSION,
            "supported_checks": [],
        },
    )
    with pytest.raises(ValueError, match="ingredient novelty policy check unavailable"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
            ),
            novelty_details=ingredient_novelty_gate_details(
                theorem_type_expr="True",
                novelty_cache=novelty_cache_from_hashes(("0" * 64,)),
            ),
        )


def test_statement_gate_rejects_novelty_family_check_absent_from_policy(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"],
        {
            "schema_version": 1,
            "novelty_cache_version": NOVELTY_CACHE_VERSION,
            "supported_checks": ["theorem_type_cache"],
        },
    )

    with pytest.raises(ValueError, match="ingredient novelty policy check unavailable: selection_family_cache"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
                "selection_family_not_in_novelty_cache",
            ),
            novelty_details=ingredient_novelty_gate_details(
                theorem_type_expr="True",
                novelty_cache=novelty_cache_from_hashes(("0" * 64,)),
                selection=selection,
            ),
        )


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("novelty_cache_sha256", "ingredient novelty cache sha256 placeholder"),
        ("novelty_statement_hash", "ingredient novelty statement hash placeholder"),
        ("novelty_family_hash", "ingredient novelty family hash placeholder"),
    ),
)
def test_statement_gate_rejects_placeholder_novelty_hash_details(
    field: str,
    message: str,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    novelty_details = ingredient_novelty_gate_details(
        theorem_type_expr="True",
        novelty_cache=novelty_cache_from_hashes(("1" * 64,)),
        selection=selection,
    )
    novelty_details[field] = "0" * 64

    with pytest.raises(ValueError, match=message):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
                "selection_family_not_in_novelty_cache",
            ),
            novelty_details=novelty_details,
        )


@pytest.mark.parametrize(
    ("update", "drop"),
    (
        ({"operator_note": "private"}, None),
        ({"novelty_policy_check": "operator_note"}, None),
        ({"novelty_cache_entries": True}, None),
        ({"novelty_family_cache_entries": True}, None),
        ({}, "novelty_cache_version"),
    ),
)
def test_statement_gate_rejects_invalid_novelty_details(
    update: dict[str, object],
    drop: str | None,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    novelty_details = ingredient_novelty_gate_details(
        theorem_type_expr="True",
        novelty_cache=novelty_cache_from_hashes(("1" * 64,)),
        selection=selection,
    )
    novelty_details.update(update)
    if drop is not None:
        novelty_details.pop(drop)

    with pytest.raises(ValueError, match="ingredient statement gate novelty details invalid"):
        ingredient_statement_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            runner="declared-public-artifact",
            checks=(
                "statement_hash_bound",
                "target_hash_bound",
                "soundness_template_bound",
                "novelty_cache_bound",
                "theorem_type_not_in_novelty_cache",
                "selection_family_not_in_novelty_cache",
            ),
            novelty_details=novelty_details,
        )


def test_ingredient_shortcut_gate_receipt_binds_selected_fact_types(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    receipt = ingredient_shortcut_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="True",
        mathlib_commit="abc123",
    )

    assert receipt.runner == "source-oracle-exact-match-v1"
    assert receipt.checks == (
        "recipe_shortcut_policy_bound",
        "selected_facts_loaded",
        "source_fact_catalog_scanned",
        "no_source_fact_type_exact_match",
    )
    assert receipt.details["declared_shortcut_checks"] == ["source_oracle"]
    assert receipt.details["selected_fact_ids"] == list(selection.selected_fact_ids)
    assert receipt.details["selected_fact_type_sha256s"][selection.selected_fact_ids[0]] == text_sha256("...")
    assert receipt.details["selected_selector_id"] == selection.selected_selector_id
    assert receipt.details["selected_recipe_id"] == selection.selected_recipe_id
    assert receipt.details["source_oracle_mode"] == "exact_type_catalog_v1"
    assert receipt.details["source_fact_count"] == 2
    assert isinstance(receipt.details["source_fact_type_catalog_sha256"], str)
    assert receipt.details["theorem_type_expr_sha256"] == text_sha256("True")


def test_ingredient_shortcut_gate_receipt_binds_source_subterm_oracle(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_subterm_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_subterm_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "List.length ([] : List Nat) = 0"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    receipt = ingredient_shortcut_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="List.length ([0] : List Nat) = 1",
        mathlib_commit="abc123",
    )

    assert receipt.runner == "source-oracle-subterm-v1"
    assert receipt.checks == (
        "recipe_shortcut_policy_bound",
        "selected_facts_loaded",
        "source_fact_catalog_scanned",
        "no_source_fact_type_exact_match",
        "no_source_fact_type_subterm_match",
    )
    assert receipt.details["declared_shortcut_checks"] == ["source_oracle", "source_subterm_oracle"]
    assert receipt.details["source_subterm_oracle_mode"] == "normalized_type_substring_v1"
    assert receipt.details["source_subterm_match_count"] == 0


def test_ingredient_shortcut_gate_receipt_binds_numeric_skeleton_oracle(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_numeric_skeleton_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_numeric_skeleton_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "Nat.succ 0 = 1"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    receipt = ingredient_shortcut_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="Nat.pred 2 = 1",
        mathlib_commit="abc123",
    )

    assert receipt.runner == "source-oracle-semantic-v1"
    assert receipt.checks == (
        "recipe_shortcut_policy_bound",
        "selected_facts_loaded",
        "source_fact_catalog_scanned",
        "no_source_fact_type_exact_match",
        "no_source_fact_numeric_skeleton_match",
    )
    assert receipt.details["declared_shortcut_checks"] == ["source_oracle", "source_numeric_skeleton_oracle"]
    assert receipt.details["source_numeric_skeleton_match_count"] == 0
    assert receipt.details["source_numeric_skeleton_oracle_mode"] == "decimal_token_skeleton_v1"
    assert receipt.details["theorem_numeric_skeleton_sha256"] == text_sha256("Nat.pred # = #")


def test_ingredient_shortcut_gate_receipt_binds_shape_skeleton_oracle(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_shape_skeleton_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_shape_skeleton_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "Nat.succ 0 = 1"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    receipt = ingredient_shortcut_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="Nat.pred 2 = Nat.succ 1",
        mathlib_commit="abc123",
    )

    assert receipt.runner == "source-oracle-semantic-v1"
    assert receipt.checks == (
        "recipe_shortcut_policy_bound",
        "selected_facts_loaded",
        "source_fact_catalog_scanned",
        "no_source_fact_type_exact_match",
        "no_source_fact_type_shape_skeleton_match",
    )
    assert receipt.details["declared_shortcut_checks"] == ["source_oracle", "source_shape_skeleton_oracle"]
    assert receipt.details["source_shape_skeleton_match_count"] == 0
    assert receipt.details["source_shape_skeleton_oracle_mode"] == "identifier_decimal_token_skeleton_v1"
    assert receipt.details["theorem_shape_skeleton_sha256"] == text_sha256("ID # = ID #")


def test_ingredient_shortcut_gate_receipt_binds_token_multiset_oracle(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_token_multiset_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_token_multiset_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "Nat.succ 0 = 1"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    receipt = ingredient_shortcut_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="Nat.pred 2 = Nat.succ 1",
        mathlib_commit="abc123",
    )

    assert receipt.runner == "source-oracle-semantic-v1"
    assert receipt.checks == (
        "recipe_shortcut_policy_bound",
        "selected_facts_loaded",
        "source_fact_catalog_scanned",
        "no_source_fact_type_exact_match",
        "no_source_fact_token_multiset_match",
    )
    assert receipt.details["declared_shortcut_checks"] == ["source_oracle", "source_token_multiset_oracle"]
    assert receipt.details["source_token_multiset_match_count"] == 0
    assert receipt.details["source_token_multiset_oracle_mode"] == "identifier_operator_multiset_v1"
    assert receipt.details["theorem_token_multiset_sha256"] == canonical_sha256(
        {"tokens": ["=", "Nat.pred", "Nat.succ"]}
    )


def test_ingredient_shortcut_gate_receipt_binds_tactic_probe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "simp", "aesop", "omega", "grind"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "simp", "aesop", "omega", "grind")})
                .model_dump(mode="json")
            ]
        },
    )
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    tactics = ingredient_shortcut_tactics_for_selection(root, selection)
    probe = ingredient_shortcut_tactic_probe_script(
        theorem_name="generated_list_length",
        theorem_type_expr="True",
        imports=("Mathlib",),
        tactics=tactics,
    )
    tactic_details = ingredient_shortcut_tactic_gate_details(
        theorem_name="generated_list_length",
        theorem_type_expr="True",
        imports=("Mathlib",),
        tactics=tactics,
        verify_reason="compile_error",
        max_heartbeats=200_000,
    )

    receipt = ingredient_shortcut_gate_receipt(
        root,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("1"),
        theorem_statement_sha256=_sha("2"),
        ingredient_manifest_sha256=_sha("3"),
        selection_receipt_sha256=canonical_sha256(selection),
        theorem_type_expr="True",
        mathlib_commit="abc123",
        theorem_name="generated_list_length",
        imports=("Mathlib",),
        shortcut_tactic_details=tactic_details,
    )

    assert tactics == ("simp", "aesop", "omega", "grind")
    assert receipt.runner == "source-oracle-shortcut-tactics-v1"
    assert receipt.checks == (
        "recipe_shortcut_policy_bound",
        "selected_facts_loaded",
        "source_fact_catalog_scanned",
        "no_source_fact_type_exact_match",
        "shortcut_tactics_checked",
        "no_simp_shortcut",
        "no_aesop_shortcut",
        "no_omega_shortcut",
        "no_grind_shortcut",
        "shortcut_tactics_reason:compile_error",
    )
    assert receipt.details["declared_shortcut_checks"] == ["source_oracle", "simp", "aesop", "omega", "grind"]
    assert receipt.details["shortcut_tactic_gate"] == tactic_details
    assert tactic_details["shortcut_tactic_probe_sha256"] == text_sha256(probe)
    assert tactic_details["shortcut_tactics"] == ["simp", "aesop", "omega", "grind"]


def test_ingredient_shortcut_gate_receipt_requires_tactic_details(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "simp"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe().model_copy(update={"shortcut_checks": ("source_oracle", "simp")}).model_dump(mode="json")
            ]
        },
    )
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="ingredient shortcut tactic details missing"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            mathlib_commit="abc123",
        )


@pytest.mark.parametrize("verify_reason", ("ok", " compile_error "))
def test_ingredient_shortcut_tactic_gate_details_rejects_invalid_reason(verify_reason: str) -> None:
    with pytest.raises(ValueError, match="reason invalid"):
        ingredient_shortcut_tactic_gate_details(
            theorem_name="generated_list_length",
            theorem_type_expr="True",
            imports=("Mathlib",),
            tactics=("simp",),
            verify_reason=verify_reason,
            max_heartbeats=200_000,
        )


@pytest.mark.parametrize("max_heartbeats", (0, True))
def test_ingredient_shortcut_tactic_gate_details_rejects_invalid_budget(max_heartbeats: int) -> None:
    with pytest.raises(ValueError, match="budget invalid"):
        ingredient_shortcut_tactic_gate_details(
            theorem_name="generated_list_length",
            theorem_type_expr="True",
            imports=("Mathlib",),
            tactics=("simp",),
            verify_reason="compile_error",
            max_heartbeats=max_heartbeats,
        )


def test_ingredient_shortcut_tactic_probe_rejects_noncanonical_tactics() -> None:
    with pytest.raises(ValueError, match="ingredient shortcut tactic checks invalid"):
        ingredient_shortcut_tactic_probe_script(
            theorem_name="generated_list_length",
            theorem_type_expr="True",
            imports=("Mathlib",),
            tactics=("aesop", "simp"),
        )


def test_ingredient_shortcut_gate_receipt_rejects_exact_source_fact_match(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="source fact exactly matches theorem type"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="...",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_rejects_source_subterm_match(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_subterm_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_subterm_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "List.length ([] : List Nat) = 0"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="source fact type appears inside theorem type"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="And True (List.length ([] : List Nat) = 0)",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_rejects_numeric_skeleton_match(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_numeric_skeleton_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_numeric_skeleton_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "Nat.succ 0 = 1"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="source fact numeric skeleton matches theorem type"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="Nat.succ 2 = 3",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_rejects_shape_skeleton_match(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_shape_skeleton_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_shape_skeleton_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "Nat.succ 0 = 1"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="source fact shape skeleton matches theorem type"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="Nat.pred 2 = 3",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_rejects_token_multiset_match(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"],
        {"schema_version": 1, "supported_checks": ["source_oracle", "source_token_multiset_oracle"]},
    )
    _write_ingredient_json(
        root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"],
        {
            "recipes": [
                _recipe()
                .model_copy(update={"shortcut_checks": ("source_oracle", "source_token_multiset_oracle")})
                .model_dump(mode="json")
            ]
        },
    )
    _write_ingredient_jsonl(
        root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"],
        _fact().model_copy(update={"type_expr": "Nat.succ 0 = 1"}),
    )
    _write_selection_repo_fact_count(root, 1)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="source fact token multiset matches theorem type"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="1 = Nat.succ 2",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_rejects_noncanonical_theorem_type(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="ingredient theorem type expression not canonical"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr=" True ",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_rejects_selection_hash_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )

    with pytest.raises(ValueError, match="ingredient gate selection receipt mismatch"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=_sha("4"),
            theorem_type_expr="True",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_scans_nonselected_source_facts(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    facts = _write_selection_ingredient_repo(root)
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        mathlib_commit="abc123",
    )
    rows = []
    for fact in facts:
        type_expr = "List.length ([] : List Nat) = 0" if fact.fact_id in selection.selected_fact_ids else "True"
        rows.append(fact.model_copy(update={"type_expr": type_expr}))
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"], *rows)

    with pytest.raises(ValueError, match="source fact exactly matches theorem type"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            mathlib_commit="abc123",
        )


def test_ingredient_shortcut_gate_receipt_rejects_cross_catalog_fact_id_duplicate(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_jsonl(root / INGREDIENT_MANIFEST_COMPONENT_PATHS["source_lemmas_sha256"], _fact())
    selection = IngredientSelectionReceipt(
        selected_selector_id="hard_selector",
        selected_recipe_id="list_length_v1",
        selected_definition_ids=("List.length",),
        selected_fact_ids=("List.length_reverse",),
        selected_bridge_ids=("List.length_to_Nat",),
        selected_parameters={"Nat": "2"},
        difficulty_lane="hard",
        selection_seed_sha256=_sha("a"),
    )

    with pytest.raises(ValueError, match="ingredient fact catalog id duplicate: List.length_map"):
        ingredient_shortcut_gate_receipt(
            root,
            selection=selection,
            active_task_id="lemma.ingredient.list_length",
            active_target_sha256=_sha("1"),
            theorem_statement_sha256=_sha("2"),
            ingredient_manifest_sha256=_sha("3"),
            selection_receipt_sha256=canonical_sha256(selection),
            theorem_type_expr="True",
            mathlib_commit="abc123",
        )


def test_select_ingredient_receipt_from_root_rejects_unverified_reports(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    _write_ingredient_json(
        root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"],
        {
            "definition_count": 1,
            "fact_count": 1,
            "compatibility_edge_count": 1,
            "recipe_count": 1,
            "difficulty_lane_coverage": {"hard": 1},
                "bridge_coverage": {"List.length_to_Nat": 1},
                "estimated_theorem_space_size": 4,
                "shortcut_risk_distribution": {"paid_eligible": 1},
                "reserve_selector_health": {"ready": True},
        },
    )

    with pytest.raises(ValueError, match="ingredient report count mismatch: ingredient_quality_report:fact_count"):
        select_ingredient_receipt_from_root(
            root,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            mathlib_commit="abc123",
        )


def test_verify_ingredient_task_against_root_binds_public_selection_and_task_identity(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    manifest = ingredient_manifest_from_root(
        root,
        lemma_corpus_snapshot_sha256=_sha("f"),
    )
    manifest_sha256 = hashlib.sha256(ingredient_manifest_bytes(manifest)).hexdigest()
    seed = _sha("a")
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        mathlib_commit=manifest.mathlib_commit,
    )
    statement = "theorem generated_list_length : True := by\n  sorry"
    active_target_sha256 = _target_sha256(
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
    )
    theorem_statement_sha256 = text_sha256(statement)
    receipt = build_ingredient_generation_receipt(
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=manifest_sha256,
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit=manifest.mathlib_commit,
        recipe_bundle_sha256=manifest.recipe_bundle_sha256,
        difficulty_state_sha256=_sha("3"),
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=active_target_sha256,
        theorem_statement=statement,
        gate_receipt=_generation_gate_receipt(
            receipt_kind="statement_gate",
            active_target_sha256=active_target_sha256,
            theorem_statement_sha256=theorem_statement_sha256,
            ingredient_manifest_sha256=manifest_sha256,
            selection_receipt_sha256=canonical_sha256(selection),
        ),
        shortcut_receipt=_generation_gate_receipt(
            receipt_kind="shortcut_gate",
            active_target_sha256=active_target_sha256,
            theorem_statement_sha256=theorem_statement_sha256,
            ingredient_manifest_sha256=manifest_sha256,
            selection_receipt_sha256=canonical_sha256(selection),
        ),
    )
    task = build_fixture_ingredient_task(
        receipt=receipt,
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
    )

    verified = verify_ingredient_task_against_root(
        task,
        root,
        manifest=manifest,
        ingredient_manifest_sha256=manifest_sha256,
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
    )

    assert verified == receipt
    for field, value, message in (
        ("queue_position", 1, "ingredient task queue position mismatch"),
        ("queue_depth", -1, "ingredient task queue depth mismatch"),
        ("frontier_depth", None, "ingredient task frontier depth mismatch"),
        ("difficulty_band", "easy", "ingredient task difficulty band mismatch"),
    ):
        bad_task = task.model_copy(update={field: value})
        with pytest.raises(ValueError, match=message):
            verify_ingredient_task_against_root(
                bad_task,
                root,
                manifest=manifest,
                ingredient_manifest_sha256=manifest_sha256,
                challenge_seed_sha256=seed,
                difficulty_lane="hard",
            )
    bad_task = task.model_copy(update={"source_license": "MIT"})
    with pytest.raises(ValueError, match="ingredient task source license mismatch"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(update={"policy": "strict_envelope"})
    with pytest.raises(ValueError, match="ingredient task submission policy mismatch"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(update={"lean_toolchain": "private/toolchain"})
    with pytest.raises(ValueError, match="ingredient task lean toolchain mismatch"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(update={"imports": ("Private.OperatorHints",)})
    with pytest.raises(ValueError, match="ingredient import invalid: Private.OperatorHints"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(update={"metadata": {**task.metadata, "operator_hint": "private"}})
    with pytest.raises(ValueError, match="ingredient task metadata schema mismatch"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(
        update={"metadata": {**task.metadata, "builder_receipt_sha256s": ["0" * 64]}}
    )
    with pytest.raises(ValueError, match="ingredient task metadata schema mismatch"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(update={"theorem_name": "other_list_length"})
    with pytest.raises(ValueError, match="ingredient theorem statement header mismatch"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(update={"statement": f"{statement}\n\naxiom hidden_hint : False"})
    with pytest.raises(ValueError, match="ingredient theorem statement body invalid"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_metadata = {**task.metadata, "ingredient_repo_commit": "private/path"}
    bad_task = task.model_copy(update={"metadata": bad_metadata})
    with pytest.raises(ValidationError, match="ingredient_repo_commit"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_metadata = {**task.metadata, "mathlib_commit": "private/path"}
    bad_task = task.model_copy(update={"metadata": bad_metadata})
    with pytest.raises(ValidationError, match="mathlib_commit"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    bad_task = task.model_copy(update={"id": "private/path"})
    with pytest.raises(ValidationError, match="active_task_id"):
        verify_ingredient_task_against_root(
            bad_task,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    assert (
        verify_ingredient_generation_receipt_artifact(
            task,
            receipt,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
        == receipt
    )
    envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id="signer.alpha",
        signature="sig.alpha",
    )
    assert (
        verify_ingredient_generation_receipt_envelope(
            task,
            envelope,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
        == envelope
    )
    keypair = Keypair.create_from_uri("//LemmaIngredientSignerAlpha")
    signable = ingredient_generation_receipt_envelope(
        receipt,
        signer_id=keypair.ss58_address,
        signature="pending",
    )
    signed_envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id=keypair.ss58_address,
        signature="0x"
        + keypair.sign(ingredient_generation_receipt_envelope_signing_payload(signable)).hex(),
    )
    assert (
        verify_ingredient_generation_receipt_envelope(
            task,
            signed_envelope,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            signature_verifier=Ss58IngredientEnvelopeSignatureVerifier(),
        )
        == signed_envelope
    )
    bad_signer = Keypair.create_from_uri("//LemmaIngredientSignerImpostor")
    bad_signature = "0x" + bad_signer.sign(
        ingredient_generation_receipt_envelope_signing_payload(signable)
    ).hex()
    with pytest.raises(
        ValueError,
        match="ingredient generation receipt envelope signature verification failed",
    ):
        verify_ingredient_generation_receipt_envelope(
            task,
            signed_envelope.model_copy(update={"signature": bad_signature}),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            signature_verifier=Ss58IngredientEnvelopeSignatureVerifier(),
        )
    with pytest.raises(ValueError, match="ingredient generation receipt envelope signature missing"):
        verify_ingredient_generation_receipt_envelope(
            task,
            ingredient_generation_receipt_envelope(receipt),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            signature_verifier=Ss58IngredientEnvelopeSignatureVerifier(),
        )
    second_envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id="signer.beta",
        signature="sig.beta",
    )
    assert (
        verify_ingredient_generation_receipt_envelope_quorum(
            task,
            (envelope, second_envelope),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            quorum=2,
        )
        == (envelope, second_envelope)
    )
    with pytest.raises(ValueError, match="ingredient generation receipt envelope quorum shortfall"):
        verify_ingredient_generation_receipt_envelope_quorum(
            task,
            (envelope,),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            quorum=2,
        )
    with pytest.raises(ValueError, match="ingredient generation receipt envelope duplicate"):
        verify_ingredient_generation_receipt_envelope_quorum(
            task,
            (envelope, envelope),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            quorum=2,
        )
    with pytest.raises(
        ValueError,
        match="ingredient generation receipt envelope signer metadata required for quorum",
    ):
        verify_ingredient_generation_receipt_envelope_quorum(
            task,
            (envelope, ingredient_generation_receipt_envelope(receipt)),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            quorum=2,
        )
    with pytest.raises(ValueError, match="ingredient generation receipt envelope duplicate signer"):
        verify_ingredient_generation_receipt_envelope_quorum(
            task,
            (
                envelope,
                ingredient_generation_receipt_envelope(
                    receipt,
                    signer_id="signer.alpha",
                    signature="sig.other",
                ),
            ),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
            quorum=2,
        )
    with pytest.raises(ValueError, match="ingredient generation receipt envelope hash mismatch"):
        verify_ingredient_generation_receipt_envelope(
            task,
            envelope.model_copy(update={"generation_receipt_sha256": _sha("0")}),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    with pytest.raises(
        ValueError,
        match="ingredient generation receipt envelope signature metadata mismatch",
    ):
        verify_ingredient_generation_receipt_envelope(
            task,
            ingredient_generation_receipt_envelope(
                receipt,
                signer_id="signer.alpha",
                signature="sig.alpha",
            ).model_copy(update={"signature": None}),
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )
    drifted_receipt = receipt.model_copy(update={"gate_receipt_sha256": _sha("8")})
    with pytest.raises(ValueError, match="ingredient generation receipt artifact mismatch"):
        verify_ingredient_generation_receipt_artifact(
            task,
            drifted_receipt,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=seed,
            difficulty_lane="hard",
        )


def test_verify_ingredient_task_against_root_rejects_selection_drift(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_selection_ingredient_repo(root)
    manifest = ingredient_manifest_from_root(root, lemma_corpus_snapshot_sha256=_sha("f"))
    manifest_sha256 = hashlib.sha256(ingredient_manifest_bytes(manifest)).hexdigest()
    task = build_fixture_ingredient_registry(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=manifest_sha256,
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit=manifest.mathlib_commit,
        recipe_bundle_sha256=manifest.recipe_bundle_sha256,
        difficulty_state_sha256=_sha("3"),
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(),),
        bridges=(
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        parameter_sets={"Nat": ("2", "3")},
        theorem_name="generated_list_length",
        type_expr="True",
        statement="theorem generated_list_length : True := by\n  sorry",
        active_task_id="lemma.ingredient.list_length",
        gate_receipt_sha256=_sha("6"),
        shortcut_receipt_sha256=_sha("7"),
    ).tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "fact_ids": ["List.not_selected"]}})

    with pytest.raises(ValueError, match="ingredient task selection mismatch"):
        verify_ingredient_task_against_root(
            drifted,
            root,
            manifest=manifest,
            ingredient_manifest_sha256=manifest_sha256,
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
        )


def test_select_fixture_ingredients_returns_compatible_receipt() -> None:
    receipt = select_fixture_ingredients(
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(),),
        bridges=(
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        parameter_sets={"Nat": ("2", "3")},
    )

    assert receipt.selected_recipe_id == "list_length_v1"
    assert receipt.selected_definition_ids == ("List.length",)
    assert receipt.selected_fact_ids == ("List.length_map",)
    assert receipt.selected_bridge_ids == ("List.length_to_Nat",)
    assert receipt.selected_parameters["Nat"] in {"2", "3"}
    assert receipt.selection_seed_sha256 == _sha("a")


def test_select_fixture_ingredients_omits_parameters_for_none_rule() -> None:
    recipe = _recipe().model_copy(update={"parameter_rule": "none"})
    receipt = select_fixture_ingredients(
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(recipe,),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(),),
        bridges=(
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        parameter_sets={"Nat": ("2", "3")},
    )

    assert receipt.selected_parameters == {}


def test_select_fixture_ingredients_rejects_missing_finite_nat_parameter_set() -> None:
    with pytest.raises(ValueError, match="ingredient recipe parameter set missing: recipe_rules:list_length_v1:Nat"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(_selector(),),
            recipes=(_recipe(),),
            definitions=(_definition(),),
            facts=(_fact(),),
            compatibility_edges=(_edge(),),
            bridges=(
                BridgeRule(
                    bridge_id="List.length_to_Nat",
                    from_domain="List",
                    to_domain="Nat",
                    safe_recipes=("list_length_v1",),
                ),
            ),
            parameter_sets={},
        )


def test_select_fixture_ingredients_hash_orders_bool_parameters() -> None:
    seed = _sha("b")
    values = ("true", "false")
    expected = min(
        values,
        key=lambda value: canonical_sha256(
            {
                "seed": seed,
                "label": "list_length_v1:parameter:Bool",
                "key": canonical_sha256({"value": value}),
            }
        ),
    )

    receipt = select_fixture_ingredients(
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe().model_copy(update={"parameter_rule": "finite_bool"}),),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(bridge_ids=()),),
        parameter_sets={"Bool": values},
    )

    assert receipt.selected_parameters == {"Bool": expected}


def test_select_fixture_ingredients_rejects_missing_finite_bool_parameter_set() -> None:
    with pytest.raises(ValueError, match="ingredient recipe parameter set missing: recipe_rules:list_length_v1:Bool"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(_selector(),),
            recipes=(_recipe().model_copy(update={"parameter_rule": "finite_bool"}),),
            definitions=(_definition(),),
            facts=(_fact(),),
            compatibility_edges=(_edge(bridge_ids=()),),
            parameter_sets={},
        )


def test_select_fixture_ingredients_hash_orders_int_parameters() -> None:
    seed = _sha("b")
    values = ("-1", "0", "2")
    expected = min(
        values,
        key=lambda value: canonical_sha256(
            {
                "seed": seed,
                "label": "list_length_v1:parameter:Int",
                "key": canonical_sha256({"value": value}),
            }
        ),
    )

    receipt = select_fixture_ingredients(
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe().model_copy(update={"parameter_rule": "finite_int"}),),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(bridge_ids=()),),
        parameter_sets={"Int": values},
    )

    assert receipt.selected_parameters == {"Int": expected}


def test_select_fixture_ingredients_rejects_missing_finite_int_parameter_set() -> None:
    with pytest.raises(ValueError, match="ingredient recipe parameter set missing: recipe_rules:list_length_v1:Int"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(_selector(),),
            recipes=(_recipe().model_copy(update={"parameter_rule": "finite_int"}),),
            definitions=(_definition(),),
            facts=(_fact(),),
            compatibility_edges=(_edge(bridge_ids=()),),
            parameter_sets={},
        )


def test_select_fixture_ingredients_hash_orders_fact_candidates() -> None:
    seed = _sha("b")
    facts = (_fact("List.length_map"), _fact("List.length_reverse"))
    expected = min(
        facts,
        key=lambda fact: canonical_sha256(
            {"seed": seed, "label": "list_length_v1:fact:lemma:0", "key": fact.fact_id}
        ),
    )

    receipt = select_fixture_ingredients(
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=facts,
        compatibility_edges=(_edge(bridge_ids=()),),
        parameter_sets={"Nat": ("2",)},
    )

    assert receipt.selected_fact_ids == (expected.fact_id,)


def test_select_fixture_ingredients_skips_unusable_source_facts() -> None:
    receipt = select_fixture_ingredients(
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        selectors=(_selector(),),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=(
            _fact("List.length_map").model_copy(update={"metadata": {"usable_as_source_fact": False}}),
            _fact("List.length_reverse").model_copy(update={"metadata": {"usable_as_source_fact": True}}),
        ),
        compatibility_edges=(_edge(bridge_ids=()),),
        parameter_sets={"Nat": ("2",)},
    )

    assert receipt.selected_fact_ids == ("List.length_reverse",)


def test_select_fixture_ingredients_skips_disallowed_definition_recipe() -> None:
    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(_selector(),),
            recipes=(_recipe(),),
            definitions=(
                _definition().model_copy(update={"metadata": {"allowed_recipes": ["list_length_other_v1"]}}),
            ),
            facts=(_fact(),),
            compatibility_edges=(_edge(bridge_ids=()),),
            parameter_sets={"Nat": ("2",)},
        )


def test_select_fixture_ingredients_applies_selector_max_simp_risk() -> None:
    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(
                _selector().model_copy(
                    update={"ingredient_filters": {"domains": ["List", "Nat"], "max_simp_risk": "medium"}}
                ),
            ),
            recipes=(_recipe(),),
            definitions=(_definition().model_copy(update={"metadata": {"simp_risk": "high"}}),),
            facts=(_fact(),),
            compatibility_edges=(_edge(bridge_ids=()),),
            parameter_sets={"Nat": ("2",)},
        )

    receipt = select_fixture_ingredients(
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        selectors=(
            _selector().model_copy(
                update={"ingredient_filters": {"domains": ["List", "Nat"], "max_simp_risk": "medium"}}
            ),
        ),
        recipes=(_recipe(),),
        definitions=(_definition().model_copy(update={"metadata": {"simp_risk": "medium"}}),),
        facts=(_fact(),),
        compatibility_edges=(_edge(bridge_ids=()),),
        parameter_sets={"Nat": ("2",)},
    )

    assert receipt.selected_definition_ids == ("List.length",)


def test_select_fixture_ingredients_applies_selector_min_dependency_depth() -> None:
    receipt = select_fixture_ingredients(
        challenge_seed_sha256=_sha("a"),
        difficulty_lane="hard",
        selectors=(
            _selector().model_copy(
                update={"ingredient_filters": {"domains": ["List", "Nat"], "min_dependency_depth": 2}}
            ),
        ),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=(
            _fact("List.length_map").model_copy(update={"metadata": {"dependency_depth": 1}}),
            _fact("List.length_reverse").model_copy(update={"metadata": {"dependency_depth": 2}}),
        ),
        compatibility_edges=(_edge(bridge_ids=()),),
        parameter_sets={"Nat": ("2",)},
    )

    assert receipt.selected_fact_ids == ("List.length_reverse",)

    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(
                _selector().model_copy(
                    update={"ingredient_filters": {"domains": ["List", "Nat"], "min_dependency_depth": 2}}
                ),
            ),
            recipes=(_recipe(),),
            definitions=(_definition(),),
            facts=(_fact().model_copy(update={"metadata": {"dependency_depth": 1}}),),
            compatibility_edges=(_edge(bridge_ids=()),),
            parameter_sets={"Nat": ("2",)},
        )


def test_select_fixture_ingredients_skips_bridge_domain_mismatch() -> None:
    with pytest.raises(ValueError, match="no compatible ingredient selection for difficulty lane: hard"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(_selector(),),
            recipes=(_recipe(),),
            definitions=(_definition(),),
            facts=(_fact(),),
            compatibility_edges=(_edge(),),
            bridges=(
                BridgeRule(
                    bridge_id="List.length_to_Nat",
                    from_domain="List",
                    to_domain="Order",
                    safe_recipes=("list_length_v1",),
                ),
            ),
            parameter_sets={"Nat": ("2",)},
        )


def test_fixture_reserve_selector_is_deterministic_and_emits_valid_task() -> None:
    def reserve_selectors(seed: str) -> tuple[RecipeSelector, RecipeSelector]:
        first, second = sorted(
            ("reserve_alpha", "reserve_beta"),
            key=lambda selector_id: canonical_sha256({"seed": seed, "label": "selector", "key": selector_id}),
        )
        return (
            RecipeSelector(selector_id=first, difficulty_lane="hard", recipe_ids=("missing_recipe_v1",)),
            RecipeSelector(selector_id=second, difficulty_lane="hard", recipe_ids=("list_length_v1",)),
        )

    seed = _sha("f")
    selectors = reserve_selectors(seed)
    kwargs = {
        "challenge_seed_sha256": seed,
        "difficulty_lane": "hard",
        "selectors": tuple(reversed(selectors)),
        "recipes": (_recipe(),),
        "definitions": (_definition(),),
        "facts": (_fact(),),
        "compatibility_edges": (_edge(),),
        "bridges": (
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            ),
        ),
        "parameter_sets": {"Nat": ("2", "3")},
    }

    receipt = select_fixture_ingredients(**kwargs)
    replayed = select_fixture_ingredients(**kwargs)

    assert receipt == replayed
    assert receipt.selected_selector_id == selectors[1].selector_id
    assert receipt.selected_recipe_id == "list_length_v1"

    challenge_seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
    )
    registry = build_fixture_ingredient_registry(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=_sha("1"),
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit="abc123",
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
        difficulty_lane="hard",
        selectors=tuple(reversed(reserve_selectors(challenge_seed))),
        recipes=(_recipe(),),
        definitions=(_definition(),),
        facts=(_fact(),),
        compatibility_edges=(_edge(),),
        bridges=kwargs["bridges"],
        parameter_sets=kwargs["parameter_sets"],
        theorem_name="generated_list_length",
        type_expr="True",
        statement="theorem generated_list_length : True := by\n  sorry",
        active_task_id="lemma.ingredient.list_length",
        gate_receipt_sha256=_sha("6"),
        shortcut_receipt_sha256=_sha("7"),
    )

    task = registry.tasks[0]
    assert task.metadata["selector_id"] == reserve_selectors(challenge_seed)[1].selector_id
    assert task.metadata["recipe_id"] == "list_length_v1"
    assert task.source_ref.name == "list_length_v1"
    assert expected_ingredient_generation_receipt_sha256(task) == task.metadata["generation_receipt_sha256"]


def test_select_fixture_ingredients_fails_closed_without_required_bridge() -> None:
    with pytest.raises(ValueError, match="no compatible ingredient selection"):
        select_fixture_ingredients(
            challenge_seed_sha256=_sha("a"),
            difficulty_lane="hard",
            selectors=(_selector(),),
            recipes=(_recipe(),),
            definitions=(_definition(),),
            facts=(_fact(),),
            compatibility_edges=(_edge(),),
            bridges=(),
        )


def test_generation_receipt_accepts_dynamic_active_k() -> None:
    receipt = IngredientGenerationReceipt(
        schema_version=1,
        tempo=42,
        active_K=2,
        epoch_seed_sha256=_sha("a"),
        ingredient_manifest_sha256=_sha("1"),
        lemma_corpus_snapshot_sha256=_sha("f"),
        ingredient_repo_commit="abc123",
        mathlib_commit="abc123",
        recipe_bundle_sha256=_sha("2"),
        difficulty_state_sha256=_sha("3"),
        selection=_selection(),
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=_sha("4"),
        theorem_statement_sha256=_sha("5"),
        gate_receipt_sha256=_sha("6"),
        shortcut_receipt_sha256=_sha("7"),
    )

    assert receipt.active_K == 2


def test_ingredient_receipts_reject_extra_fields() -> None:
    with pytest.raises(ValidationError, match="private_seed"):
        IngredientSelectionReceipt.model_validate(
            {
                "selected_recipe_id": "list_length_v1",
                "difficulty_lane": "hard",
                "selection_seed_sha256": _sha("1"),
                "private_seed": "nope",
            }
        )
    receipt = _receipt_for_statement("theorem generated_list_length : True := by\n  sorry")
    with pytest.raises(ValidationError, match="private_seed"):
        IngredientGenerationReceiptEnvelope.model_validate(
            {
                "schema_version": 1,
                "generation_receipt_sha256": canonical_sha256(receipt),
                "generation_receipt": receipt.model_dump(mode="json"),
                "private_seed": "nope",
            }
        )

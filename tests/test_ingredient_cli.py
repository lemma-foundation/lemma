"""CLI tests for ingredient task artifact commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from bittensor_wallet import Keypair
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.problems.base import Problem
from lemma.supply.ingredients import (
    DIFFICULTY_LANES,
    INGREDIENT_MANIFEST_COMPONENT_PATHS,
    INGREDIENT_QUALITY_REPORT_KEYS,
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
    RecipeRule,
    RecipeSelector,
    build_fixture_ingredient_task,
    build_ingredient_generation_receipt,
    canonical_json_bytes,
    canonical_sha256,
    ingredient_challenge_seed_sha256,
    ingredient_challenge_slot_seed_sha256,
    ingredient_generation_receipt_envelope,
    ingredient_generation_receipt_envelope_signing_payload,
    ingredient_novelty_family_hash,
    select_ingredient_receipt_from_root,
    text_sha256,
)
from lemma.supply.novelty import NOVELTY_CACHE_VERSION
from lemma.tasks import load_task_registry, problem_target_sha256
from lemma.validator import task_registry_for_validation
from pydantic import BaseModel


def _ingredient_manifest_json(component_hashes: dict[str, str] | None = None) -> str:
    payload = {
        "schema_version": 1,
        "mathlib_commit": "abc123",
        "lemma_corpus_snapshot_sha256": "f" * 64,
        "definitions_sha256": "a" * 64,
        "facts_sha256": "1" * 64,
        "source_theorems_sha256": "2" * 64,
        "source_lemmas_sha256": "3" * 64,
        "compatibility_graph_sha256": "4" * 64,
        "source_compatibility_sha256": "5" * 64,
        "definition_compatibility_sha256": "6" * 64,
        "bridge_catalog_sha256": "7" * 64,
        "recipe_selectors_sha256": "8" * 64,
        "recipe_bundle_sha256": "9" * 64,
        "difficulty_ladder_sha256": "b" * 64,
        "difficulty_retarget_sha256": "c" * 64,
        "novelty_policy_sha256": "d" * 64,
        "shortcut_policy_sha256": "e" * 64,
        "reserve_selector_policy_sha256": "0" * 63 + "1",
    }
    if component_hashes is not None:
        payload.update(component_hashes)
    return canonical_json_bytes(IngredientManifest(**payload)).decode("utf-8") + "\n"


def _write_ingredient_component_tree(root: Path) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mathlib_commit.txt").write_text("abc123\n", encoding="utf-8")
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).parent.mkdir(parents=True, exist_ok=True)
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "mathlib_commit": "abc123",
                "source_row_count": 4,
                "definition_count": 1,
                "fact_count": 3,
                "source_license_counts": {"Apache-2.0": 4},
            }
        )
    )
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).write_bytes(
        _ingredient_json_bytes(
            {
                "definition_count": 1,
                "fact_count": 3,
                "compatibility_edge_count": 3,
                "recipe_count": 1,
                "difficulty_lane_coverage": {"hard": 1},
                "bridge_coverage": {"List.length_to_Nat": 1},
                "estimated_theorem_space_size": 1,
                "shortcut_risk_distribution": {"paid_eligible": 1},
                "reserve_selector_health": {"ready": True},
            }
        )
    )
    for artifact_id, relative_path in INGREDIENT_RECIPE_ARTIFACT_PATHS.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if artifact_id == "recipe_rules":
            payload = {
                "recipes": [
                    RecipeRule(
                        recipe_id="list_length_v1",
                        version=1,
                        domains=("List", "Nat"),
                        required_ingredient_classes=("list_definition", "list_fact"),
                        required_definitions=("List.length",),
                        required_fact_kinds=("lemma",),
                        parameter_rule="finite_nat",
                        soundness_template="soundness_templates/fixture.lean",
                        shortcut_checks=("source_oracle",),
                    ).model_dump(mode="json")
                ]
            }
        else:
            payload = {"Nat": ["2"]}
        path.write_bytes(_ingredient_json_bytes(payload))
    template = root / "recipes" / "soundness_templates" / "fixture.lean"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        "import Mathlib\n\n"
        "theorem list_length_soundness (n : Nat) : List.length (List.replicate n 0) = n := by\n"
        "  simp\n",
        encoding="utf-8",
    )
    hashes = {}
    for field, relative_path in INGREDIENT_MANIFEST_COMPONENT_PATHS.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _ingredient_component_bytes(field)
        path.write_bytes(content)
        hashes[field] = hashlib.sha256(content).hexdigest()
    return hashes


def _ingredient_component_bytes(field: str) -> bytes:
    if field == "definitions_sha256":
        return _ingredient_row_bytes(
            DefinitionIngredient(
                definition_id="List.length",
                lean_name="List.length",
                domain="List",
                type_signature="List α -> Nat",
                imports=("Mathlib",),
                source_path="Mathlib/Data/List/Basic.lean",
                mathlib_commit="abc123",
            )
        )
    if field in {"facts_sha256", "source_theorems_sha256", "source_lemmas_sha256"}:
        return _ingredient_row_bytes(
            FactIngredient(
                fact_id=f"{field}.length_fact",
                lean_name=f"{field}.length_fact",
                kind="lemma",
                domain="List",
                type_expr="List.length ([] : List Nat) = 0",
                imports=("Mathlib",),
                source_path="Mathlib/Data/List/Basic.lean",
                mathlib_commit="abc123",
                difficulty_hint=1,
            )
        )
    if field in {"compatibility_graph_sha256", "source_compatibility_sha256", "definition_compatibility_sha256"}:
        return _ingredient_row_bytes(
            CompatibilityEdge(
                edge_id=f"{field}.edge",
                recipe_id="list_length_v1",
                ingredient_class="list_fact",
                allowed_domains=("List",),
                allowed_definition_ids=("List.length",),
                allowed_fact_patterns=("length",),
                bridge_ids=("List.length_to_Nat",),
                difficulty_lanes=("hard",),
                certification_receipt_sha256="1" * 64,
            )
        )
    if field == "bridge_catalog_sha256":
        return _ingredient_row_bytes(
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("list_length_v1",),
            )
        )
    if field == "recipe_selectors_sha256":
        return _ingredient_row_bytes(
            RecipeSelector(
                selector_id="hard_list_length_selector_v1",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
            )
        )
    if field == "recipe_bundle_sha256":
        return _ingredient_json_bytes({"schema_version": 1, "recipes": ["list_length_v1"]})
    if field == "shortcut_policy_sha256":
        return _ingredient_json_bytes({"schema_version": 1, "supported_checks": ["source_oracle"]})
    if field == "novelty_policy_sha256":
        return _ingredient_json_bytes(
            {
                "schema_version": 1,
                "novelty_cache_version": NOVELTY_CACHE_VERSION,
                "supported_checks": ["theorem_type_cache", "selection_family_cache"],
            }
        )
    if field == "difficulty_ladder_sha256":
        return _ingredient_json_bytes({"schema_version": 1, "difficulty_lanes": list(DIFFICULTY_LANES)})
    if field == "difficulty_retarget_sha256":
        return _ingredient_json_bytes(
            {
                "schema_version": 1,
                "retarget_mode": "manual_state_v1",
                "state_schema": "tempo_lane_v1",
            }
        )
    if field == "reserve_selector_policy_sha256":
        return _ingredient_json_bytes(
            {
                "schema_version": 1,
                "reserve_enabled": True,
                "selection_method": "hash_order_first_eligible",
            }
        )
    return _ingredient_json_bytes({"schema_version": 1, "component": field})


def _ingredient_json_bytes(payload: dict[str, object]) -> bytes:
    return canonical_json_bytes(payload) + b"\n"


def _difficulty_state_jsonl(*rows: dict[str, object]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _ingredient_row_bytes(row: BaseModel) -> bytes:
    return canonical_json_bytes(row) + b"\n"


def _write_ingredient_component_row(
    root: Path,
    component_hashes: dict[str, str],
    field: str,
    row: BaseModel,
) -> None:
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field]
    path.write_bytes(_ingredient_row_bytes(row))
    component_hashes[field] = hashlib.sha256(path.read_bytes()).hexdigest()


def _write_ingredient_component_rows(
    root: Path,
    component_hashes: dict[str, str],
    field: str,
    *rows: BaseModel,
) -> None:
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field]
    path.write_bytes(b"".join(_ingredient_row_bytes(row) for row in rows))
    component_hashes[field] = hashlib.sha256(path.read_bytes()).hexdigest()


def _write_recipe_rules(root: Path, *recipes: RecipeRule) -> None:
    path = root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]
    path.write_bytes(
        _ingredient_json_bytes({"recipes": [recipe.model_dump(mode="json") for recipe in recipes]})
    )
    parameter_sets = {}
    if any(recipe.parameter_rule == "finite_nat" for recipe in recipes):
        parameter_sets["Nat"] = ["2"]
    if any(recipe.parameter_rule == "finite_bool" for recipe in recipes):
        parameter_sets["Bool"] = ["true"]
    if any(recipe.parameter_rule == "finite_int" for recipe in recipes):
        parameter_sets["Int"] = ["-1"]
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes(parameter_sets)
    )


def _write_single_extra_definition_recipe_component_tree(
    root: Path,
    *,
    recipe_id: str,
    extra_definition_id: str,
    extra_definition_type: str,
    fact_pattern: str,
    fact_type_expr: str,
    soundness_template: str,
    soundness_theorem: str,
    soundness_type_expr: str,
    base_definition_id: str | None = "List.length",
    base_definition_domain: str = "List",
    base_definition_type: str = "List α -> Nat",
    extra_definition_domain: str = "List",
    fact_domain: str = "List",
    ingredient_class: str = "list_fact",
    recipe_domains: tuple[str, ...] = ("List", "Nat"),
    required_ingredient_classes: tuple[str, ...] = ("list_definition", "list_fact"),
    source_path: str = "Mathlib/Data/List/Basic.lean",
) -> dict[str, str]:
    component_hashes = _write_ingredient_component_tree(root)
    required_definitions = tuple(
        sorted(definition_id for definition_id in (base_definition_id, extra_definition_id) if definition_id)
    )
    definitions = [
        DefinitionIngredient(
            definition_id=extra_definition_id,
            lean_name=extra_definition_id,
            domain=extra_definition_domain,
            type_signature=extra_definition_type,
            imports=("Mathlib",),
            source_path=source_path,
            mathlib_commit="abc123",
        )
    ]
    if base_definition_id is not None:
        definitions.append(
            DefinitionIngredient(
                definition_id=base_definition_id,
                lean_name=base_definition_id,
                domain=base_definition_domain,
                type_signature=base_definition_type,
                imports=("Mathlib",),
                source_path=source_path,
                mathlib_commit="abc123",
            )
        )
    _write_ingredient_component_rows(
        root,
        component_hashes,
        "definitions_sha256",
        *sorted(definitions, key=lambda definition: definition.definition_id),
    )
    _write_ingredient_component_rows(
        root,
        component_hashes,
        "facts_sha256",
        FactIngredient(
            fact_id=f"facts_sha256.{fact_pattern}_length_fact",
            lean_name=f"facts_sha256.{fact_pattern}_length_fact",
            kind="lemma",
            domain=fact_domain,
            type_expr=fact_type_expr,
            imports=("Mathlib",),
            source_path=source_path,
            mathlib_commit="abc123",
            difficulty_hint=1,
        ),
    )
    for field in ("source_theorems_sha256", "source_lemmas_sha256", "bridge_catalog_sha256"):
        _write_ingredient_component_rows(root, component_hashes, field)
    _write_ingredient_component_rows(
        root,
        component_hashes,
        "compatibility_graph_sha256",
        CompatibilityEdge(
            edge_id=f"{recipe_id}.edge.{fact_pattern}",
            recipe_id=recipe_id,
            ingredient_class=ingredient_class,
            allowed_domains=recipe_domains,
            allowed_definition_ids=required_definitions,
            allowed_fact_patterns=(fact_pattern,),
            difficulty_lanes=("hard",),
            certification_receipt_sha256="1" * 64,
        ),
    )
    for field in ("source_compatibility_sha256", "definition_compatibility_sha256"):
        _write_ingredient_component_rows(root, component_hashes, field)
    _write_ingredient_component_rows(
        root,
        component_hashes,
        "recipe_selectors_sha256",
        RecipeSelector(
            selector_id=f"hard_{recipe_id.removesuffix('_v1')}_selector_v1",
            difficulty_lane="hard",
            recipe_ids=(recipe_id,),
        ),
    )
    recipe = RecipeRule(
        recipe_id=recipe_id,
        version=1,
        domains=recipe_domains,
        required_ingredient_classes=required_ingredient_classes,
        required_definitions=required_definitions,
        required_fact_kinds=("lemma",),
        parameter_rule="finite_nat",
        soundness_template=soundness_template,
        shortcut_checks=("source_oracle",),
    )
    _write_recipe_rules(root, recipe)
    (root / "recipes" / "soundness_templates" / "fixture.lean").unlink()
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle_path.write_bytes(_ingredient_json_bytes({"schema_version": 1, "recipes": [recipe_id]}))
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    template = root / "recipes" / soundness_template
    template.write_text(
        "import Mathlib\n\n"
        f"theorem {soundness_theorem} (n : Nat) : {soundness_type_expr} := by\n"
        "  simp\n",
        encoding="utf-8",
    )
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "mathlib_commit": "abc123",
                "source_row_count": len(definitions) + 1,
                "definition_count": len(definitions),
                "fact_count": 1,
                "source_license_counts": {"Apache-2.0": len(definitions) + 1},
            }
        )
    )
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).write_bytes(
        _ingredient_json_bytes(
            {
                "definition_count": len(definitions),
                "fact_count": 1,
                "compatibility_edge_count": 1,
                "recipe_count": 1,
                "difficulty_lane_coverage": {"hard": 1},
                "bridge_coverage": {},
                "estimated_theorem_space_size": 1,
                "shortcut_risk_distribution": {"paid_eligible": 1},
                "reserve_selector_health": {"ready": True},
            }
        )
    )
    return component_hashes


def _write_map_recipe_component_tree(root: Path) -> dict[str, str]:
    return _write_single_extra_definition_recipe_component_tree(
        root,
        recipe_id="list_map_length_v1",
        extra_definition_id="List.map",
        extra_definition_type="(α -> β) -> List α -> List β",
        fact_pattern="map",
        fact_type_expr="List.length (List.map (fun x : Nat => x) ([] : List Nat)) = 0",
        soundness_template="soundness_templates/list_map_length.lean",
        soundness_theorem="list_map_length_soundness",
        soundness_type_expr="List.length (List.map (fun x : Nat => x) (List.replicate n 0)) = n",
    )


def test_ingredients_inspect_reports_manifest_identity(tmp_path) -> None:  # noqa: ANN001
    manifest = tmp_path / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest)])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary == {
        "created_at": None,
        "ingredient_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "lemma_corpus_snapshot_sha256": "f" * 64,
        "manifest": str(manifest),
        "mathlib_commit": "abc123",
        "recipe_bundle_sha256": "9" * 64,
        "reserve_selector_policy_sha256": "0" * 63 + "1",
        "schema_version": 1,
    }


def test_ingredients_inspect_rejects_symlink_manifest_path(tmp_path) -> None:  # noqa: ANN001
    manifest = tmp_path / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(), encoding="utf-8")
    symlink_manifest = tmp_path / "manifest-link.json"
    symlink_manifest.symlink_to(manifest)

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(symlink_manifest)])

    assert result.exit_code != 0
    assert "ingredient manifest path invalid" in result.output


def test_ingredients_inspect_rejects_symlink_root_path(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    symlink_root = tmp_path / "ingredients-link"
    symlink_root.symlink_to(root, target_is_directory=True)

    result = CliRunner().invoke(
        main,
        ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(symlink_root)],
    )

    assert result.exit_code != 0
    assert "ingredient root path invalid" in result.output


def test_ingredients_inspect_rejects_noncanonical_manifest(tmp_path) -> None:  # noqa: ANN001
    payload = json.loads(_ingredient_manifest_json())
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest)])

    assert result.exit_code != 0
    assert "ingredient manifest noncanonical" in result.output


def test_ingredients_inspect_rejects_manifest_created_at_side_channel(tmp_path) -> None:  # noqa: ANN001
    payload = json.loads(_ingredient_manifest_json())
    payload["created_at"] = "2026-05-31T00:00:00Z"
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(_ingredient_json_bytes(payload))

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest)])

    assert result.exit_code != 0
    assert "ingredient manifest schema invalid" in result.output


def test_ingredients_write_manifest_writes_verified_manifest(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "write-manifest",
            "--root",
            str(root),
            "--proof-atlas-snapshot-sha256",
            "f" * 64,
        ],
    )

    assert result.exit_code == 0, result.output
    manifest_path = root / "manifest.json"
    raw = manifest_path.read_bytes()
    summary = json.loads(result.output)
    manifest = IngredientManifest.model_validate_json(raw)
    assert summary == {
        "component_count": len(INGREDIENT_MANIFEST_COMPONENT_PATHS),
        "component_schema_status": "verified",
        "ingredient_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest": str(manifest_path),
        "mathlib_commit": "abc123",
        "recipe_artifact_status": "verified",
        "report_status": "verified",
    }
    assert manifest.mathlib_commit == "abc123"
    assert manifest.lemma_corpus_snapshot_sha256 == "f" * 64
    for field, expected_hash in component_hashes.items():
        assert getattr(manifest, field) == expected_hash
    assert raw == (
        json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()

    inspect_result = CliRunner().invoke(
        main,
        ["ingredients", "inspect", "--manifest", str(manifest_path), "--root", str(root)],
    )

    assert inspect_result.exit_code == 0, inspect_result.output


def test_ingredients_write_manifest_rejects_mathlib_commit_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_ingredient_component_tree(root)

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "write-manifest",
            "--root",
            str(root),
            "--lemma-corpus-snapshot-sha256",
            "f" * 64,
            "--mathlib-commit",
            "def456",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient mathlib commit mismatch" in result.output
    assert not (root / "manifest.json").exists()


def test_ingredients_write_manifest_rejects_non_commit_mathlib_pin(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_ingredient_component_tree(root)
    (root / "mathlib_commit.txt").write_text("private/path\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "write-manifest",
            "--root",
            str(root),
            "--lemma-corpus-snapshot-sha256",
            "f" * 64,
        ],
    )

    assert result.exit_code != 0
    assert "ingredient mathlib commit invalid" in result.output
    assert not (root / "manifest.json").exists()


def test_ingredients_write_manifest_rejects_symlink_mathlib_pin(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    _write_ingredient_component_tree(root)
    path = root / "mathlib_commit.txt"
    external_path = tmp_path / "mathlib_commit.external.txt"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "write-manifest",
            "--root",
            str(root),
            "--lemma-corpus-snapshot-sha256",
            "f" * 64,
        ],
    )

    assert result.exit_code != 0
    assert "ingredient mathlib commit path invalid" in result.output
    assert not (root / "manifest.json").exists()


def test_ingredients_inspect_verifies_component_hashes(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["component_count"] == len(INGREDIENT_MANIFEST_COMPONENT_PATHS)
    assert summary["component_schema_counts"]["definitions_sha256"] == 1
    assert summary["component_schema_counts"]["recipe_bundle_sha256"] == 1
    assert summary["component_schema_status"] == "verified"
    assert summary["component_status"] == "verified"
    assert summary["mathlib_commit_status"] == "verified"
    assert summary["report_count"] == len(INGREDIENT_REPOSITORY_REPORT_PATHS)
    assert set(summary["report_hashes"]) == {
        f"{report_id}_sha256" for report_id in INGREDIENT_REPOSITORY_REPORT_PATHS
    }
    assert summary["report_status"] == "verified"
    assert summary["recipe_artifact_count"] == len(INGREDIENT_RECIPE_ARTIFACT_PATHS) + 1
    assert "soundness_template:fixture.lean" in summary["recipe_artifact_hashes"]
    assert summary["recipe_artifact_status"] == "verified"
    assert summary["root"] == str(root)


def test_ingredients_inspect_allows_public_raw_ingredient_metadata(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "definitions_sha256",
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            metadata={"allowed_recipes": ["list_length_v1"], "simp_risk": "medium"},
        ),
    )
    _write_ingredient_component_row(
        root,
        component_hashes,
        "facts_sha256",
        FactIngredient(
            fact_id="facts_sha256.length_fact",
            lean_name="facts_sha256.length_fact",
            kind="lemma",
            domain="List",
            type_expr="List.length ([] : List Nat) = 0",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            difficulty_hint=1,
            metadata={
                "difficulty_score": 3,
                "direct_dependency_count": 4,
                "dependency_depth": 2,
                "proof_sha256": "1" * 64,
                "queue_depth": 1,
                "source_line": 12,
                "statement_family": "list_length",
                "subtopic": "Length",
                "topic": "List",
                "usable_as_source_fact": True,
            },
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output


def test_ingredients_inspect_rejects_unsupported_definition_simp_risk(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "definitions_sha256",
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            metadata={"simp_risk": "operator_only"},
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition metadata invalid: definitions_sha256:1:simp_risk" in result.output


def test_ingredients_inspect_rejects_private_fact_metadata_labels(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "facts_sha256",
        FactIngredient(
            fact_id="facts_sha256.length_fact",
            lean_name="facts_sha256.length_fact",
            kind="lemma",
            domain="List",
            type_expr="List.length ([] : List Nat) = 0",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            difficulty_hint=1,
            metadata={"statement_family": "private/path"},
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient fact metadata invalid: facts_sha256:1:statement_family:private/path" in result.output


def test_ingredients_inspect_rejects_absolute_definition_source_path(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    payload = DefinitionIngredient(
        definition_id="List.length",
        lean_name="List.length",
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    ).model_dump(mode="json")
    payload["source_path"] = "/tmp/Mathlib/Data/List/Basic.lean"
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_bytes(_ingredient_json_bytes(payload))
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition source path invalid: definitions_sha256:1" in result.output


def test_ingredients_inspect_rejects_url_fact_source_path(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    payload = FactIngredient(
        fact_id="facts_sha256.length_fact",
        lean_name="facts_sha256.length_fact",
        kind="lemma",
        domain="List",
        type_expr="List.length ([] : List Nat) = 0",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
        difficulty_hint=1,
    ).model_dump(mode="json")
    payload["source_path"] = "https://mathlib.example/Mathlib/Data/List/Basic.lean"
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"]
    path.write_bytes(_ingredient_json_bytes(payload))
    component_hashes["facts_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient fact source path invalid: facts_sha256:1" in result.output


def test_ingredients_inspect_rejects_non_mathlib_definition_import(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    payload = DefinitionIngredient(
        definition_id="List.length",
        lean_name="List.length",
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    ).model_dump(mode="json")
    payload["imports"] = ["Private.OperatorHints"]
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_bytes(_ingredient_json_bytes(payload))
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition import invalid: definitions_sha256:1:Private.OperatorHints" in result.output


def test_ingredients_inspect_rejects_duplicate_fact_import(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    payload = FactIngredient(
        fact_id="facts_sha256.length_fact",
        lean_name="facts_sha256.length_fact",
        kind="lemma",
        domain="List",
        type_expr="List.length ([] : List Nat) = 0",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
        difficulty_hint=1,
    ).model_dump(mode="json")
    payload["imports"] = ["Mathlib.Data.List.Basic", "Mathlib.Data.List.Basic"]
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"]
    path.write_bytes(_ingredient_json_bytes(payload))
    component_hashes["facts_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient fact import duplicate: facts_sha256:1:Mathlib.Data.List.Basic" in result.output


def test_ingredients_inspect_rejects_unsorted_definition_imports(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    payload = DefinitionIngredient(
        definition_id="List.length",
        lean_name="List.length",
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    ).model_dump(mode="json")
    payload["imports"] = ["Mathlib.Data.List.Basic", "Mathlib"]
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_bytes(_ingredient_json_bytes(payload))
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition import order invalid: definitions_sha256:1" in result.output


def test_ingredients_inspect_rejects_definition_identity_alias(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    payload = DefinitionIngredient(
        definition_id="List.length",
        lean_name="List.length",
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    ).model_dump(mode="json")
    payload["lean_name"] = "List.size"
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_bytes(_ingredient_json_bytes(payload))
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition lean name mismatch: definitions_sha256:1" in result.output


def test_ingredients_inspect_rejects_invalid_fact_id(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    payload = FactIngredient(
        fact_id="facts_sha256.length_fact",
        lean_name="facts_sha256.length_fact",
        kind="lemma",
        domain="List",
        type_expr="List.length ([] : List Nat) = 0",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
        difficulty_hint=1,
    ).model_dump(mode="json")
    payload["fact_id"] = "facts_sha256 length_fact"
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"]
    path.write_bytes(_ingredient_json_bytes(payload))
    component_hashes["facts_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient fact id invalid: facts_sha256:1:facts_sha256 length_fact" in result.output


def test_ingredients_inspect_rejects_definition_domain_side_channel(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "definitions_sha256",
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List/bad",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition domain invalid: definitions_sha256:1:List/bad" in result.output


def test_ingredients_inspect_rejects_fact_domain_side_channel(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "facts_sha256",
        FactIngredient(
            fact_id="facts_sha256.length_fact",
            lean_name="facts_sha256.length_fact",
            kind="lemma",
            domain="List/bad",
            type_expr="List.length ([] : List Nat) = 0",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            difficulty_hint=1,
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient fact domain invalid: facts_sha256:1:List/bad" in result.output


def test_ingredients_inspect_rejects_unsupported_definition_metadata(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "definitions_sha256",
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            metadata={"operator_hint": "prefer this definition"},
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition metadata unsupported: definitions_sha256:1:operator_hint" in result.output


def test_ingredients_inspect_rejects_definition_metadata_unknown_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "definitions_sha256",
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            metadata={"allowed_recipes": ["missing_recipe_v1"]},
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition metadata recipe missing: definitions_sha256:1:missing_recipe_v1" in result.output


def test_ingredients_inspect_rejects_definition_allowed_recipe_private_label(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "definitions_sha256",
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            metadata={"allowed_recipes": ["private/path"]},
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition metadata invalid: definitions_sha256:1:allowed_recipes:private/path" in result.output


def test_ingredients_inspect_rejects_unsorted_definition_allowed_recipes(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_nat",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
        RecipeRule(
            recipe_id="z_recipe_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    recipe_bundle = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    recipe_bundle.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "recipes": ["list_length_v1", "z_recipe_v1"]})
    )
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(recipe_bundle.read_bytes()).hexdigest()
    report_path = root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["recipe_count"] = 2
    report_path.write_bytes(_ingredient_json_bytes(report))
    _write_ingredient_component_row(
        root,
        component_hashes,
        "definitions_sha256",
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            metadata={"allowed_recipes": ["z_recipe_v1", "list_length_v1"]},
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient definition metadata order invalid: definitions_sha256:1:allowed_recipes" in result.output


@pytest.mark.parametrize(
    ("metadata", "message"),
    (
        ({"proof_sha256": "0" * 64}, "ingredient fact metadata invalid: facts_sha256:1:proof_sha256"),
        ({"direct_dependency_count": True}, "ingredient fact metadata invalid: facts_sha256:1:direct_dependency_count"),
    ),
)
def test_ingredients_inspect_rejects_invalid_fact_metadata(
    tmp_path, metadata: dict[str, object], message: str
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "facts_sha256",
        FactIngredient(
            fact_id="facts_sha256.length_fact",
            lean_name="facts_sha256.length_fact",
            kind="lemma",
            domain="List",
            type_expr="List.length ([] : List Nat) = 0",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
            difficulty_hint=1,
            metadata=metadata,
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_recipe_bundle_without_recipes(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    path.write_bytes(_ingredient_json_bytes({"schema_version": 1}))
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe bundle missing: recipes" in result.output


def test_ingredients_inspect_rejects_recipe_bundle_unknown_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    path.write_bytes(_ingredient_json_bytes({"schema_version": 1, "recipes": ["missing_v1"]}))
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe bundle unknown recipe: missing_v1" in result.output


def test_ingredients_inspect_rejects_recipe_bundle_private_label(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    path.write_bytes(_ingredient_json_bytes({"schema_version": 1, "recipes": ["private/path"]}))
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe bundle recipe invalid:private/path" in result.output


def test_ingredients_inspect_rejects_duplicate_recipe_bundle_id(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    path.write_bytes(_ingredient_json_bytes({"schema_version": 1, "recipes": ["list_length_v1", "list_length_v1"]}))
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe bundle duplicate: list_length_v1" in result.output


def test_ingredients_inspect_rejects_recipe_bundle_missing_recipe_rule(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    path.write_bytes(_ingredient_json_bytes({"schema_version": 1, "recipes": []}))
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe bundle missing recipe: list_length_v1" in result.output


def test_ingredients_inspect_rejects_recipe_bundle_order_drift(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
        RecipeRule(
            recipe_id="z_list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "recipes": ["z_list_length_v1", "list_length_v1"]})
    )
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe bundle order invalid" in result.output


def test_ingredients_select_receipt_reports_public_repo_selection(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    manifest_model = IngredientManifest.model_validate_json(manifest.read_bytes())
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    difficulty_state = root / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    difficulty_state_sha256 = hashlib.sha256(difficulty_state.read_bytes()).hexdigest()
    seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=manifest_sha256,
        recipe_bundle_sha256=manifest_model.recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
    )

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "select-receipt",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--difficulty-lane",
            "hard",
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    receipt = IngredientSelectionReceipt.model_validate(summary["selection"])
    assert summary["challenge_seed_sha256"] == seed
    assert summary["difficulty_state_sha256"] == difficulty_state_sha256
    assert summary["difficulty_lane"] == "hard"
    assert summary["epoch_seed_sha256"] == hashlib.sha256(b"epoch-seed").hexdigest()
    assert summary["ingredient_manifest_sha256"] == manifest_sha256
    assert summary["manifest"] == str(manifest)
    assert summary["netuid"] == 467
    assert summary["root"] == str(root)
    assert summary["selection_receipt_sha256"] == canonical_sha256(receipt)
    assert summary["selection_seed_sha256"] == seed
    assert summary["tempo"] == 42
    assert receipt.selected_recipe_id == "list_length_v1"
    assert receipt.selected_definition_ids == ("List.length",)
    assert set(receipt.selected_fact_ids).issubset(
        {"facts_sha256.length_fact", "source_theorems_sha256.length_fact", "source_lemmas_sha256.length_fact"}
    )
    assert receipt.selected_bridge_ids == ("List.length_to_Nat",)
    assert receipt.selected_parameters["Nat"] == "2"
    mismatch = CliRunner().invoke(
        main,
        [
            "ingredients",
            "select-receipt",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--challenge-seed-sha256",
            "a" * 64,
            "--difficulty-lane",
            "hard",
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
        ],
    )

    assert mismatch.exit_code != 0
    assert "ingredient selection challenge seed mismatch" in mismatch.output

    slot_seed = ingredient_challenge_slot_seed_sha256(
        challenge_seed_sha256=seed,
        queue_position=1,
        active_K=2,
    )
    slot_result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "select-receipt",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--difficulty-lane",
            "hard",
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--queue-position",
            "1",
            "--active-k",
            "2",
        ],
    )

    assert slot_result.exit_code == 0, slot_result.output
    slot_summary = json.loads(slot_result.output)
    slot_receipt = IngredientSelectionReceipt.model_validate(slot_summary["selection"])
    assert slot_summary["active_K"] == 2
    assert slot_summary["queue_position"] == 1
    assert slot_summary["challenge_seed_sha256"] == seed
    assert slot_summary["selection_seed_sha256"] == slot_seed
    assert slot_receipt.selection_seed_sha256 == slot_seed


def test_ingredients_select_receipt_rejects_symlink_manifest_path(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    symlink_manifest = tmp_path / "manifest-link.json"
    symlink_manifest.symlink_to(manifest)

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "select-receipt",
            "--manifest",
            str(symlink_manifest),
            "--root",
            str(root),
            "--challenge-seed-sha256",
            "a" * 64,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient manifest path invalid" in result.output


def test_ingredients_select_receipt_rejects_unusable_source_facts(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    for field in ("facts_sha256", "source_theorems_sha256", "source_lemmas_sha256"):
        path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS[field]
        path.write_bytes(
            _ingredient_row_bytes(
                FactIngredient(
                    fact_id=f"{field}.length_fact",
                    lean_name=f"{field}.length_fact",
                    kind="lemma",
                    domain="List",
                    type_expr="List.length ([] : List Nat) = 0",
                    imports=("Mathlib",),
                    source_path="Mathlib/Data/List/Basic.lean",
                    mathlib_commit="abc123",
                    difficulty_hint=1,
                    metadata={"usable_as_source_fact": False},
                )
            )
        )
        component_hashes[field] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "select-receipt",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--challenge-seed-sha256",
            "a" * 64,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert result.exit_code != 0
    assert "no compatible ingredient selection for difficulty lane: hard" in result.output


def test_ingredients_select_receipt_rejects_bridge_domain_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "bridge_catalog_sha256",
        BridgeRule(
            bridge_id="List.length_to_Nat",
            from_domain="List",
            to_domain="Order",
            safe_recipes=("list_length_v1",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "select-receipt",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--challenge-seed-sha256",
            "a" * 64,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert result.exit_code != 0
    assert (
        "ingredient compatibility bridge domain undeclared: compatibility_graph_sha256:1:List.length_to_Nat"
        in result.output
    )


def test_ingredients_select_receipt_rejects_difficulty_state_jsonl_drift(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = root / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))

    base_args = [
        "ingredients",
        "select-receipt",
        "--manifest",
        str(manifest),
        "--root",
        str(root),
        "--netuid",
        "467",
        "--tempo",
        "42",
        "--epoch-seed",
        "epoch-seed",
        "--difficulty-state-jsonl",
        str(difficulty_state),
    ]
    hash_drift = CliRunner().invoke(
        main,
        [
            *base_args,
            "--difficulty-state-sha256",
            "a" * 64,
        ],
    )

    assert hash_drift.exit_code != 0
    assert "ingredient difficulty state sha256 mismatch" in hash_drift.output

    symlink_difficulty_state = root / "difficulty-state-link.jsonl"
    symlink_difficulty_state.symlink_to(difficulty_state)
    symlink_result = CliRunner().invoke(
        main,
        [
            *base_args[:-1],
            str(symlink_difficulty_state),
        ],
    )

    assert symlink_result.exit_code != 0
    assert "ingredient difficulty state path invalid" in symlink_result.output

    lane_drift = CliRunner().invoke(
        main,
        [
            *base_args,
            "--difficulty-lane",
            "easy",
        ],
    )

    assert lane_drift.exit_code != 0
    assert "ingredient difficulty state active lane mismatch" in lane_drift.output


def test_ingredients_verify_task_binds_public_repo_selection_to_task(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    manifest_model = IngredientManifest.model_validate_json(manifest.read_bytes())
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    difficulty_state = root / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    difficulty_state_sha256 = hashlib.sha256(difficulty_state.read_bytes()).hexdigest()
    seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=manifest_sha256,
        recipe_bundle_sha256=manifest_model.recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
    )
    statement = "theorem generated_list_length : True := by\n  sorry"
    selection = select_ingredient_receipt_from_root(
        root,
        challenge_seed_sha256=seed,
        difficulty_lane="hard",
        mathlib_commit=manifest_model.mathlib_commit,
    )
    active_target_sha256 = problem_target_sha256(
        Problem(
            id="lemma.ingredient.list_length",
            theorem_name="generated_list_length",
            type_expr="True",
            split="ingredient",
            lean_toolchain="leanprover/lean4:v4.30.0-rc2",
            mathlib_rev=manifest_model.mathlib_commit,
            imports=("Mathlib",),
            extra={"challenge_full": statement},
        )
    )
    theorem_statement_sha256 = text_sha256(statement)
    gate_receipt = IngredientGateReceipt(
        schema_version=1,
        receipt_kind="statement_gate",
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=active_target_sha256,
        theorem_statement_sha256=theorem_statement_sha256,
        ingredient_manifest_sha256=manifest_sha256,
        selection_receipt_sha256=canonical_sha256(selection),
        status="passed",
        runner="fixture-statement-gate",
        checks=("metadata_bound",),
    )
    shortcut_receipt = gate_receipt.model_copy(
        update={
            "receipt_kind": "shortcut_gate",
            "runner": "fixture-shortcut-gate",
        }
    )
    receipt = build_ingredient_generation_receipt(
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=manifest_sha256,
        lemma_corpus_snapshot_sha256="f" * 64,
        ingredient_repo_commit="abc123",
        mathlib_commit=manifest_model.mathlib_commit,
        recipe_bundle_sha256=manifest_model.recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
        selection=selection,
        active_task_id="lemma.ingredient.list_length",
        active_target_sha256=active_target_sha256,
        theorem_statement=statement,
        gate_receipt=gate_receipt,
        shortcut_receipt=shortcut_receipt,
    )
    task = build_fixture_ingredient_task(
        receipt=receipt,
        theorem_name="generated_list_length",
        type_expr="True",
        statement=statement,
    )
    task_path = root / "task.json"
    task_path.write_bytes(canonical_json_bytes(task) + b"\n")
    receipt_path = root / "generation-receipt.json"
    receipt_path.write_bytes(canonical_json_bytes(receipt) + b"\n")

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt",
            str(receipt_path),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary == {
        "active_task_id": task.id,
        "challenge_seed_sha256": seed,
        "difficulty_lane": "hard",
        "difficulty_state_sha256": difficulty_state_sha256,
        "epoch_seed_sha256": receipt.epoch_seed_sha256,
        "generation_receipt": str(receipt_path),
        "generation_receipt_sha256": canonical_sha256(receipt),
        "generation_receipt_status": "verified",
        "ingredient_manifest_sha256": receipt.ingredient_manifest_sha256,
        "manifest": str(manifest),
        "netuid": 467,
        "root": str(root),
        "selection_receipt_sha256": canonical_sha256(selection),
        "selected_recipe_id": selection.selected_recipe_id,
        "selected_selector_id": selection.selected_selector_id,
        "tempo": 42,
        "task": str(task_path),
        "task_status": "verified",
    }
    symlink_task_path = root / "task-link.json"
    symlink_task_path.symlink_to(task_path)
    symlink_task = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(symlink_task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert symlink_task.exit_code != 0
    assert "ingredient task path invalid" in symlink_task.output
    noncanonical_task_path = root / "task-pretty.json"
    noncanonical_task_path.write_text(
        json.dumps(json.loads(task_path.read_bytes()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    noncanonical_task = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(noncanonical_task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert noncanonical_task.exit_code != 0
    assert "ingredient task noncanonical" in noncanonical_task.output
    noncanonical_receipt_path = root / "generation-receipt-pretty.json"
    noncanonical_receipt_path.write_text(
        json.dumps(json.loads(receipt_path.read_bytes()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    noncanonical_receipt = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt",
            str(noncanonical_receipt_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert noncanonical_receipt.exit_code != 0
    assert "ingredient generation receipt artifact noncanonical" in noncanonical_receipt.output
    symlink_receipt_path = root / "generation-receipt-link.json"
    symlink_receipt_path.symlink_to(receipt_path)
    symlink_receipt = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt",
            str(symlink_receipt_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert symlink_receipt.exit_code != 0
    assert "ingredient generation receipt artifact path invalid" in symlink_receipt.output
    lane_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--difficulty-lane",
            "easy",
        ],
    )

    assert lane_drift.exit_code != 0
    assert "ingredient difficulty state active lane mismatch" in lane_drift.output

    drifted_difficulty_state = root / "difficulty-state-drift.jsonl"
    drifted_difficulty_state.write_bytes(
        _difficulty_state_jsonl(
            {"tempo": 1, "difficulty_lane": "easy"},
            {"tempo": 42, "difficulty_lane": "hard"},
        )
    )
    hash_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-state-jsonl",
            str(drifted_difficulty_state),
        ],
    )

    assert hash_drift.exit_code != 0
    assert "ingredient task difficulty state sha256 mismatch" in hash_drift.output

    seedless_hash_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--difficulty-state-jsonl",
            str(drifted_difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert seedless_hash_drift.exit_code != 0
    assert "ingredient task difficulty state sha256 mismatch" in seedless_hash_drift.output

    missing_seed_inputs = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--difficulty-state-jsonl",
            str(difficulty_state),
        ],
    )

    assert missing_seed_inputs.exit_code != 0
    assert "provide --challenge-seed-sha256 or --netuid/--epoch-seed/--difficulty-state-jsonl" in (
        missing_seed_inputs.output
    )

    missing_quorum_envelope = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt-envelope-quorum",
            "2",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert missing_quorum_envelope.exit_code != 0
    assert "provide --generation-receipt-envelope to verify envelope quorum or signatures" in (
        missing_quorum_envelope.output
    )
    missing_signature_envelope = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--verify-envelope-signatures",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert missing_signature_envelope.exit_code != 0
    assert "provide --generation-receipt-envelope to verify envelope quorum or signatures" in (
        missing_signature_envelope.output
    )

    bad_tempo_task_path = root / "task-bad-tempo.json"
    bad_tempo_task_path.write_bytes(
        canonical_json_bytes(task.model_copy(update={"metadata": {**task.metadata, "tempo": "42"}}))
        + b"\n"
    )
    bad_tempo = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(bad_tempo_task_path),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert bad_tempo.exit_code != 0
    assert "ingredient task tempo metadata malformed" in bad_tempo.output

    wrong_epoch_seed = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
            "--netuid",
            "467",
            "--epoch-seed",
            "different-epoch-seed",
        ],
    )

    assert wrong_epoch_seed.exit_code != 0
    assert "ingredient task epoch seed mismatch" in wrong_epoch_seed.output
    wrong_netuid = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
            "--netuid",
            "468",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert wrong_netuid.exit_code != 0
    assert "ingredient task challenge seed mismatch" in wrong_netuid.output
    bad_policy_task_path = root / "task-bad-policy.json"
    bad_policy_task_path.write_bytes(
        canonical_json_bytes(task.model_copy(update={"policy": "strict_envelope"})) + b"\n"
    )
    bad_policy = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(bad_policy_task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert bad_policy.exit_code != 0
    assert "ingredient task submission policy mismatch" in bad_policy.output
    bad_queue_task_path = root / "task-bad-queue.json"
    bad_queue_task_path.write_bytes(
        canonical_json_bytes(task.model_copy(update={"queue_depth": 1})) + b"\n"
    )
    bad_queue = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(bad_queue_task_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert bad_queue.exit_code != 0
    assert "ingredient task queue depth mismatch" in bad_queue.output
    envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id="signer.alpha",
        signature="sig.alpha",
    )
    second_envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id="signer.beta",
        signature="sig.beta",
    )
    envelope_path = root / "generation-receipt-envelope.json"
    second_envelope_path = root / "generation-receipt-envelope-2.json"
    envelope_path.write_bytes(canonical_json_bytes(envelope) + b"\n")
    second_envelope_path.write_bytes(canonical_json_bytes(second_envelope) + b"\n")
    noncanonical_envelope_path = root / "generation-receipt-envelope-pretty.json"
    noncanonical_envelope_path.write_text(
        json.dumps(json.loads(envelope_path.read_bytes()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    noncanonical_envelope = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt-envelope",
            str(noncanonical_envelope_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert noncanonical_envelope.exit_code != 0
    assert "ingredient generation receipt envelope noncanonical" in noncanonical_envelope.output
    symlink_envelope_path = root / "generation-receipt-envelope-link.json"
    symlink_envelope_path.symlink_to(envelope_path)
    symlink_envelope = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt-envelope",
            str(symlink_envelope_path),
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert symlink_envelope.exit_code != 0
    assert "ingredient generation receipt envelope path invalid" in symlink_envelope.output

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt-envelope",
            str(envelope_path),
            "--generation-receipt-envelope",
            str(second_envelope_path),
            "--generation-receipt-envelope-quorum",
            "2",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary == {
        "active_task_id": task.id,
        "generation_receipt_envelope_quorum": 2,
        "envelope_signature_status": "metadata_only",
        "challenge_seed_sha256": seed,
        "difficulty_lane": "hard",
        "difficulty_state_sha256": difficulty_state_sha256,
        "epoch_seed_sha256": receipt.epoch_seed_sha256,
        "generation_receipt_envelope_sha256s": [
            canonical_sha256(envelope),
            canonical_sha256(second_envelope),
        ],
        "generation_receipt_envelopes": [str(envelope_path), str(second_envelope_path)],
        "generation_receipt_sha256": canonical_sha256(receipt),
        "generation_receipt_status": "verified",
        "ingredient_manifest_sha256": receipt.ingredient_manifest_sha256,
        "manifest": str(manifest),
        "netuid": 467,
        "root": str(root),
        "selection_receipt_sha256": canonical_sha256(selection),
        "selected_recipe_id": selection.selected_recipe_id,
        "selected_selector_id": selection.selected_selector_id,
        "tempo": 42,
        "task": str(task_path),
        "task_status": "verified",
    }
    shortfall = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt-envelope",
            str(envelope_path),
            "--generation-receipt-envelope-quorum",
            "2",
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert shortfall.exit_code != 0
    assert "ingredient generation receipt envelope quorum shortfall" in shortfall.output
    signer_keypair = Keypair.create_from_uri("//LemmaCliIngredientSigner")
    signable_envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id=signer_keypair.ss58_address,
        signature="pending",
    )
    signed_envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id=signer_keypair.ss58_address,
        signature="0x"
        + signer_keypair.sign(
            ingredient_generation_receipt_envelope_signing_payload(signable_envelope)
        ).hex(),
    )
    signed_envelope_path = root / "generation-receipt-envelope-signed.json"
    signed_envelope_path.write_bytes(canonical_json_bytes(signed_envelope) + b"\n")

    signed_result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt-envelope",
            str(signed_envelope_path),
            "--verify-envelope-signatures",
            "--challenge-seed-sha256",
            seed,
            "--difficulty-lane",
            "hard",
        ],
    )

    assert signed_result.exit_code == 0, signed_result.output
    signed_summary = json.loads(signed_result.output)
    assert signed_summary["envelope_signature_status"] == "verified"
    assert signed_summary["generation_receipt_envelope_sha256s"] == [
        canonical_sha256(signed_envelope)
    ]


def test_ingredients_inspect_rejects_component_hash_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    (root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]).write_text(
        DefinitionIngredient(
            definition_id="List.size",
            lean_name="List.size",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component hash mismatch: definitions_sha256" in result.output


def test_ingredients_inspect_rejects_symlink_component_path(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    external_path = tmp_path / "definitions.external.jsonl"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component path invalid: definitions_sha256" in result.output


def test_ingredients_inspect_rejects_symlink_mathlib_pin(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    path = root / "mathlib_commit.txt"
    external_path = tmp_path / "mathlib_commit.external.txt"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient mathlib commit path invalid" in result.output


@pytest.mark.parametrize("artifact_id", tuple(INGREDIENT_RECIPE_ARTIFACT_PATHS))
def test_ingredients_inspect_rejects_symlink_recipe_artifact_path(
    tmp_path, artifact_id: str
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    path = root / INGREDIENT_RECIPE_ARTIFACT_PATHS[artifact_id]
    external_path = tmp_path / f"{artifact_id}.external.json"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert f"ingredient recipe artifact path invalid: {artifact_id}" in result.output


def test_ingredients_inspect_rejects_invalid_component_schema(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_text(json.dumps({"definition_id": "broken"}) + "\n", encoding="utf-8")
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component invalid: definitions_sha256:1" in result.output


def test_ingredients_inspect_rejects_duplicate_component_id(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    definition = DefinitionIngredient(
        definition_id="List.length",
        lean_name="List.length",
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    )
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_bytes(_ingredient_row_bytes(definition) * 2)
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component id duplicate: definitions_sha256:2" in result.output


def test_ingredients_inspect_rejects_component_row_order_drift(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    first = DefinitionIngredient(
        definition_id="Nat.succ",
        lean_name="Nat.succ",
        domain="Nat",
        type_signature="Nat -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/Nat/Basic.lean",
        mathlib_commit="abc123",
    )
    second = DefinitionIngredient(
        definition_id="List.length",
        lean_name="List.length",
        domain="List",
        type_signature="List α -> Nat",
        imports=("Mathlib",),
        source_path="Mathlib/Data/List/Basic.lean",
        mathlib_commit="abc123",
    )
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_bytes(_ingredient_row_bytes(first) + _ingredient_row_bytes(second))
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component id order invalid: definitions_sha256:2" in result.output


def test_ingredients_inspect_rejects_duplicate_fact_id_across_catalogs(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["source_lemmas_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            FactIngredient(
                fact_id="facts_sha256.length_fact",
                lean_name="facts_sha256.length_fact",
                kind="lemma",
                domain="List",
                type_expr="List.reverse [] = ([] : List Nat)",
                imports=("Mathlib",),
                source_path="Mathlib/Data/List/Basic.lean",
                mathlib_commit="abc123",
                difficulty_hint=1,
            )
        )
    )
    component_hashes["source_lemmas_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient fact catalog id duplicate: facts_sha256.length_fact" in result.output


def test_ingredients_inspect_rejects_noncanonical_component_row(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]
    path.write_text(
        DefinitionIngredient(
            definition_id="List.length",
            lean_name="List.length",
            domain="List",
            type_signature="List α -> Nat",
            imports=("Mathlib",),
            source_path="Mathlib/Data/List/Basic.lean",
            mathlib_commit="abc123",
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    component_hashes["definitions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component noncanonical: definitions_sha256:1" in result.output


def test_ingredients_inspect_rejects_noncanonical_json_component(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_ladder_sha256"]
    path.write_text(
        json.dumps({"schema_version": 1, "difficulty_lanes": list(DIFFICULTY_LANES)}) + "\n",
        encoding="utf-8",
    )
    component_hashes["difficulty_ladder_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty ladder noncanonical" in result.output


def test_ingredients_inspect_rejects_difficulty_ladder_without_lanes(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_ladder_sha256"]
    path.write_bytes(_ingredient_json_bytes({"schema_version": 1}))
    component_hashes["difficulty_ladder_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty ladder missing: difficulty_lanes" in result.output


def test_ingredients_inspect_rejects_duplicate_difficulty_ladder_lane(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_ladder_sha256"]
    path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "difficulty_lanes": ["easy", "medium", "hard", "hard"]})
    )
    component_hashes["difficulty_ladder_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty ladder duplicate: hard" in result.output


def test_ingredients_inspect_rejects_unsupported_difficulty_ladder_lane(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_ladder_sha256"]
    path.write_bytes(
        _ingredient_json_bytes(
            {"schema_version": 1, "difficulty_lanes": ["easy", "medium", "hard", "frontier", "impossible"]}
        )
    )
    component_hashes["difficulty_ladder_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty ladder unsupported: impossible" in result.output


def test_ingredients_inspect_rejects_reordered_difficulty_ladder(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_ladder_sha256"]
    path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "difficulty_lanes": ["medium", "easy", "hard", "frontier"]})
    )
    component_hashes["difficulty_ladder_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty ladder order invalid" in result.output


def test_ingredients_inspect_rejects_reserve_selector_policy_without_enabled(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["reserve_selector_policy_sha256"]
    path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "selection_method": "hash_order_first_eligible"})
    )
    component_hashes["reserve_selector_policy_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient reserve selector policy missing: reserve_enabled" in result.output


def test_ingredients_inspect_rejects_disabled_reserve_selector_policy(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["reserve_selector_policy_sha256"]
    path.write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "reserve_enabled": False,
                "selection_method": "hash_order_first_eligible",
            }
        )
    )
    component_hashes["reserve_selector_policy_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient reserve selector policy disabled" in result.output


def test_ingredients_inspect_rejects_unsupported_reserve_selector_policy_method(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["reserve_selector_policy_sha256"]
    path.write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "reserve_enabled": True,
                "selection_method": "round_robin",
            }
        )
    )
    component_hashes["reserve_selector_policy_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient reserve selector policy selection method unsupported" in result.output


def test_ingredients_inspect_rejects_difficulty_retarget_policy_without_mode(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_retarget_sha256"]
    path.write_bytes(_ingredient_json_bytes({"schema_version": 1, "state_schema": "tempo_lane_v1"}))
    component_hashes["difficulty_retarget_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty retarget policy missing: retarget_mode" in result.output


def test_ingredients_inspect_rejects_unsupported_difficulty_retarget_mode(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_retarget_sha256"]
    path.write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "retarget_mode": "solve_rate_v1",
                "state_schema": "tempo_lane_v1",
            }
        )
    )
    component_hashes["difficulty_retarget_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty retarget policy mode unsupported" in result.output


def test_ingredients_inspect_rejects_unsupported_difficulty_state_schema(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["difficulty_retarget_sha256"]
    path.write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "retarget_mode": "manual_state_v1",
                "state_schema": "windowed_solve_rate_v1",
            }
        )
    )
    component_hashes["difficulty_retarget_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient difficulty retarget policy state schema unsupported" in result.output


def test_ingredients_inspect_rejects_component_mathlib_commit_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            FactIngredient(
                fact_id="bad.fact",
                lean_name="bad.fact",
                kind="lemma",
                domain="List",
                type_expr="True",
                imports=("Mathlib",),
                source_path="Mathlib/Data/List/Basic.lean",
                mathlib_commit="def456",
                difficulty_hint=1,
            )
        )
    )
    component_hashes["facts_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component mathlib commit mismatch: facts_sha256:1" in result.output


def test_ingredients_inspect_rejects_missing_recipe_reference(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("missing_recipe_v1",),
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient component recipe reference missing: recipe_selectors_sha256:1" in result.output


@pytest.mark.parametrize(
    ("field", "row"),
    (
        (
            "recipe_selectors_sha256",
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("private/path",),
            ),
        ),
        (
            "bridge_catalog_sha256",
            BridgeRule(
                bridge_id="List.length_to_Nat",
                from_domain="List",
                to_domain="Nat",
                safe_recipes=("private/path",),
            ),
        ),
        (
            "compatibility_graph_sha256",
            CompatibilityEdge(
                edge_id="compatibility_graph_sha256.edge",
                recipe_id="private/path",
                ingredient_class="list_fact",
                allowed_domains=("List",),
                allowed_definition_ids=("List.length",),
                allowed_fact_patterns=("length",),
                bridge_ids=("List.length_to_Nat",),
                difficulty_lanes=("hard",),
                certification_receipt_sha256="1" * 64,
            ),
        ),
    ),
)
def test_ingredients_inspect_rejects_component_recipe_reference_private_label(
    tmp_path, field: str, row: BaseModel
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(root, component_hashes, field, row)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert f"ingredient component recipe reference invalid: {field}:1:private/path" in result.output


def test_ingredients_inspect_rejects_selector_without_recipe_ids(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=(),
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector recipe ids missing: recipe_selectors_sha256:1" in result.output


def test_ingredients_inspect_rejects_invalid_selector_id(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken/selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector id invalid: recipe_selectors_sha256:1:broken/selector" in result.output


def test_ingredients_inspect_rejects_duplicate_selector_recipe_id(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1", "list_length_v1"),
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector recipe id duplicate: recipe_selectors_sha256:1:list_length_v1" in result.output


def test_ingredients_inspect_rejects_unsorted_selector_recipe_ids(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_nat",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
        RecipeRule(
            recipe_id="z_recipe_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    recipe_bundle = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    recipe_bundle.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "recipes": ["list_length_v1", "z_recipe_v1"]})
    )
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(recipe_bundle.read_bytes()).hexdigest()
    report_path = root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["recipe_count"] = 2
    report_path.write_bytes(_ingredient_json_bytes(report))
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("z_recipe_v1", "list_length_v1"),
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector recipe id order invalid: recipe_selectors_sha256:1" in result.output


def test_ingredients_inspect_allows_selector_max_simp_risk_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"max_simp_risk": "medium"},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output


def test_ingredients_inspect_rejects_invalid_selector_max_simp_risk_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"max_simp_risk": "operator_only"},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector filter invalid: recipe_selectors_sha256:1:max_simp_risk" in result.output


def test_ingredients_inspect_allows_selector_min_dependency_depth_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"min_dependency_depth": 2},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output


def test_ingredients_inspect_rejects_invalid_selector_min_dependency_depth_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"min_dependency_depth": True},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector filter invalid: recipe_selectors_sha256:1:min_dependency_depth" in result.output


def test_ingredients_inspect_rejects_unsupported_selector_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"source_family": "lists"},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector filter unsupported: recipe_selectors_sha256:1:source_family" in result.output


def test_ingredients_inspect_rejects_duplicate_selector_domain_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"domains": ["List", "List"]},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector filter duplicate: recipe_selectors_sha256:1:domains:List" in result.output


def test_ingredients_inspect_rejects_unsorted_selector_domain_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"domains": ["Nat", "List"]},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector filter order invalid: recipe_selectors_sha256:1:domains" in result.output


def test_ingredients_inspect_rejects_invalid_selector_domain_filter(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_selectors_sha256"]
    path.write_bytes(
        _ingredient_row_bytes(
            RecipeSelector(
                selector_id="broken_selector",
                difficulty_lane="hard",
                recipe_ids=("list_length_v1",),
                ingredient_filters={"domains": ["List/bad"]},
            )
        )
    )
    component_hashes["recipe_selectors_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient selector filter domain invalid: recipe_selectors_sha256:1:domains:List/bad" in result.output


def test_ingredients_inspect_rejects_missing_recipe_definition_reference(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.size",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe definition reference missing: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_invalid_recipe_id(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list/bad",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe id invalid: recipe_rules:list/bad" in result.output


def test_ingredients_inspect_rejects_missing_recipe_fact_kind(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("theorem",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe fact kind missing: recipe_rules:list_length_v1" in result.output


@pytest.mark.parametrize(
    ("recipe_overrides", "message"),
    [
        ({"domains": ()}, "ingredient recipe domains missing: recipe_rules:list_length_v1"),
        (
            {"domains": ("List/bad",)},
            "ingredient recipe domain invalid: recipe_rules:list_length_v1:List/bad",
        ),
        ({"domains": ("List", "List")}, "ingredient recipe domain duplicate: recipe_rules:list_length_v1:List"),
        ({"domains": ("Nat", "List")}, "ingredient recipe domain order invalid: recipe_rules:list_length_v1"),
        (
            {"required_definitions": ()},
            "ingredient recipe definitions missing: recipe_rules:list_length_v1",
        ),
        (
            {"required_definitions": ("List.length", "List.length")},
            "ingredient recipe definition duplicate: recipe_rules:list_length_v1:List.length",
        ),
        (
            {"required_definitions": ("Nat.succ", "List.length")},
            "ingredient recipe definition order invalid: recipe_rules:list_length_v1",
        ),
        ({"required_fact_kinds": ()}, "ingredient recipe fact kinds missing: recipe_rules:list_length_v1"),
        (
            {"required_fact_kinds": ("lemma", "lemma")},
            "ingredient recipe fact kind duplicate: recipe_rules:list_length_v1:lemma",
        ),
        (
            {"required_fact_kinds": ("theorem", "lemma")},
            "ingredient recipe fact kind order invalid: recipe_rules:list_length_v1",
        ),
    ],
)
def test_ingredients_inspect_rejects_invalid_recipe_selection_fields(
    tmp_path: Path,
    recipe_overrides: dict[str, object],
    message: str,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    recipe_kwargs = {
        "recipe_id": "list_length_v1",
        "version": 1,
        "domains": ("List", "Nat"),
        "required_ingredient_classes": ("list_definition", "list_fact"),
        "required_definitions": ("List.length",),
        "required_fact_kinds": ("lemma",),
        "parameter_rule": "none",
        "soundness_template": "soundness_templates/fixture.lean",
        "shortcut_checks": ("source_oracle",),
    }
    recipe_kwargs.update(recipe_overrides)
    _write_recipe_rules(root, RecipeRule(**recipe_kwargs))
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_missing_recipe_ingredient_classes(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=(),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe ingredient classes missing: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_duplicate_recipe_ingredient_class(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_fact", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe ingredient class duplicate: recipe_rules:list_length_v1:list_fact" in result.output


def test_ingredients_inspect_rejects_unsorted_recipe_ingredient_classes(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_fact", "list_definition"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe ingredient class order invalid: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_invalid_recipe_ingredient_class(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list/fact",),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe ingredient class invalid: recipe_rules:list_length_v1:list/fact" in result.output


def test_ingredients_inspect_rejects_undeclared_compatibility_ingredient_class(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "compatibility_graph_sha256",
        CompatibilityEdge(
            edge_id="broken_edge",
            recipe_id="list_length_v1",
            ingredient_class="set_fact",
            allowed_domains=("List",),
            allowed_definition_ids=("List.length",),
            allowed_fact_patterns=("length",),
            difficulty_lanes=("hard",),
            certification_receipt_sha256="1" * 64,
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient compatibility class undeclared: compatibility_graph_sha256:1" in result.output


@pytest.mark.parametrize(
    ("edge_overrides", "message"),
    [
        (
            {"edge_id": "broken/edge"},
            "ingredient compatibility edge id invalid: compatibility_graph_sha256:1:broken/edge",
        ),
        (
            {"ingredient_class": "list/fact"},
            "ingredient compatibility class invalid: compatibility_graph_sha256:1:list/fact",
        ),
        ({"difficulty_lanes": ()}, "ingredient compatibility difficulty lanes missing: compatibility_graph_sha256:1"),
        (
            {"difficulty_lanes": ("hard", "hard")},
            "ingredient compatibility difficulty lane duplicate: compatibility_graph_sha256:1:hard",
        ),
        (
            {"difficulty_lanes": ("hard", "easy")},
            "ingredient compatibility difficulty lane order invalid: compatibility_graph_sha256:1",
        ),
        ({"allowed_domains": ()}, "ingredient compatibility allowed domains missing: compatibility_graph_sha256:1"),
        (
            {"allowed_domains": ("List/bad",)},
            "ingredient compatibility allowed domain invalid: compatibility_graph_sha256:1:List/bad",
        ),
        (
            {"allowed_domains": ("List", "List")},
            "ingredient compatibility allowed domain duplicate: compatibility_graph_sha256:1:List",
        ),
        (
            {"allowed_domains": ("Nat", "List")},
            "ingredient compatibility allowed domain order invalid: compatibility_graph_sha256:1",
        ),
        ({"allowed_fact_patterns": ()}, "ingredient compatibility fact patterns missing: compatibility_graph_sha256:1"),
        (
            {"allowed_fact_patterns": ("length/bad",)},
            "ingredient compatibility fact pattern invalid: compatibility_graph_sha256:1:length/bad",
        ),
        (
            {"allowed_fact_patterns": ("length", "length")},
            "ingredient compatibility fact pattern duplicate: compatibility_graph_sha256:1:length",
        ),
        (
            {"allowed_fact_patterns": ("z_length", "length")},
            "ingredient compatibility fact pattern order invalid: compatibility_graph_sha256:1",
        ),
        (
            {"allowed_definition_ids": ("List.length", "List.length")},
            "ingredient compatibility definition duplicate: compatibility_graph_sha256:1:List.length",
        ),
        (
            {"allowed_definition_ids": ("List.size", "List.length")},
            "ingredient compatibility definition order invalid: compatibility_graph_sha256:1",
        ),
        (
            {"bridge_ids": ("List.length_to_Nat", "List.length_to_Nat")},
            "ingredient compatibility bridge duplicate: compatibility_graph_sha256:1:List.length_to_Nat",
        ),
        (
            {"bridge_ids": ("z_bridge", "List.length_to_Nat")},
            "ingredient compatibility bridge order invalid: compatibility_graph_sha256:1",
        ),
        (
            {"certification_receipt_sha256": "0" * 64},
            "ingredient compatibility certification receipt placeholder: compatibility_graph_sha256:1",
        ),
    ],
)
def test_ingredients_inspect_rejects_invalid_compatibility_edge_shape(
    tmp_path: Path,
    edge_overrides: dict[str, str | tuple[str, ...]],
    message: str,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    edge_kwargs = {
        "edge_id": "broken_edge",
        "recipe_id": "list_length_v1",
        "ingredient_class": "list_fact",
        "allowed_domains": ("List",),
        "allowed_definition_ids": ("List.length",),
        "allowed_fact_patterns": ("length",),
        "bridge_ids": ("List.length_to_Nat",),
        "difficulty_lanes": ("hard",),
        "certification_receipt_sha256": "1" * 64,
    }
    edge_kwargs.update(edge_overrides)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "compatibility_graph_sha256",
        CompatibilityEdge(**edge_kwargs),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_missing_compatibility_definition_reference(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "compatibility_graph_sha256",
        CompatibilityEdge(
            edge_id="broken_edge",
            recipe_id="list_length_v1",
            ingredient_class="list_fact",
            allowed_domains=("List",),
            allowed_definition_ids=("List.size",),
            allowed_fact_patterns=("length",),
            difficulty_lanes=("hard",),
            certification_receipt_sha256="1" * 64,
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient compatibility definition reference missing: compatibility_graph_sha256:1" in result.output


def test_ingredients_inspect_rejects_missing_compatibility_bridge_reference(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "compatibility_graph_sha256",
        CompatibilityEdge(
            edge_id="broken_edge",
            recipe_id="list_length_v1",
            ingredient_class="list_fact",
            allowed_domains=("List",),
            allowed_definition_ids=("List.length",),
            allowed_fact_patterns=("length",),
            bridge_ids=("List.size_to_Nat",),
            difficulty_lanes=("hard",),
            certification_receipt_sha256="1" * 64,
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient compatibility bridge reference missing: compatibility_graph_sha256:1" in result.output


def test_ingredients_inspect_rejects_undeclared_compatibility_domain(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "compatibility_graph_sha256",
        CompatibilityEdge(
            edge_id="broken_edge",
            recipe_id="list_length_v1",
            ingredient_class="list_fact",
            allowed_domains=("List", "Set"),
            allowed_definition_ids=("List.length",),
            allowed_fact_patterns=("length",),
            difficulty_lanes=("hard",),
            certification_receipt_sha256="1" * 64,
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient compatibility domain undeclared: compatibility_graph_sha256:1:Set" in result.output


def test_ingredients_inspect_rejects_unsafe_compatibility_bridge(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_nat",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
        RecipeRule(
            recipe_id="other_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_nat",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    recipe_bundle = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    recipe_bundle.write_bytes(
        _ingredient_json_bytes(
            {"schema_version": 1, "recipes": ["list_length_v1", "other_length_v1"]}
        )
    )
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(recipe_bundle.read_bytes()).hexdigest()
    _write_ingredient_component_row(
        root,
        component_hashes,
        "bridge_catalog_sha256",
        BridgeRule(
            bridge_id="List.length_to_Nat",
            from_domain="List",
            to_domain="Nat",
            safe_recipes=("other_length_v1",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient compatibility bridge unsafe: compatibility_graph_sha256:1:List.length_to_Nat" in result.output


def test_ingredients_inspect_rejects_compatibility_bridge_domain_drift(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "bridge_catalog_sha256",
        BridgeRule(
            bridge_id="List.length_to_Nat",
            from_domain="List",
            to_domain="Fin",
            safe_recipes=("list_length_v1",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert (
        "ingredient compatibility bridge domain undeclared: compatibility_graph_sha256:1:List.length_to_Nat"
        in result.output
    )


@pytest.mark.parametrize(
    ("bridge_overrides", "message"),
    [
        ({"bridge_id": "List/bad"}, "ingredient bridge id invalid: bridge_catalog_sha256:1:List/bad"),
        ({"from_domain": ""}, "ingredient bridge from domain missing: bridge_catalog_sha256:1"),
        ({"to_domain": ""}, "ingredient bridge to domain missing: bridge_catalog_sha256:1"),
        (
            {"from_domain": "List/bad"},
            "ingredient bridge domain invalid: bridge_catalog_sha256:1:from_domain:List/bad",
        ),
        ({"to_domain": "List"}, "ingredient bridge domains not bridged: bridge_catalog_sha256:1:List"),
        ({"safe_recipes": ()}, "ingredient bridge safe recipes missing: bridge_catalog_sha256:1"),
        (
            {"safe_recipes": ("list_length_v1", "list_length_v1")},
            "ingredient bridge safe recipe duplicate: bridge_catalog_sha256:1:list_length_v1",
        ),
        (
            {"metadata": {"operator_hint": "prefer this bridge"}},
            "ingredient bridge metadata unsupported: bridge_catalog_sha256:1:operator_hint",
        ),
        (
            {"metadata": {"meaning": ""}},
            "ingredient bridge metadata invalid: bridge_catalog_sha256:1:meaning",
        ),
        (
            {"metadata": {"meaning": "private/path"}},
            "ingredient bridge metadata invalid: bridge_catalog_sha256:1:meaning:private/path",
        ),
    ],
)
def test_ingredients_inspect_rejects_invalid_bridge_rule_shape(
    tmp_path: Path,
    bridge_overrides: dict[str, object],
    message: str,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    bridge_kwargs = {
        "bridge_id": "List.length_to_Nat",
        "from_domain": "List",
        "to_domain": "Nat",
        "safe_recipes": ("list_length_v1",),
    }
    bridge_kwargs.update(bridge_overrides)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "bridge_catalog_sha256",
        BridgeRule(**bridge_kwargs),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_unsorted_bridge_safe_recipes(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_nat",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
        RecipeRule(
            recipe_id="z_recipe_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    recipe_bundle = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    recipe_bundle.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "recipes": ["list_length_v1", "z_recipe_v1"]})
    )
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(recipe_bundle.read_bytes()).hexdigest()
    report_path = root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["recipe_count"] = 2
    report_path.write_bytes(_ingredient_json_bytes(report))
    _write_ingredient_component_row(
        root,
        component_hashes,
        "bridge_catalog_sha256",
        BridgeRule(
            bridge_id="List.length_to_Nat",
            from_domain="List",
            to_domain="Nat",
            safe_recipes=("z_recipe_v1", "list_length_v1"),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient bridge safe recipe order invalid: bridge_catalog_sha256:1" in result.output


def test_ingredients_inspect_allows_bridge_meaning_metadata(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_ingredient_component_row(
        root,
        component_hashes,
        "bridge_catalog_sha256",
        BridgeRule(
            bridge_id="List.length_to_Nat",
            from_domain="List",
            to_domain="Nat",
            safe_recipes=("list_length_v1",),
            metadata={"meaning": "list_length_to_nat"},
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output


def test_ingredients_inspect_rejects_mathlib_commit_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    (root / "mathlib_commit.txt").write_text("def456\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient mathlib commit mismatch" in result.output


def test_ingredients_inspect_rejects_incomplete_quality_report(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = {key: 1 for key in INGREDIENT_QUALITY_REPORT_KEYS}
    payload.pop("reserve_selector_health")
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient quality report missing: reserve_selector_health" in result.output


def test_ingredients_inspect_rejects_unknown_quality_report_key(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).read_text())
    payload["operator_hint"] = "private"
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient quality report unsupported: operator_hint" in result.output


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        (
            "difficulty_lane_coverage",
            {"impossible": 1},
            "ingredient quality report unsupported: difficulty_lane_coverage:impossible",
        ),
        (
            "bridge_coverage",
            {"missing_bridge": 1},
            "ingredient quality report unsupported: bridge_coverage:missing_bridge",
        ),
        (
            "estimated_theorem_space_size",
            -1,
            "ingredient quality report invalid: estimated_theorem_space_size",
        ),
        (
            "shortcut_risk_distribution",
            {"operator_only": 1},
            "ingredient quality report unsupported: shortcut_risk_distribution:operator_only",
        ),
        (
            "reserve_selector_health",
            {"ready": "yes"},
            "ingredient quality report invalid: reserve_selector_health:ready",
        ),
        (
            "reserve_selector_health",
            {"healthy": True},
            "ingredient quality report unsupported: reserve_selector_health:healthy",
        ),
    ],
)
def test_ingredients_inspect_rejects_invalid_quality_report_shape(
    tmp_path,
    key: str,
    value: object,
    message: str,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).read_text())
    payload[key] = value
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_quality_report_count_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).read_text())
    payload["fact_count"] = 2
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient report count mismatch: ingredient_quality_report:fact_count" in result.output


def test_ingredients_inspect_rejects_noncanonical_repository_report(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    path = root / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient report noncanonical: ingredient_quality_report" in result.output


@pytest.mark.parametrize("report_id", tuple(INGREDIENT_REPOSITORY_REPORT_PATHS))
def test_ingredients_inspect_rejects_symlink_repository_report(
    tmp_path, report_id: str
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    path = root / INGREDIENT_REPOSITORY_REPORT_PATHS[report_id]
    external_path = tmp_path / f"{report_id}.external.json"
    external_path.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external_path)

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert f"ingredient report path invalid: {report_id}" in result.output


def test_ingredients_inspect_rejects_extraction_report_count_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text())
    payload["definition_count"] = 2
    payload["source_row_count"] = 5
    payload["source_license_counts"] = {"Apache-2.0": 5}
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient report count mismatch: extraction_report:definition_count" in result.output


def test_ingredients_inspect_rejects_unknown_extraction_report_key(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text())
    payload["operator_hint"] = "private"
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient extraction report unsupported: operator_hint" in result.output


def test_ingredients_inspect_rejects_extraction_report_mathlib_commit_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text())
    payload["mathlib_commit"] = "def456"
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient extraction report mathlib commit mismatch" in result.output


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("mathlib_commit", "", "ingredient extraction report invalid: mathlib_commit"),
        ("mathlib_commit", "0" * 6, "ingredient extraction report mathlib commit placeholder"),
        ("source_row_count", -1, "ingredient extraction report invalid: source_row_count"),
        (
            "source_license_counts",
            {"Apache-2.0": "4"},
            "ingredient extraction report invalid: source_license_counts:Apache-2.0",
        ),
    ],
)
def test_ingredients_inspect_rejects_invalid_extraction_report_shape(
    tmp_path,
    key: str,
    value: object,
    message: str,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text())
    payload[key] = value
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("source_row_count", 5, "ingredient extraction report count mismatch: source_row_count"),
        (
            "source_license_counts",
            {"Apache-2.0": 3},
            "ingredient extraction report count mismatch: source_license_counts",
        ),
    ],
)
def test_ingredients_inspect_rejects_inconsistent_extraction_report_counts(
    tmp_path,
    key: str,
    value: object,
    message: str,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    payload = json.loads((root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text())
    payload[key] = value
    (root / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).write_bytes(
        _ingredient_json_bytes(payload)
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_missing_soundness_templates(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    (root / "recipes" / "soundness_templates" / "fixture.lean").unlink()

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient soundness templates missing" in result.output


def test_ingredients_inspect_rejects_missing_recipe_soundness_template(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/missing.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe soundness template missing: recipe_rules:list_length_v1" in result.output


@pytest.mark.parametrize(
    "soundness_template",
    (
        "../private.lean",
        "/tmp/fixture.lean",
        "soundness_templates/private/path.lean",
        "soundness_templates/private template.lean",
    ),
)
def test_ingredients_inspect_rejects_private_soundness_template_path(
    tmp_path, soundness_template: str
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template=soundness_template,
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe soundness template invalid: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_unused_soundness_template(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    extra_template = root / "recipes" / "soundness_templates" / "operator_note.lean"
    extra_template.write_text("theorem operator_note : True := by\n  trivial\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe soundness template unused: soundness_templates/operator_note.lean" in result.output


@pytest.mark.parametrize(
    ("relative_path", "message"),
    (
        ("operator_note.txt", "ingredient recipe soundness template unexpected: soundness_templates/operator_note.txt"),
        ("private/operator_note.lean", "ingredient recipe soundness template unexpected: soundness_templates/private"),
    ),
)
def test_ingredients_inspect_rejects_unexpected_soundness_template_entry(
    tmp_path, relative_path: str, message: str
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    extra_template = root / "recipes" / "soundness_templates" / relative_path
    extra_template.parent.mkdir(parents=True, exist_ok=True)
    extra_template.write_text("theorem operator_note : True := by\n  trivial\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_soundness_template_forbidden_token(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    template = root / "recipes" / "soundness_templates" / "fixture.lean"
    template.write_text("theorem fixture_soundness : True := by\n  sorry\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe soundness template forbidden token: recipe_rules:list_length_v1:sorry" in result.output


def test_ingredients_inspect_rejects_soundness_template_without_declaration(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    template = root / "recipes" / "soundness_templates" / "fixture.lean"
    template.write_text("-- comment-only template\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe soundness template declaration missing: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_private_soundness_template_import(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    template = root / "recipes" / "soundness_templates" / "fixture.lean"
    template.write_text(
        "import Private.OperatorHints\n\n"
        "theorem fixture_soundness : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe soundness template import invalid: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_invalid_recipe_artifact(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).write_bytes(
        _ingredient_json_bytes({"recipes": [{"recipe_id": "broken"}]})
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe artifact invalid: recipe_rules" in result.output


def test_ingredients_inspect_rejects_recipe_rules_extra_top_level_field(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    path = root / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["extra_field"] = "ignored"
    path.write_bytes(_ingredient_json_bytes(payload))

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe artifact invalid: recipe_rules" in result.output


def test_ingredients_inspect_rejects_noncanonical_recipe_artifact(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_text(
        json.dumps({"Nat": ["2"]}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe artifact noncanonical: parameter_sets" in result.output


def test_ingredients_inspect_allows_none_parameter_rule_without_parameter_sets(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(_ingredient_json_bytes({}))
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output


def test_ingredients_inspect_allows_finite_bool_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_bool",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Bool": ["false", "true"]})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output


def test_ingredients_inspect_allows_finite_int_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_int",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Int": ["-1", "0", "2"]})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code == 0, result.output


def test_ingredients_inspect_rejects_unused_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Nat": ["2"]})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set unsupported: parameter_sets:Nat" in result.output


def test_ingredients_inspect_rejects_unsupported_recipe_parameter_rule(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="epoch_seeded_predicate_and_function",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert (
        "ingredient recipe parameter rule unsupported: "
        "recipe_rules:list_length_v1:epoch_seeded_predicate_and_function"
    ) in result.output


def test_ingredients_inspect_rejects_unsupported_recipe_version(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=2,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe version unsupported: recipe_rules:list_length_v1:2" in result.output


def test_ingredients_inspect_rejects_recipe_preconditions_until_supported(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            preconditions=("list_element_type_nonempty",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert (
        "ingredient recipe precondition unsupported: "
        "recipe_rules:list_length_v1:list_element_type_nonempty"
    ) in result.output


def test_ingredients_inspect_rejects_recipe_difficulty_delta_until_supported(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
            difficulty_delta=2,
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe difficulty delta unsupported: recipe_rules:list_length_v1:2" in result.output


def test_ingredients_inspect_rejects_missing_finite_nat_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(_ingredient_json_bytes({}))
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set missing: recipe_rules:list_length_v1:Nat" in result.output


def test_ingredients_inspect_rejects_missing_finite_bool_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_bool",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(_ingredient_json_bytes({}))
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set missing: recipe_rules:list_length_v1:Bool" in result.output


def test_ingredients_inspect_rejects_missing_finite_int_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_int",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(_ingredient_json_bytes({}))
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set missing: recipe_rules:list_length_v1:Int" in result.output


@pytest.mark.parametrize("values", (["two"], ["02"]))
def test_ingredients_inspect_rejects_invalid_finite_nat_parameter_set(
    tmp_path, values: list[str]
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Nat": values})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set invalid: recipe_rules:list_length_v1:Nat" in result.output


def test_ingredients_inspect_rejects_invalid_finite_bool_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_bool",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Bool": ["yes"]})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set invalid: recipe_rules:list_length_v1:Bool" in result.output


@pytest.mark.parametrize("values", (["+1"], ["-0"], ["01"]))
def test_ingredients_inspect_rejects_invalid_finite_int_parameter_set(
    tmp_path, values: list[str]
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_int",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Int": values})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set invalid: recipe_rules:list_length_v1:Int" in result.output


def test_ingredients_inspect_rejects_extra_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Nat": ["2"], "Other": ["x"]})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set unsupported: parameter_sets:Other" in result.output


def test_ingredients_inspect_rejects_duplicate_parameter_set_value(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Nat": ["2", "2"]})
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set duplicate: parameter_sets:Nat:2" in result.output


@pytest.mark.parametrize(
    ("parameter_rule", "values", "message"),
    (
        ("finite_nat", {"Nat": ["3", "2"]}, "ingredient recipe parameter set order invalid: parameter_sets:Nat"),
        ("finite_int", {"Int": ["0", "-1"]}, "ingredient recipe parameter set order invalid: parameter_sets:Int"),
        (
            "finite_bool",
            {"Bool": ["true", "false"]},
            "ingredient recipe parameter set order invalid: parameter_sets:Bool",
        ),
    ),
)
def test_ingredients_inspect_rejects_unsorted_parameter_set_values(
    tmp_path, parameter_rule: str, values: dict[str, list[str]], message: str
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule=parameter_rule,
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle",),
        ),
    )
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(_ingredient_json_bytes(values))
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert message in result.output


def test_ingredients_inspect_rejects_duplicate_recipe_id(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    recipe = RecipeRule(
        recipe_id="list_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length",),
        required_fact_kinds=("lemma",),
        parameter_rule="none",
        soundness_template="soundness_templates/fixture.lean",
        shortcut_checks=("source_oracle",),
    )
    _write_recipe_rules(root, recipe, recipe)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe id duplicate: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_recipe_rule_order(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    recipe = RecipeRule(
        recipe_id="list_length_v1",
        version=1,
        domains=("List", "Nat"),
        required_ingredient_classes=("list_definition", "list_fact"),
        required_definitions=("List.length",),
        required_fact_kinds=("lemma",),
        parameter_rule="none",
        soundness_template="soundness_templates/fixture.lean",
        shortcut_checks=("source_oracle",),
    )
    other = recipe.model_copy(update={"recipe_id": "z_list_length_v1"})
    _write_recipe_rules(root, other, recipe)
    bundle_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["recipe_bundle_sha256"]
    bundle_path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "recipes": ["z_list_length_v1", "list_length_v1"]})
    )
    component_hashes["recipe_bundle_sha256"] = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe order invalid: recipe_rules" in result.output


def test_ingredients_inspect_rejects_missing_recipe_shortcut_checks(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=(),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe shortcut checks missing: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_duplicate_recipe_shortcut_check(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle", "source_oracle"),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe shortcut check duplicate: recipe_rules:list_length_v1:source_oracle" in result.output


def test_ingredients_inspect_rejects_unsupported_recipe_shortcut_check(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("recipe_template_oracle",),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert (
        "ingredient recipe shortcut check unsupported: recipe_rules:list_length_v1:recipe_template_oracle"
        in result.output
    )


def test_ingredients_inspect_rejects_recipe_shortcut_check_order(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "supported_checks": ["source_oracle", "simp"]})
    )
    component_hashes["shortcut_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="none",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("simp", "source_oracle"),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe shortcut check order invalid: recipe_rules:list_length_v1" in result.output


def test_ingredients_inspect_rejects_shortcut_policy_without_supported_checks(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"]
    policy_path.write_bytes(_ingredient_json_bytes({"schema_version": 1}))
    component_hashes["shortcut_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient shortcut policy missing: supported_checks" in result.output


def test_ingredients_inspect_rejects_unsupported_shortcut_policy_check(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "supported_checks": ["recipe_template_oracle", "source_oracle"]})
    )
    component_hashes["shortcut_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient shortcut policy unsupported: recipe_template_oracle" in result.output


def test_ingredients_inspect_rejects_shortcut_policy_check_order(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "supported_checks": ["simp", "source_oracle"]})
    )
    component_hashes["shortcut_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient shortcut policy order invalid" in result.output


def test_ingredients_inspect_rejects_recipe_shortcut_check_absent_from_policy(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"]
    policy_path.write_bytes(_ingredient_json_bytes({"schema_version": 1, "supported_checks": []}))
    component_hashes["shortcut_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe shortcut check not in policy: recipe_rules:list_length_v1:source_oracle" in result.output


def test_ingredients_inspect_rejects_novelty_policy_without_supported_checks(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes({"schema_version": 1, "novelty_cache_version": NOVELTY_CACHE_VERSION})
    )
    component_hashes["novelty_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient novelty policy missing: supported_checks" in result.output


def test_ingredients_inspect_rejects_unsupported_novelty_policy_check(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "novelty_cache_version": NOVELTY_CACHE_VERSION,
                "supported_checks": ["theorem_type_cache", "near_duplicate_skeleton"],
            }
        )
    )
    component_hashes["novelty_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient novelty policy unsupported: near_duplicate_skeleton" in result.output


def test_ingredients_inspect_rejects_novelty_policy_check_order(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "novelty_cache_version": NOVELTY_CACHE_VERSION,
                "supported_checks": ["selection_family_cache", "theorem_type_cache"],
            }
        )
    )
    component_hashes["novelty_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient novelty policy order invalid" in result.output


def test_ingredients_inspect_rejects_novelty_policy_cache_version_drift(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["novelty_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes(
            {
                "schema_version": 1,
                "novelty_cache_version": "other",
                "supported_checks": ["theorem_type_cache"],
            }
        )
    )
    component_hashes["novelty_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient novelty policy cache version invalid" in result.output


def test_ingredients_inspect_rejects_empty_parameter_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    (root / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).write_bytes(
        _ingredient_json_bytes({"Nat": []})
    )

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest), "--root", str(root)])

    assert result.exit_code != 0
    assert "ingredient recipe parameter set missing: recipe_rules:list_length_v1:Nat" in result.output


def test_ingredients_inspect_rejects_invalid_manifest_schema(tmp_path) -> None:  # noqa: ANN001
    manifest = tmp_path / "manifest.json"
    payload = json.loads(_ingredient_manifest_json())
    payload["operator_note"] = "private"
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["ingredients", "inspect", "--manifest", str(manifest)])

    assert result.exit_code != 0
    assert "ingredient manifest schema invalid" in result.output


def test_build_fixture_ingredient_registry_command_writes_one_task(tmp_path) -> None:  # noqa: ANN001
    output = tmp_path / "ingredient.registry.json"

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-fixture-ingredient-registry",
            "--output",
            str(output),
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    registry = load_task_registry(output.read_bytes())
    task = registry.tasks[0]

    assert summary["tasks"] == 1
    assert summary["registry_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert task.source_stream == "ingredient"
    assert task.metadata["supply_mode"] == "ingredient"
    assert task.metadata["active_K"] == 1
    assert task.metadata["recipe_id"] == "fixture_true_recipe_v1"
    assert task.metadata["lemma_corpus_snapshot_sha256"] == "f" * 64
    assert task.metadata["active_target_sha256"] == task.target_sha256
    assert task.metadata["ingredient_ids"] == ["True", "True.intro"]
    assert task.metadata["ingredient_count"] == 2
    assert task.metadata["hidden_lemma_count"] == 0
    assert isinstance(task.metadata["novelty_family_hash"], str)
    assert len(task.metadata["novelty_family_hash"]) == 64


def test_build_ingredient_task_command_writes_public_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    output_dir = tmp_path / "challenge"
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    difficulty_state_sha256 = hashlib.sha256(difficulty_state.read_bytes()).hexdigest()
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))
    statement = "theorem generated_list_length : True := by\n  sorry"
    signer_keypair = Keypair.create_from_uri("//LemmaIngredientTaskSigner")
    lean_gate_calls = []

    def fake_statement_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        lean_gate_calls.append(
            {
                "problem": problem,
                "proof_script": proof_script,
                "submission_policy": submission_policy,
            }
        )
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_statement_gate)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            statement,
            "--run-statement-gate",
            "--run-soundness-template-gate",
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--evidence-key-uri",
            "//LemmaIngredientTaskSigner",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    task_path = output_dir / "task.json"
    receipt_path = output_dir / "generation-receipt.json"
    gate_receipt_path = output_dir / "gate-receipt.json"
    shortcut_receipt_path = output_dir / "shortcut-receipt.json"
    envelope_path = output_dir / "generation-receipt-envelope.json"
    registry_path = output_dir / "active-registry.json"
    artifact_manifest_path = output_dir / "artifact-manifest.json"
    selection_path = output_dir / "selection-receipt.json"
    assert summary["task"] == str(task_path)
    assert summary["gate_receipt"] == str(gate_receipt_path)
    assert summary["shortcut_receipt"] == str(shortcut_receipt_path)
    assert summary["generation_receipt"] == str(receipt_path)
    assert summary["generation_receipt_envelope"] == str(envelope_path)
    assert summary["active_registry"] == str(registry_path)
    assert summary["artifact_manifest"] == str(artifact_manifest_path)

    registry = load_task_registry(registry_path.read_bytes())
    task = registry.tasks[0]
    receipt = IngredientGenerationReceipt.model_validate_json(receipt_path.read_bytes())
    envelope = IngredientGenerationReceiptEnvelope.model_validate_json(envelope_path.read_bytes())
    artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    gate_receipt = json.loads(gate_receipt_path.read_text(encoding="utf-8"))
    shortcut_receipt = json.loads(shortcut_receipt_path.read_text(encoding="utf-8"))
    epoch_seed_sha256 = hashlib.sha256(b"epoch-seed").hexdigest()

    assert task.id == "lemma.ingredient.list_length"
    assert receipt.active_task_id == task.id
    assert envelope.generation_receipt == receipt
    assert envelope.signer_id == signer_keypair.ss58_address
    assert summary["netuid"] == 467
    assert summary["tempo"] == 42
    assert summary["epoch_seed_sha256"] == epoch_seed_sha256
    assert summary["difficulty_state_sha256"] == difficulty_state_sha256
    assert summary["difficulty_lane"] == "hard"
    assert summary["challenge_seed_sha256"] == task.metadata["selection_seed_sha256"]
    assert summary["generation_receipt_sha256"] == canonical_sha256(receipt)
    assert summary["generation_receipt_envelope_sha256"] == canonical_sha256(envelope)
    assert summary["gate_receipt_sha256"] == receipt.gate_receipt_sha256
    assert summary["ingredient_repo_commit"] == receipt.ingredient_repo_commit
    assert summary["lemma_corpus_snapshot_sha256"] == receipt.lemma_corpus_snapshot_sha256
    assert summary["mathlib_commit"] == receipt.mathlib_commit
    assert summary["novelty_family_hash"] == ingredient_novelty_family_hash(receipt.selection)
    assert summary["recipe_bundle_sha256"] == receipt.recipe_bundle_sha256
    assert summary["selected_parameters_sha256"] == canonical_sha256(
        {"selected_parameters": receipt.selection.selected_parameters}
    )
    assert summary["selected_recipe_id"] == receipt.selection.selected_recipe_id
    assert summary["selected_selector_id"] == receipt.selection.selected_selector_id
    assert summary["shortcut_receipt_sha256"] == receipt.shortcut_receipt_sha256
    assert summary["theorem_type_expr_sha256"] == text_sha256(task.type_expr)
    assert artifact_manifest["netuid"] == 467
    assert artifact_manifest["tempo"] == 42
    assert artifact_manifest["epoch_seed_sha256"] == epoch_seed_sha256
    assert artifact_manifest["difficulty_state_sha256"] == difficulty_state_sha256
    assert artifact_manifest["difficulty_lane"] == "hard"
    assert artifact_manifest["active_target_sha256"] == task.target_sha256
    assert artifact_manifest["theorem_statement_sha256"] == receipt.theorem_statement_sha256
    assert artifact_manifest["selected_recipe_id"] == receipt.selection.selected_recipe_id
    assert artifact_manifest["selected_selector_id"] == receipt.selection.selected_selector_id
    assert artifact_manifest["ingredient_repo_commit"] == receipt.ingredient_repo_commit
    assert artifact_manifest["lemma_corpus_snapshot_sha256"] == receipt.lemma_corpus_snapshot_sha256
    assert artifact_manifest["mathlib_commit"] == receipt.mathlib_commit
    assert artifact_manifest["novelty_family_hash"] == ingredient_novelty_family_hash(receipt.selection)
    assert artifact_manifest["recipe_bundle_sha256"] == receipt.recipe_bundle_sha256
    assert artifact_manifest["selected_parameters_sha256"] == canonical_sha256(
        {"selected_parameters": receipt.selection.selected_parameters}
    )
    assert artifact_manifest["theorem_type_expr_sha256"] == text_sha256(task.type_expr)
    assert artifact_manifest["artifacts"]["task"]["path"] == "task.json"
    assert artifact_manifest["artifacts"]["selection_receipt"]["path"] == "selection-receipt.json"
    assert artifact_manifest["artifacts"]["gate_receipt"]["path"] == "gate-receipt.json"
    assert artifact_manifest["artifacts"]["shortcut_receipt"]["path"] == "shortcut-receipt.json"
    assert str(tmp_path) not in artifact_manifest_path.read_text(encoding="utf-8")
    assert gate_receipt["runner"] == "lean-statement-gate"
    assert "lean_challenge_typechecked" in gate_receipt["checks"]
    assert "soundness_template_bound" in gate_receipt["checks"]
    assert "soundness_template_typechecked" in gate_receipt["checks"]
    assert "soundness_template_no_holes" in gate_receipt["checks"]
    assert "novelty_cache_bound" in gate_receipt["checks"]
    assert "theorem_type_not_in_novelty_cache" in gate_receipt["checks"]
    assert "selection_family_not_in_novelty_cache" in gate_receipt["checks"]
    assert gate_receipt["details"]["selected_selector_id"] == receipt.selection.selected_selector_id
    assert gate_receipt["details"]["selected_recipe_id"] == receipt.selection.selected_recipe_id
    assert gate_receipt["details"]["selected_parameters"] == receipt.selection.selected_parameters
    assert gate_receipt["details"]["selected_parameters_sha256"] == canonical_sha256(
        {"selected_parameters": receipt.selection.selected_parameters}
    )
    assert gate_receipt["details"]["soundness_template"] == "soundness_templates/fixture.lean"
    assert gate_receipt["details"]["theorem_type_expr_sha256"] == text_sha256("True")
    assert gate_receipt["details"]["novelty_gate"]["novelty_cache_entries"] == 1
    assert gate_receipt["details"]["novelty_gate"]["novelty_family_cache_entries"] == 0
    assert gate_receipt["details"]["novelty_gate"]["novelty_family_hash"] == ingredient_novelty_family_hash(
        receipt.selection
    )
    assert gate_receipt["details"]["novelty_gate"]["novelty_cache_version"] == NOVELTY_CACHE_VERSION
    assert gate_receipt["details"]["novelty_gate"]["novelty_policy_check"] == "theorem_type_cache"
    assert shortcut_receipt["runner"] == "source-oracle-exact-match-v1"
    assert "recipe_shortcut_policy_bound" in shortcut_receipt["checks"]
    assert shortcut_receipt["details"]["declared_shortcut_checks"] == ["source_oracle"]
    assert shortcut_receipt["details"]["selected_fact_ids"] == list(receipt.selection.selected_fact_ids)
    assert shortcut_receipt["details"]["selected_selector_id"] == receipt.selection.selected_selector_id
    assert shortcut_receipt["details"]["selected_recipe_id"] == receipt.selection.selected_recipe_id
    assert "no_source_fact_type_exact_match" in shortcut_receipt["checks"]
    assert shortcut_receipt["details"]["source_oracle_mode"] == "exact_type_catalog_v1"
    assert shortcut_receipt["details"]["source_fact_count"] == 3
    assert len(lean_gate_calls) == 2
    statement_gate = lean_gate_calls[0]
    soundness_gate = lean_gate_calls[1]
    assert statement_gate["problem"].extra["challenge_full"] == statement
    assert statement_gate["problem"].extra["lean_build_target"] == "Challenge"
    assert statement_gate["submission_policy"] == "strict_envelope"
    assert "sorry" in statement_gate["proof_script"]
    assert "list_length_soundness" in soundness_gate["problem"].extra["challenge_full"]
    assert soundness_gate["problem"].extra["lean_build_target"] == "Challenge"
    assert soundness_gate["submission_policy"] == "strict_envelope"

    verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task_path),
            "--generation-receipt-envelope",
            str(envelope_path),
            "--verify-envelope-signatures",
            "--challenge-seed-sha256",
            summary["challenge_seed_sha256"],
            "--difficulty-lane",
            "hard",
        ],
    )

    assert verify.exit_code == 0, verify.output
    verify_summary = json.loads(verify.output)
    assert verify_summary["envelope_signature_status"] == "verified"
    assert verify_summary["generation_receipt_envelope_sha256s"] == [canonical_sha256(envelope)]
    assert verify_summary["selected_recipe_id"] == receipt.selection.selected_recipe_id
    assert verify_summary["selected_selector_id"] == receipt.selection.selected_selector_id
    assert selection_path.exists()
    missing_novelty_cache = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
        ],
    )

    assert missing_novelty_cache.exit_code != 0
    assert "ingredient task artifact novelty cache required" in missing_novelty_cache.output

    noncanonical_novelty_cache = tmp_path / "novelty-cache-pretty.jsonl"
    noncanonical_novelty_cache.write_text(
        json.dumps({"statement_hash": "0" * 64}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    noncanonical_novelty_cache_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(noncanonical_novelty_cache),
        ],
    )

    assert noncanonical_novelty_cache_verify.exit_code != 0
    assert "novelty cache row noncanonical" in noncanonical_novelty_cache_verify.output

    bundle_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--verify-envelope-signatures",
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert bundle_verify.exit_code == 0, bundle_verify.output
    bundle_summary = json.loads(bundle_verify.output)
    assert bundle_summary["bundle_status"] == "verified"
    assert bundle_summary["envelope_signature_status"] == "verified"
    assert bundle_summary["active_task_id"] == task.id
    assert bundle_summary["netuid"] == 467
    assert bundle_summary["tempo"] == 42
    assert bundle_summary["epoch_seed_sha256"] == epoch_seed_sha256
    assert bundle_summary["difficulty_state_sha256"] == difficulty_state_sha256
    assert bundle_summary["difficulty_lane"] == "hard"
    assert bundle_summary["generation_receipt_sha256"] == canonical_sha256(receipt)
    assert bundle_summary["ingredient_repo_commit"] == receipt.ingredient_repo_commit
    assert bundle_summary["lemma_corpus_snapshot_sha256"] == receipt.lemma_corpus_snapshot_sha256
    assert bundle_summary["mathlib_commit"] == receipt.mathlib_commit
    assert bundle_summary["novelty_family_hash"] == ingredient_novelty_family_hash(receipt.selection)
    assert bundle_summary["recipe_bundle_sha256"] == receipt.recipe_bundle_sha256
    assert bundle_summary["selected_parameters_sha256"] == canonical_sha256(
        {"selected_parameters": receipt.selection.selected_parameters}
    )
    assert bundle_summary["selected_recipe_id"] == receipt.selection.selected_recipe_id
    assert bundle_summary["selected_selector_id"] == receipt.selection.selected_selector_id
    assert bundle_summary["theorem_type_expr_sha256"] == text_sha256(task.type_expr)

    second_signer_keypair = Keypair.create_from_uri("//LemmaIngredientTaskSigner2")
    second_signable_envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id=second_signer_keypair.ss58_address,
        signature="pending",
    )
    second_envelope = ingredient_generation_receipt_envelope(
        receipt,
        signer_id=second_signer_keypair.ss58_address,
        signature="0x"
        + second_signer_keypair.sign(
            ingredient_generation_receipt_envelope_signing_payload(second_signable_envelope)
        ).hex(),
    )
    second_envelope_path = tmp_path / "generation-receipt-envelope-2.json"
    second_envelope_path.write_bytes(canonical_json_bytes(second_envelope) + b"\n")
    symlink_extra_envelope_path = tmp_path / "generation-receipt-envelope-link.json"
    symlink_extra_envelope_path.symlink_to(second_envelope_path)
    symlink_extra_envelope = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--generation-receipt-envelope",
            str(symlink_extra_envelope_path),
            "--generation-receipt-envelope-quorum",
            "2",
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert symlink_extra_envelope.exit_code != 0
    assert "ingredient generation receipt envelope path invalid" in symlink_extra_envelope.output
    quorum_bundle_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--generation-receipt-envelope",
            str(second_envelope_path),
            "--generation-receipt-envelope-quorum",
            "2",
            "--verify-envelope-signatures",
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert quorum_bundle_verify.exit_code == 0, quorum_bundle_verify.output
    quorum_bundle_summary = json.loads(quorum_bundle_verify.output)
    assert quorum_bundle_summary["generation_receipt_envelope_quorum"] == 2
    assert quorum_bundle_summary["envelope_signature_status"] == "verified"
    assert quorum_bundle_summary["generation_receipt_envelope_sha256s"] == [
        canonical_sha256(envelope),
        canonical_sha256(second_envelope),
    ]

    production_light_bundle = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert production_light_bundle.exit_code != 0
    assert "production ingredient bundle gate checks missing: soundness_template_witness_checked" in (
        production_light_bundle.output
    )

    original_task_bytes = task_path.read_bytes()
    original_registry_bytes = registry_path.read_bytes()
    original_registry_payload = json.loads(original_registry_bytes)
    original_artifact_manifest_bytes = artifact_manifest_path.read_bytes()
    original_artifact_manifest = json.loads(original_artifact_manifest_bytes)
    task_path.write_text(
        json.dumps(json.loads(original_task_bytes), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["artifacts"]["task"]["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    noncanonical_task = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert noncanonical_task.exit_code != 0
    assert "ingredient task artifact noncanonical: task" in noncanonical_task.output
    task_path.write_bytes(original_task_bytes)
    artifact_manifest_path.write_bytes(original_artifact_manifest_bytes)

    extra_bundle_file = output_dir / "operator-note.txt"
    extra_bundle_file.write_text("private", encoding="utf-8")
    extra_bundle_path = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert extra_bundle_path.exit_code != 0
    assert "ingredient task artifact unexpected path: operator-note.txt" in extra_bundle_path.output
    extra_bundle_file.unlink()

    external_task_path = tmp_path / "external-task.json"
    external_task_path.write_bytes(original_task_bytes)
    task_path.unlink()
    task_path.symlink_to(external_task_path)
    symlink_task_path = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert symlink_task_path.exit_code != 0
    assert "ingredient task artifact path invalid: task" in symlink_task_path.output
    task_path.unlink()
    task_path.write_bytes(original_task_bytes)
    external_task_path.unlink()

    task_path.unlink()
    task_path.mkdir()
    directory_task_path = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert directory_task_path.exit_code != 0
    assert "ingredient task artifact path invalid: task" in directory_task_path.output
    task_path.rmdir()
    task_path.write_bytes(original_task_bytes)

    bundle_symlink_path = tmp_path / "challenge-link"
    bundle_symlink_path.symlink_to(output_dir, target_is_directory=True)
    symlink_bundle_path = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(bundle_symlink_path),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert symlink_bundle_path.exit_code != 0
    assert "ingredient task bundle path invalid" in symlink_bundle_path.output
    bundle_symlink_path.unlink()

    external_manifest_path = tmp_path / "external-artifact-manifest.json"
    external_manifest_path.write_bytes(original_artifact_manifest_bytes)
    artifact_manifest_path.unlink()
    artifact_manifest_path.symlink_to(external_manifest_path)
    symlink_artifact_manifest_path = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert symlink_artifact_manifest_path.exit_code != 0
    assert "ingredient task artifact manifest path invalid" in symlink_artifact_manifest_path.output
    artifact_manifest_path.unlink()
    artifact_manifest_path.write_bytes(original_artifact_manifest_bytes)
    external_manifest_path.unlink()

    alternate_task_path = output_dir / "operator-note" / "task.json"
    alternate_task_path.parent.mkdir()
    alternate_task_path.write_bytes(task_path.read_bytes())
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["artifacts"]["task"]["path"] = "operator-note/task.json"
    artifact_manifest["artifacts"]["task"]["sha256"] = hashlib.sha256(
        alternate_task_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    artifact_path_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert artifact_path_drift.exit_code != 0
    assert "ingredient task artifact manifest schema invalid" in artifact_path_drift.output
    alternate_task_path.unlink()
    alternate_task_path.parent.rmdir()
    artifact_manifest_path.write_bytes(original_artifact_manifest_bytes)

    registry_with_extra = json.loads(canonical_json_bytes(original_registry_payload))
    registry_with_extra["operator_note"] = "private"
    registry_path.write_bytes(canonical_json_bytes(registry_with_extra) + b"\n")
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["artifacts"]["active_registry"]["sha256"] = hashlib.sha256(
        registry_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    extra_registry_field = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert extra_registry_field.exit_code != 0
    assert "task registry unknown top-level field: operator_note" in extra_registry_field.output

    registry_with_created_at = json.loads(canonical_json_bytes(original_registry_payload))
    registry_with_created_at["created_at"] = "2026-05-31T00:00:00Z"
    registry_path.write_bytes(canonical_json_bytes(registry_with_created_at) + b"\n")
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["artifacts"]["active_registry"]["sha256"] = hashlib.sha256(
        registry_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    registry_created_at = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert registry_created_at.exit_code != 0
    assert "task registry created_at is not allowed" in registry_created_at.output

    registry_with_signature = json.loads(canonical_json_bytes(original_registry_payload))
    registry_with_signature["signed_by"] = "fixture-signer"
    registry_with_signature["signature"] = "fixture-signature"
    registry_path.write_bytes(canonical_json_bytes(registry_with_signature) + b"\n")
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["artifacts"]["active_registry"]["sha256"] = hashlib.sha256(
        registry_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    registry_signature_metadata = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert registry_signature_metadata.exit_code != 0
    assert "task registry signature metadata is not allowed" in registry_signature_metadata.output

    registry_path.write_bytes(original_registry_bytes)
    artifact_manifest_path.write_bytes(original_artifact_manifest_bytes)
    artifact_manifest = json.loads(original_artifact_manifest_bytes)

    drifted_difficulty_state = tmp_path / "difficulty-state-drift.jsonl"
    drifted_difficulty_state.write_bytes(
        _difficulty_state_jsonl(
            {"tempo": 1, "difficulty_lane": "easy"},
            {"tempo": 42, "difficulty_lane": "hard"},
        )
    )
    difficulty_state_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(drifted_difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert difficulty_state_drift.exit_code != 0
    assert "ingredient difficulty state sha256 mismatch" in difficulty_state_drift.output

    wrong_epoch_seed = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--epoch-seed",
            "different-epoch-seed",
        ],
    )

    assert wrong_epoch_seed.exit_code != 0
    assert "ingredient task artifact epoch seed mismatch" in wrong_epoch_seed.output

    seed_drift_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    seed_drift_manifest["netuid"] = 468
    artifact_manifest_path.write_bytes(canonical_json_bytes(seed_drift_manifest) + b"\n")
    seed_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert seed_drift.exit_code != 0
    assert "ingredient task artifact challenge seed mismatch" in seed_drift.output
    artifact_manifest_path.write_bytes(canonical_json_bytes(original_artifact_manifest) + b"\n")

    selection_metadata_drift_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    selection_metadata_drift_manifest["selected_selector_id"] = "operator_note"
    artifact_manifest_path.write_bytes(canonical_json_bytes(selection_metadata_drift_manifest) + b"\n")
    selection_metadata_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert selection_metadata_drift.exit_code != 0
    assert "ingredient task artifact selection metadata mismatch" in (
        selection_metadata_drift.output
    )
    artifact_manifest_path.write_bytes(canonical_json_bytes(original_artifact_manifest) + b"\n")

    target_drift_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    target_drift_manifest["theorem_statement_sha256"] = text_sha256("theorem operator_note : True := by\n  trivial")
    artifact_manifest_path.write_bytes(canonical_json_bytes(target_drift_manifest) + b"\n")
    target_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert target_drift.exit_code != 0
    assert "ingredient task artifact target mismatch" in target_drift.output
    artifact_manifest_path.write_bytes(canonical_json_bytes(original_artifact_manifest) + b"\n")

    realized_context_drift_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    realized_context_drift_manifest["selected_parameters_sha256"] = "e" * 64
    artifact_manifest_path.write_bytes(canonical_json_bytes(realized_context_drift_manifest) + b"\n")
    realized_context_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert realized_context_drift.exit_code != 0
    assert "ingredient task artifact realized context mismatch" in realized_context_drift.output
    artifact_manifest_path.write_bytes(canonical_json_bytes(original_artifact_manifest) + b"\n")

    provenance_drift_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    provenance_drift_manifest["ingredient_repo_commit"] = "def456"
    artifact_manifest_path.write_bytes(canonical_json_bytes(provenance_drift_manifest) + b"\n")
    provenance_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert provenance_drift.exit_code != 0
    assert "ingredient task artifact provenance mismatch" in provenance_drift.output
    artifact_manifest_path.write_bytes(canonical_json_bytes(original_artifact_manifest) + b"\n")

    corpus_drift_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    corpus_drift_manifest["lemma_corpus_snapshot_sha256"] = "e" * 64
    artifact_manifest_path.write_bytes(canonical_json_bytes(corpus_drift_manifest) + b"\n")
    corpus_drift = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert corpus_drift.exit_code != 0
    assert "ingredient task artifact provenance mismatch" in corpus_drift.output
    artifact_manifest_path.write_bytes(canonical_json_bytes(original_artifact_manifest) + b"\n")

    original_gate_receipt = dict(gate_receipt)
    gate_receipt["checks"] = [
        check for check in gate_receipt["checks"] if check != "soundness_template_bound"
    ]
    gate_receipt_path.write_bytes(canonical_json_bytes(gate_receipt) + b"\n")
    artifact_manifest["gate_receipt_sha256"] = canonical_sha256(gate_receipt)
    artifact_manifest["artifacts"]["gate_receipt"]["sha256"] = hashlib.sha256(
        gate_receipt_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    missing_gate_check = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert missing_gate_check.exit_code != 0
    assert "ingredient statement gate required check missing: soundness_template_bound" in missing_gate_check.output
    gate_receipt_path.write_bytes(canonical_json_bytes(original_gate_receipt) + b"\n")
    artifact_manifest = original_artifact_manifest
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")

    gate_receipt = dict(original_gate_receipt)
    gate_receipt["checks"] = [*gate_receipt["checks"], "operator_note:private"]
    gate_receipt_path.write_bytes(canonical_json_bytes(gate_receipt) + b"\n")
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["gate_receipt_sha256"] = canonical_sha256(gate_receipt)
    artifact_manifest["artifacts"]["gate_receipt"]["sha256"] = hashlib.sha256(
        gate_receipt_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    unsupported_gate_check = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert unsupported_gate_check.exit_code != 0
    assert "ingredient statement gate check unsupported: operator_note:private" in unsupported_gate_check.output
    gate_receipt_path.write_bytes(canonical_json_bytes(original_gate_receipt) + b"\n")
    artifact_manifest = original_artifact_manifest
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")

    gate_receipt = dict(original_gate_receipt)
    checks = list(gate_receipt["checks"])
    gate_receipt["checks"] = [checks[1], checks[0], *checks[2:]]
    gate_receipt_path.write_bytes(canonical_json_bytes(gate_receipt) + b"\n")
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["gate_receipt_sha256"] = canonical_sha256(gate_receipt)
    artifact_manifest["artifacts"]["gate_receipt"]["sha256"] = hashlib.sha256(
        gate_receipt_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    reordered_gate_check = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert reordered_gate_check.exit_code != 0
    assert "ingredient statement gate check order invalid" in reordered_gate_check.output
    gate_receipt_path.write_bytes(canonical_json_bytes(original_gate_receipt) + b"\n")
    artifact_manifest = original_artifact_manifest
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")

    gate_receipt = dict(original_gate_receipt)
    gate_receipt["checks"] = [
        "lean_verify_reason:operator_note" if check == "lean_verify_reason:ok" else check
        for check in gate_receipt["checks"]
    ]
    gate_receipt_path.write_bytes(canonical_json_bytes(gate_receipt) + b"\n")
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["gate_receipt_sha256"] = canonical_sha256(gate_receipt)
    artifact_manifest["artifacts"]["gate_receipt"]["sha256"] = hashlib.sha256(
        gate_receipt_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    drifted_gate_reason = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert drifted_gate_reason.exit_code != 0
    assert "ingredient statement gate Lean reason invalid" in drifted_gate_reason.output
    gate_receipt_path.write_bytes(canonical_json_bytes(original_gate_receipt) + b"\n")
    artifact_manifest = original_artifact_manifest
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")

    gate_receipt = json.loads(canonical_json_bytes(original_gate_receipt))
    gate_receipt["details"]["selected_selector_id"] = "operator_note"
    gate_receipt_path.write_bytes(canonical_json_bytes(gate_receipt) + b"\n")
    artifact_manifest = json.loads(canonical_json_bytes(original_artifact_manifest))
    artifact_manifest["gate_receipt_sha256"] = canonical_sha256(gate_receipt)
    artifact_manifest["artifacts"]["gate_receipt"]["sha256"] = hashlib.sha256(
        gate_receipt_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    drifted_selector_detail = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert drifted_selector_detail.exit_code != 0
    assert "ingredient task artifact gate receipt mismatch" in drifted_selector_detail.output
    gate_receipt_path.write_bytes(canonical_json_bytes(original_gate_receipt) + b"\n")
    artifact_manifest = original_artifact_manifest
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")

    artifact_manifest["artifacts"]["task"]["sha256"] = "0" * 64
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")
    tampered = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert tampered.exit_code != 0
    assert "ingredient task artifact manifest schema invalid" in tampered.output


def test_build_ingredient_task_can_build_dynamic_k_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    manifest_model = IngredientManifest.model_validate_json(manifest.read_bytes())
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    output_dir = tmp_path / "challenge-slot"
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    difficulty_state_sha256 = hashlib.sha256(difficulty_state.read_bytes()).hexdigest()
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))
    challenge_seed = ingredient_challenge_seed_sha256(
        netuid=467,
        tempo=42,
        epoch_seed="epoch-seed",
        ingredient_manifest_sha256=manifest_sha256,
        recipe_bundle_sha256=manifest_model.recipe_bundle_sha256,
        difficulty_state_sha256=difficulty_state_sha256,
    )
    slot_seed = ingredient_challenge_slot_seed_sha256(
        challenge_seed_sha256=challenge_seed,
        queue_position=1,
        active_K=2,
    )

    def fake_statement_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_statement_gate)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--queue-position",
            "1",
            "--active-k",
            "2",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--run-statement-gate",
            "--run-soundness-template-gate",
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    task = load_task_registry((output_dir / "active-registry.json").read_bytes()).tasks[0]
    receipt = IngredientGenerationReceipt.model_validate_json((output_dir / "generation-receipt.json").read_bytes())
    artifact_manifest = json.loads((output_dir / "artifact-manifest.json").read_text(encoding="utf-8"))
    assert summary["active_K"] == 2
    assert summary["queue_position"] == 1
    assert summary["challenge_seed_sha256"] == challenge_seed
    assert summary["selection_seed_sha256"] == slot_seed
    assert artifact_manifest["active_K"] == 2
    assert artifact_manifest["queue_position"] == 1
    assert artifact_manifest["challenge_seed_sha256"] == challenge_seed
    assert artifact_manifest["selection_seed_sha256"] == slot_seed
    assert receipt.active_K == 2
    assert receipt.selection.selection_seed_sha256 == slot_seed
    assert task.queue_position == 1
    assert task.metadata["active_K"] == 2
    assert task.metadata["selection_seed_sha256"] == slot_seed

    verify_task = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(output_dir / "task.json"),
            "--generation-receipt",
            str(output_dir / "generation-receipt.json"),
            "--challenge-seed-sha256",
            challenge_seed,
            "--difficulty-lane",
            "hard",
        ],
    )
    assert verify_task.exit_code == 0, verify_task.output

    verify_bundle = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )
    assert verify_bundle.exit_code == 0, verify_bundle.output


def _build_dynamic_k_slot_bundle(
    tmp_path: Path,
    manifest: Path,
    root: Path,
    difficulty_state: Path,
    queue_position: int,
    *,
    novelty_cache: Path | None = None,
    production: bool = False,
) -> Path:
    output_dir = tmp_path / f"challenge-slot-{queue_position}"
    args = [
        "tasks",
        "build-ingredient-task",
        "--manifest",
        str(manifest),
        "--root",
        str(root),
        "--output-dir",
        str(output_dir),
        "--netuid",
        "467",
        "--tempo",
        "42",
        "--epoch-seed",
        "epoch-seed",
        "--difficulty-state-jsonl",
        str(difficulty_state),
        "--queue-position",
        str(queue_position),
        "--active-k",
        "2",
        "--ingredient-repo-commit",
        "abc123",
        "--active-task-id",
        f"lemma.ingredient.list_length.slot{queue_position}",
        "--theorem-name",
        f"generated_list_length_slot{queue_position}",
        "--realize-selected-recipe",
    ]
    if production:
        assert novelty_cache is not None
        args.extend(
            [
                "--run-statement-gate",
                "--run-soundness-template-gate",
                "--run-triviality-gate",
                "--novelty-cache-jsonl",
                str(novelty_cache),
            ]
        )
    result = CliRunner().invoke(
        main,
        args,
        env=(
            {
                "LEMMA_PREFER_PROCESS_ENV": "1",
                "LEMMA_PROTOCOL_MODE": "production",
                "LEMMA_ACTIVE_K": "2",
            }
            if production
            else None
        ),
    )

    assert result.exit_code == 0, result.output
    return output_dir


def test_assemble_ingredient_active_registry_from_dynamic_k_bundles(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    slot0 = _build_dynamic_k_slot_bundle(tmp_path, manifest, root, difficulty_state, 0)
    slot1 = _build_dynamic_k_slot_bundle(tmp_path, manifest, root, difficulty_state, 1)
    output = tmp_path / "active-registry.json"

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "assemble-ingredient-active-registry",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(slot1),
            "--bundle",
            str(slot0),
            "--output",
            str(output),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    registry = load_task_registry(output.read_bytes(), strict_top_level=True)
    assert summary["active_K"] == 2
    assert summary["bundle_count"] == 2
    assert summary["queue_positions"] == [0, 1]
    assert summary["active_registry_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert [task.queue_position for task in registry.tasks] == [0, 1]
    assert [task.id for task in registry.tasks] == [
        "lemma.ingredient.list_length.slot0",
        "lemma.ingredient.list_length.slot1",
    ]


def test_assemble_ingredient_active_registry_rejects_missing_slot_bundle(tmp_path: Path) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    slot0 = _build_dynamic_k_slot_bundle(tmp_path, manifest, root, difficulty_state, 0)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "assemble-ingredient-active-registry",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(slot0),
            "--output",
            str(tmp_path / "active-registry.json"),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient active registry bundle count mismatch" in result.output


def test_assemble_ingredient_active_registry_production_accepts_final_gate_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))

    def fake_lean_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        if problem.extra.get("ingredient_gate_kind") == "triviality":
            return VerifyResult(passed=False, reason="compile_error")
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_lean_gate)
    slot0 = _build_dynamic_k_slot_bundle(
        tmp_path,
        manifest,
        root,
        difficulty_state,
        0,
        novelty_cache=novelty_cache,
        production=True,
    )
    slot1 = _build_dynamic_k_slot_bundle(
        tmp_path,
        manifest,
        root,
        difficulty_state,
        1,
        novelty_cache=novelty_cache,
        production=True,
    )
    output = tmp_path / "active-registry.json"

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "assemble-ingredient-active-registry",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(slot1),
            "--bundle",
            str(slot0),
            "--output",
            str(output),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "2",
        },
    )

    assert result.exit_code == 0, result.output
    registry = load_task_registry(output.read_bytes(), strict_top_level=True)
    assert [task.queue_position for task in registry.tasks] == [0, 1]


def test_assemble_ingredient_active_registry_production_rejects_weak_slot_bundle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))
    slot0 = _build_dynamic_k_slot_bundle(tmp_path, manifest, root, difficulty_state, 0)
    slot1 = _build_dynamic_k_slot_bundle(tmp_path, manifest, root, difficulty_state, 1)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "assemble-ingredient-active-registry",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(slot0),
            "--bundle",
            str(slot1),
            "--output",
            str(tmp_path / "active-registry.json"),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "2",
        },
    )

    assert result.exit_code != 0
    assert "production ingredient active-registry assembly requires Lean statement gate" in result.output


def test_build_ingredient_task_can_realize_selected_recipe(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    output_dir = tmp_path / "challenge"

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--realize-selected-recipe",
        ],
    )

    assert result.exit_code == 0, result.output
    task = load_task_registry((output_dir / "active-registry.json").read_bytes()).tasks[0]
    receipt = IngredientGenerationReceipt.model_validate_json((output_dir / "generation-receipt.json").read_bytes())
    selected_nat = receipt.selection.selected_parameters["Nat"]
    assert task.type_expr == f"List.length (List.replicate {selected_nat} 0) = {selected_nat}"
    assert task.statement == f"theorem generated_list_length : {task.type_expr} := by\n  sorry"


def test_realized_ingredient_soundness_gate_uses_witness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    output_dir = tmp_path / "challenge"
    lean_gate_calls = []

    def fake_lean_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        lean_gate_calls.append(
            {
                "problem": problem,
                "proof_script": proof_script,
                "submission_policy": submission_policy,
                "verify_timeout_s": verify_timeout_s,
            }
        )
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_lean_gate)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--realize-selected-recipe",
            "--run-statement-gate",
            "--run-soundness-template-gate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(lean_gate_calls) == 2
    soundness_gate = lean_gate_calls[1]
    assert soundness_gate["problem"].extra["ingredient_gate_kind"] == "soundness_template"
    assert "list_length_soundness" in soundness_gate["problem"].extra["challenge_full"]
    assert "sorry" not in soundness_gate["proof_script"]
    assert "_root_.list_length_soundness" in soundness_gate["proof_script"]
    gate_receipt = json.loads((output_dir / "gate-receipt.json").read_text(encoding="utf-8"))
    assert "soundness_template_witness_checked" in gate_receipt["checks"]


def test_build_ingredient_task_writes_active_registry_output(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    manifest_model = IngredientManifest.model_validate_json(manifest.read_bytes())
    output_dir = tmp_path / "challenge"
    registry_output = tmp_path / "cache" / "tempo-42.registry.json"
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--active-registry-output",
            str(registry_output),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--realize-selected-recipe",
        ],
    )

    assert result.exit_code == 0, result.output
    assert registry_output.read_bytes() == (output_dir / "active-registry.json").read_bytes()
    summary = json.loads(result.output)
    assert summary["active_registry_output"] == str(registry_output)
    assert summary["active_registry_output_sha256"] == hashlib.sha256(registry_output.read_bytes()).hexdigest()
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
        active_registry_cache_dir=registry_output.parent,
        ingredient_manifest_json=manifest,
        ingredient_manifest_sha256_expected=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        ingredient_repo_commit="abc123",
        ingredient_recipe_bundle_sha256_expected=manifest_model.recipe_bundle_sha256,
        ingredient_difficulty_state_jsonl=difficulty_state,
        netuid=467,
        lean_use_docker=False,
    )
    assert task_registry_for_validation(settings, tempo=42) == load_task_registry(registry_output.read_bytes())


def test_build_ingredient_task_rejects_active_registry_output_symlink_dir(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    real_cache = tmp_path / "real-cache"
    real_cache.mkdir()
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(real_cache, target_is_directory=True)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--active-registry-output",
            str(cache_link / "tempo-42.registry.json"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--realize-selected-recipe",
        ],
    )

    assert result.exit_code != 0
    assert "active registry output directory invalid" in result.output


def test_build_ingredient_task_rejects_realized_and_supplied_statement(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--realize-selected-recipe",
        ],
    )

    assert result.exit_code != 0
    assert "--realize-selected-recipe cannot be combined" in result.output


def test_build_ingredient_task_rejects_symlink_manifest_path(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    symlink_manifest = tmp_path / "manifest-link.json"
    symlink_manifest.symlink_to(manifest)
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(symlink_manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--run-statement-gate",
            "--run-soundness-template-gate",
            "--run-triviality-gate",
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert result.exit_code != 0
    assert "ingredient manifest path invalid" in result.output


def test_build_ingredient_task_rejects_symlink_statement_file(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    external_statement = tmp_path / "statement.external.lean"
    external_statement.write_text("theorem generated_list_length : True := by\n  sorry", encoding="utf-8")
    statement_file = tmp_path / "statement.lean"
    statement_file.symlink_to(external_statement)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement-file",
            str(statement_file),
        ],
    )

    assert result.exit_code != 0
    assert "ingredient statement file path invalid" in result.output


def test_build_ingredient_task_rejects_nonempty_output_dir(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    output_dir = tmp_path / "challenge"
    output_dir.mkdir()
    (output_dir / "operator-note.txt").write_text("private", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient task artifact output directory must be empty" in result.output


def test_build_ingredient_task_rejects_symlink_output_dir(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    output_dir = tmp_path / "challenge-link"
    output_dir.symlink_to(tmp_path / "missing-challenge", target_is_directory=True)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient task artifact output directory invalid" in result.output


def test_build_ingredient_task_production_requires_final_gate_inputs(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    root.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code != 0
    assert (
        "production ingredient task artifact requires --realize-selected-recipe, --difficulty-state-jsonl, "
        "--run-statement-gate, --run-soundness-template-gate, --run-triviality-gate, --novelty-cache-jsonl"
    ) in result.output


def test_build_ingredient_task_production_uses_dynamic_active_k(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    root.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--realize-selected-recipe",
            "--run-statement-gate",
            "--run-soundness-template-gate",
            "--run-triviality-gate",
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "2",
        },
    )

    assert result.exit_code != 0
    assert "ingredient manifest schema invalid" in result.output


def test_verify_ingredient_bundle_production_requires_public_replay_inputs(tmp_path) -> None:  # noqa: ANN001
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    root = tmp_path / "ingredients-repo"
    root.mkdir()
    bundle = tmp_path / "challenge"
    bundle.mkdir()

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(bundle),
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code != 0
    assert (
        "production ingredient bundle verification requires --difficulty-state-jsonl, "
        "--epoch-seed, --novelty-cache-jsonl"
    ) in result.output


def test_verify_ingredient_task_production_requires_public_receipt_and_seed_inputs(tmp_path) -> None:  # noqa: ANN001
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    root = tmp_path / "ingredients-repo"
    root.mkdir()
    task = tmp_path / "task.json"
    task.write_text("{}\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(task),
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code != 0
    assert (
        "production ingredient task verification requires "
        "--generation-receipt or --generation-receipt-envelope, "
        "--difficulty-state-jsonl, --netuid, --epoch-seed"
    ) in result.output


def test_build_ingredient_task_production_accepts_final_gate_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))
    output_dir = tmp_path / "challenge"
    lean_gate_kinds = []

    def fake_lean_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        lean_gate_kinds.append(problem.extra.get("ingredient_gate_kind"))
        if problem.extra.get("ingredient_gate_kind") == "triviality":
            return VerifyResult(passed=False, reason="compile_error")
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_lean_gate)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--realize-selected-recipe",
            "--run-statement-gate",
            "--run-soundness-template-gate",
            "--run-triviality-gate",
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code == 0, result.output
    assert lean_gate_kinds == ["statement", "soundness_template", "triviality"]
    gate_receipt = json.loads((output_dir / "gate-receipt.json").read_text(encoding="utf-8"))
    assert "lean_challenge_typechecked" in gate_receipt["checks"]
    assert "soundness_template_typechecked" in gate_receipt["checks"]
    assert "soundness_template_witness_checked" in gate_receipt["checks"]
    assert "bounded_triviality_checked" in gate_receipt["checks"]
    assert "novelty_cache_bound" in gate_receipt["checks"]
    production_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert production_verify.exit_code == 0, production_verify.output
    production_task_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(output_dir / "task.json"),
            "--generation-receipt",
            str(output_dir / "generation-receipt.json"),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert production_task_verify.exit_code == 0, production_task_verify.output
    assert json.loads(production_task_verify.output)["generation_receipt_status"] == "verified"


@pytest.mark.parametrize(
    (
        "recipe_id",
        "extra_definition_id",
        "extra_definition_type",
        "fact_pattern",
        "fact_type_expr",
        "soundness_template",
        "soundness_theorem",
        "soundness_type_expr",
        "active_task_id",
        "theorem_name",
        "type_snippet",
        "component_kwargs",
    ),
    (
        (
            "nat_add_zero_v1",
            "Nat.add",
            "Nat -> Nat -> Nat",
            "add",
            "Nat.add 0 0 = 0",
            "soundness_templates/nat_add_zero.lean",
            "nat_add_zero_soundness",
            "Nat.add n 0 = n",
            "lemma.ingredient.nat_add_zero",
            "generated_nat_add_zero",
            "Nat.add",
            {
                "base_definition_id": None,
                "extra_definition_domain": "Nat",
                "fact_domain": "Nat",
                "ingredient_class": "nat_fact",
                "recipe_domains": ("Nat",),
                "required_ingredient_classes": ("nat_definition", "nat_fact"),
                "source_path": "Mathlib/Data/Nat/Basic.lean",
            },
        ),
        (
            "nat_mul_one_v1",
            "Nat.mul",
            "Nat -> Nat -> Nat",
            "mul",
            "Nat.mul 0 1 = 0",
            "soundness_templates/nat_mul_one.lean",
            "nat_mul_one_soundness",
            "Nat.mul n 1 = n",
            "lemma.ingredient.nat_mul_one",
            "generated_nat_mul_one",
            "Nat.mul",
            {
                "base_definition_id": None,
                "extra_definition_domain": "Nat",
                "fact_domain": "Nat",
                "ingredient_class": "nat_fact",
                "recipe_domains": ("Nat",),
                "required_ingredient_classes": ("nat_definition", "nat_fact"),
                "source_path": "Mathlib/Data/Nat/Basic.lean",
            },
        ),
        (
            "list_append_length_v1",
            "List.append",
            "List α -> List α -> List α",
            "append",
            "List.length (([] : List Nat) ++ ([] : List Nat)) = 0",
            "soundness_templates/list_append_length.lean",
            "list_append_length_soundness",
            "List.length ((List.replicate n 0) ++ (List.replicate n 1)) = n + n",
            "lemma.ingredient.list_append_length",
            "generated_append_length",
            "++",
            {},
        ),
        (
            "list_dedup_pair_length_v1",
            "List.dedup",
            "List α -> List α",
            "dedup",
            "List.dedup ([] : List Nat) = []",
            "soundness_templates/list_dedup_pair_length.lean",
            "list_dedup_pair_length_soundness",
            "List.length (List.dedup [n, n]) = 1",
            "lemma.ingredient.list_dedup_pair_length",
            "generated_dedup_pair_length",
            "List.dedup",
            {"base_definition_id": None},
        ),
        (
            "list_drop_length_v1",
            "List.drop",
            "Nat -> List α -> List α",
            "drop",
            "List.length (List.drop 0 ([] : List Nat)) = 0",
            "soundness_templates/list_drop_length.lean",
            "list_drop_length_soundness",
            "List.length (List.drop n (List.replicate (n + n) 0)) = n",
            "lemma.ingredient.list_drop_length",
            "generated_drop_length",
            "List.drop",
            {},
        ),
        (
            "list_filter_true_length_v1",
            "List.filter",
            "(α -> Bool) -> List α -> List α",
            "filter",
            "List.length (List.filter (fun _ : Nat => true) ([] : List Nat)) = 0",
            "soundness_templates/list_filter_true_length.lean",
            "list_filter_true_length_soundness",
            "List.length (List.filter (fun _ : Nat => true) (List.replicate n 0)) = n",
            "lemma.ingredient.list_filter_true_length",
            "generated_filter_true_length",
            "List.filter",
            {},
        ),
        (
            "list_map_length_v1",
            "List.map",
            "(α -> β) -> List α -> List β",
            "map",
            "List.length (List.map (fun x : Nat => x) ([] : List Nat)) = 0",
            "soundness_templates/list_map_length.lean",
            "list_map_length_soundness",
            "List.length (List.map (fun x : Nat => x) (List.replicate n 0)) = n",
            "lemma.ingredient.list_map_length",
            "generated_map_length",
            "List.map (fun x : Nat => x)",
            {},
        ),
        (
            "list_range_length_v1",
            "List.range",
            "Nat -> List Nat",
            "range",
            "List.length (List.range 0) = 0",
            "soundness_templates/list_range_length.lean",
            "list_range_length_soundness",
            "List.length (List.range n) = n",
            "lemma.ingredient.list_range_length",
            "generated_range_length",
            "List.range",
            {},
        ),
        (
            "list_reverse_length_v1",
            "List.reverse",
            "List α -> List α",
            "reverse",
            "List.length (List.reverse ([] : List Nat)) = 0",
            "soundness_templates/list_reverse_length.lean",
            "list_reverse_length_soundness",
            "List.length (List.reverse (List.replicate n 0)) = n",
            "lemma.ingredient.list_reverse_length",
            "generated_reverse_length",
            "List.reverse",
            {},
        ),
        (
            "list_take_length_v1",
            "List.take",
            "Nat -> List α -> List α",
            "take",
            "List.length (List.take 0 ([] : List Nat)) = 0",
            "soundness_templates/list_take_length.lean",
            "list_take_length_soundness",
            "List.length (List.take n (List.replicate n 0)) = n",
            "lemma.ingredient.list_take_length",
            "generated_take_length",
            "List.take",
            {},
        ),
        (
            "list_zip_length_v1",
            "List.zip",
            "List α -> List β -> List (α × β)",
            "zip",
            "List.length (List.zip ([] : List Nat) ([] : List Nat)) = 0",
            "soundness_templates/list_zip_length.lean",
            "list_zip_length_soundness",
            "List.length (List.zip (List.replicate n 0) (List.replicate n 1)) = n",
            "lemma.ingredient.list_zip_length",
            "generated_zip_length",
            "List.zip",
            {},
        ),
    ),
)
def test_build_ingredient_task_production_accepts_realized_recipe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    recipe_id: str,
    extra_definition_id: str,
    extra_definition_type: str,
    fact_pattern: str,
    fact_type_expr: str,
    soundness_template: str,
    soundness_theorem: str,
    soundness_type_expr: str,
    active_task_id: str,
    theorem_name: str,
    type_snippet: str,
    component_kwargs: dict[str, object],
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_single_extra_definition_recipe_component_tree(
        root,
        recipe_id=recipe_id,
        extra_definition_id=extra_definition_id,
        extra_definition_type=extra_definition_type,
        fact_pattern=fact_pattern,
        fact_type_expr=fact_type_expr,
        soundness_template=soundness_template,
        soundness_theorem=soundness_theorem,
        soundness_type_expr=soundness_type_expr,
        **component_kwargs,
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))
    output_dir = tmp_path / "challenge"
    lean_gate_calls = []

    def fake_lean_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        lean_gate_calls.append(
            {
                "kind": problem.extra.get("ingredient_gate_kind"),
                "proof_script": proof_script,
                "challenge_full": problem.extra.get("challenge_full", ""),
            }
        )
        if problem.extra.get("ingredient_gate_kind") == "triviality":
            return VerifyResult(passed=False, reason="compile_error")
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_lean_gate)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            active_task_id,
            "--theorem-name",
            theorem_name,
            "--realize-selected-recipe",
            "--run-statement-gate",
            "--run-soundness-template-gate",
            "--run-triviality-gate",
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code == 0, result.output
    assert [call["kind"] for call in lean_gate_calls] == ["statement", "soundness_template", "triviality"]
    assert soundness_theorem in lean_gate_calls[1]["challenge_full"]
    assert f"_root_.{soundness_theorem}" in lean_gate_calls[1]["proof_script"]
    task = load_task_registry((output_dir / "active-registry.json").read_bytes()).tasks[0]
    assert type_snippet in task.type_expr
    gate_receipt = json.loads((output_dir / "gate-receipt.json").read_text(encoding="utf-8"))
    assert "soundness_template_witness_checked" in gate_receipt["checks"]

    production_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--novelty-cache-jsonl",
            str(novelty_cache),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1", "LEMMA_PROTOCOL_MODE": "production"},
    )

    assert production_verify.exit_code == 0, production_verify.output
    production_task_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--task",
            str(output_dir / "task.json"),
            "--generation-receipt",
            str(output_dir / "generation-receipt.json"),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--netuid",
            "467",
            "--epoch-seed",
            "epoch-seed",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1", "LEMMA_PROTOCOL_MODE": "production"},
    )

    assert production_task_verify.exit_code == 0, production_task_verify.output
    assert json.loads(production_task_verify.output)["selected_recipe_id"] == recipe_id


def test_build_ingredient_task_runs_shortcut_tactic_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    policy_path = root / INGREDIENT_MANIFEST_COMPONENT_PATHS["shortcut_policy_sha256"]
    policy_path.write_bytes(
        _ingredient_json_bytes(
            {"schema_version": 1, "supported_checks": ["source_oracle", "simp", "aesop", "omega", "grind"]}
        )
    )
    component_hashes["shortcut_policy_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    _write_recipe_rules(
        root,
        RecipeRule(
            recipe_id="list_length_v1",
            version=1,
            domains=("List", "Nat"),
            required_ingredient_classes=("list_definition", "list_fact"),
            required_definitions=("List.length",),
            required_fact_kinds=("lemma",),
            parameter_rule="finite_nat",
            soundness_template="soundness_templates/fixture.lean",
            shortcut_checks=("source_oracle", "simp", "aesop", "omega", "grind"),
        ),
    )
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    output_dir = tmp_path / "challenge"
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))
    statement = "theorem generated_list_length : True := by\n  sorry"
    args = [
        "tasks",
        "build-ingredient-task",
        "--manifest",
        str(manifest),
        "--root",
        str(root),
        "--output-dir",
        str(output_dir),
        "--netuid",
        "467",
        "--tempo",
        "42",
        "--epoch-seed",
        "epoch-seed",
        "--difficulty-state-jsonl",
        str(difficulty_state),
        "--ingredient-repo-commit",
        "abc123",
        "--active-task-id",
        "lemma.ingredient.list_length",
        "--theorem-name",
        "generated_list_length",
        "--type-expr",
        "True",
        "--statement",
        statement,
    ]

    missing_flag = CliRunner().invoke(main, args)
    assert missing_flag.exit_code != 0
    assert "--run-shortcut-tactic-gate required by selected recipe shortcut checks" in missing_flag.output

    lean_gate_calls = []

    def fake_shortcut_tactic_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        lean_gate_calls.append(
            {
                "problem": problem,
                "proof_script": proof_script,
                "submission_policy": submission_policy,
            }
        )
        if problem.extra["ingredient_gate_kind"] == "statement":
            return VerifyResult(passed=True, reason="ok")
        return VerifyResult(passed=False, reason="compile_error")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_shortcut_tactic_gate)
    result = CliRunner().invoke(main, [*args, "--run-statement-gate", "--run-shortcut-tactic-gate"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    shortcut_receipt = json.loads((output_dir / "shortcut-receipt.json").read_text(encoding="utf-8"))
    assert shortcut_receipt["runner"] == "source-oracle-shortcut-tactics-v1"
    assert "shortcut_tactics_checked" in shortcut_receipt["checks"]
    assert "no_simp_shortcut" in shortcut_receipt["checks"]
    assert "no_aesop_shortcut" in shortcut_receipt["checks"]
    assert "no_omega_shortcut" in shortcut_receipt["checks"]
    assert "no_grind_shortcut" in shortcut_receipt["checks"]
    assert "shortcut_tactics_reason:compile_error" in shortcut_receipt["checks"]
    assert shortcut_receipt["details"]["shortcut_tactic_gate"]["shortcut_tactics"] == [
        "simp",
        "aesop",
        "omega",
        "grind",
    ]
    assert shortcut_receipt["details"]["shortcut_tactic_gate"]["verify_reason"] == "compile_error"
    assert shortcut_receipt["details"]["declared_shortcut_checks"] == [
        "source_oracle",
        "simp",
        "aesop",
        "omega",
        "grind",
    ]
    assert len(lean_gate_calls) == 2
    assert lean_gate_calls[0]["problem"].extra["ingredient_gate_kind"] == "statement"
    assert lean_gate_calls[1]["problem"].extra["ingredient_gate_kind"] == "shortcut_tactics"
    assert "  | simp" in lean_gate_calls[1]["proof_script"]
    assert "  | aesop" in lean_gate_calls[1]["proof_script"]
    assert "  | omega" in lean_gate_calls[1]["proof_script"]
    assert "  | grind" in lean_gate_calls[1]["proof_script"]
    assert lean_gate_calls[1]["submission_policy"] == "strict_envelope"

    verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert verify.exit_code == 0, verify.output
    assert json.loads(verify.output)["shortcut_receipt_sha256"] == summary["shortcut_receipt_sha256"]

    gate_receipt_path = output_dir / "gate-receipt.json"
    receipt_path = output_dir / "generation-receipt.json"
    envelope_path = output_dir / "generation-receipt-envelope.json"
    artifact_manifest_path = output_dir / "artifact-manifest.json"
    gate_receipt = json.loads(gate_receipt_path.read_text(encoding="utf-8"))
    generation_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    gate_receipt["runner"] = "declared-public-artifact"
    gate_receipt["checks"] = [
        check
        for check in gate_receipt["checks"]
        if check not in {"lean_challenge_typechecked", "lean_verify_reason:ok"}
    ]
    gate_receipt_path.write_bytes(canonical_json_bytes(gate_receipt) + b"\n")
    generation_receipt["gate_receipt_sha256"] = canonical_sha256(gate_receipt)
    receipt_path.write_bytes(canonical_json_bytes(generation_receipt) + b"\n")
    envelope["generation_receipt"] = generation_receipt
    envelope["generation_receipt_sha256"] = canonical_sha256(generation_receipt)
    envelope_path.write_bytes(canonical_json_bytes(envelope) + b"\n")
    artifact_manifest["gate_receipt_sha256"] = canonical_sha256(gate_receipt)
    artifact_manifest["generation_receipt_sha256"] = canonical_sha256(generation_receipt)
    artifact_manifest["generation_receipt_envelope_sha256"] = canonical_sha256(envelope)
    artifact_manifest["artifacts"]["gate_receipt"]["sha256"] = hashlib.sha256(
        gate_receipt_path.read_bytes()
    ).hexdigest()
    artifact_manifest["artifacts"]["generation_receipt"]["sha256"] = hashlib.sha256(
        receipt_path.read_bytes()
    ).hexdigest()
    artifact_manifest["artifacts"]["generation_receipt_envelope"]["sha256"] = hashlib.sha256(
        envelope_path.read_bytes()
    ).hexdigest()
    artifact_manifest_path.write_bytes(canonical_json_bytes(artifact_manifest) + b"\n")

    missing_statement_gate = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
            "--difficulty-state-jsonl",
            str(difficulty_state),
            "--epoch-seed",
            "epoch-seed",
        ],
    )

    assert missing_statement_gate.exit_code != 0
    assert "ingredient task artifact shortcut tactic requires statement gate" in missing_statement_gate.output


def test_build_ingredient_task_rejects_public_jsonl_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    difficulty_state = tmp_path / "difficulty-state.jsonl"
    difficulty_state.write_bytes(_difficulty_state_jsonl({"tempo": 42, "difficulty_lane": "hard"}))

    def fake_statement_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_statement_gate)

    base_args = [
        "tasks",
        "build-ingredient-task",
        "--manifest",
        str(manifest),
        "--root",
        str(root),
        "--output-dir",
        str(tmp_path / "challenge"),
        "--netuid",
        "467",
        "--tempo",
        "42",
        "--epoch-seed",
        "epoch-seed",
        "--difficulty-state-jsonl",
        str(difficulty_state),
        "--ingredient-repo-commit",
        "abc123",
        "--active-task-id",
        "lemma.ingredient.list_length",
        "--theorem-name",
        "generated_list_length",
        "--type-expr",
        "True",
        "--statement",
        "theorem generated_list_length : True := by\n  sorry",
        "--run-statement-gate",
    ]
    hash_drift = CliRunner().invoke(
        main,
        [
            *base_args,
            "--difficulty-state-sha256",
            "a" * 64,
        ],
    )

    assert hash_drift.exit_code != 0
    assert "ingredient difficulty state sha256 mismatch" in hash_drift.output

    lane_drift = CliRunner().invoke(
        main,
        [
            *base_args,
            "--difficulty-lane",
            "easy",
        ],
    )

    assert lane_drift.exit_code != 0
    assert "ingredient difficulty state active lane mismatch" in lane_drift.output

    noncanonical_novelty_cache = tmp_path / "novelty-cache-pretty.jsonl"
    noncanonical_novelty_cache.write_text(
        json.dumps({"statement_hash": "0" * 64}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    cache_drift = CliRunner().invoke(
        main,
        [
            *base_args,
            "--novelty-cache-jsonl",
            str(noncanonical_novelty_cache),
        ],
    )

    assert cache_drift.exit_code != 0
    assert "novelty cache row noncanonical" in cache_drift.output

    symlink_novelty_cache = tmp_path / "novelty-cache-link.jsonl"
    symlink_novelty_cache.symlink_to(noncanonical_novelty_cache)
    symlink_cache = CliRunner().invoke(
        main,
        [
            *base_args,
            "--novelty-cache-jsonl",
            str(symlink_novelty_cache),
        ],
    )

    assert symlink_cache.exit_code != 0
    assert "novelty cache path invalid" in symlink_cache.output

    duplicate_novelty_cache = tmp_path / "novelty-cache-duplicate.jsonl"
    duplicate_novelty_cache.write_bytes(
        _ingredient_json_bytes({"statement_hash": "0" * 64})
        + _ingredient_json_bytes({"statement_hash": "0" * 64})
    )
    duplicate_cache = CliRunner().invoke(
        main,
        [
            *base_args,
            "--novelty-cache-jsonl",
            str(duplicate_novelty_cache),
        ],
    )

    assert duplicate_cache.exit_code != 0
    assert "novelty cache statement_hash duplicated" in duplicate_cache.output

    unsorted_novelty_cache = tmp_path / "novelty-cache-unsorted.jsonl"
    unsorted_novelty_cache.write_bytes(
        _ingredient_json_bytes({"statement_hash": "1" * 64})
        + _ingredient_json_bytes({"statement_hash": "0" * 64})
    )
    unsorted_cache = CliRunner().invoke(
        main,
        [
            *base_args,
            "--novelty-cache-jsonl",
            str(unsorted_novelty_cache),
        ],
    )

    assert unsorted_cache.exit_code != 0
    assert "novelty cache JSONL noncanonical" in unsorted_cache.output


def test_build_ingredient_task_requires_statement_gate_for_novelty_cache(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    novelty_cache = tmp_path / "novelty-cache.jsonl"
    novelty_cache.write_bytes(_ingredient_json_bytes({"statement_hash": "0" * 64}))

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--novelty-cache-jsonl",
            str(novelty_cache),
        ],
    )

    assert result.exit_code != 0
    assert "--novelty-cache-jsonl requires --run-statement-gate" in result.output


@pytest.mark.parametrize(
    ("flag", "message"),
    (
        ("--run-soundness-template-gate", "--run-soundness-template-gate requires --run-statement-gate"),
        ("--run-shortcut-tactic-gate", "--run-shortcut-tactic-gate requires --run-statement-gate"),
    ),
)
def test_build_ingredient_task_requires_statement_gate_for_dependent_lean_gates(
    tmp_path,
    flag: str,
    message: str,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            flag,
        ],
    )

    assert result.exit_code != 0
    assert message in result.output


def test_build_ingredient_task_rejects_non_commit_repo_provenance(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "private/path",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient_repo_commit" in result.output


@pytest.mark.parametrize(
    ("active_task_id", "message"),
    (
        ("private/path", "active_task_id"),
        ("operator.note", "ingredient active task id namespace invalid"),
    ),
)
def test_build_ingredient_task_rejects_non_protocol_active_task_id(
    tmp_path,  # noqa: ANN001
    active_task_id: str,
    message: str,
) -> None:
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            active_task_id,
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert message in result.output


def test_build_ingredient_task_rejects_non_identifier_theorem_name(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "private/path",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient theorem name invalid" in result.output


def test_build_ingredient_task_rejects_noncanonical_type_expr(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            " True ",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient theorem type expression not canonical" in result.output


def test_build_ingredient_task_rejects_noncanonical_statement_skeleton(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length :  True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient theorem statement header mismatch" in result.output


def test_build_ingredient_task_rejects_statement_header_mismatch(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem other_list_length : True := by\n  sorry",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient theorem statement header mismatch" in result.output


def test_build_ingredient_task_rejects_statement_extra_declaration(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry\n\naxiom hidden_hint : False",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient theorem statement body invalid" in result.output


def test_build_ingredient_task_rejects_title(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--title",
            "Generated List Length",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient task title must be empty" in result.output


def test_build_ingredient_task_rejects_non_public_import(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--import",
            "Private.OperatorHints",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient import invalid: Private.OperatorHints" in result.output


def test_build_ingredient_task_rejects_unsorted_imports(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--import",
            "Mathlib.Data.List.Basic",
            "--import",
            "Mathlib",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient import order invalid" in result.output


def test_build_ingredient_task_triviality_gate_binds_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")
    output_dir = tmp_path / "challenge"
    statement = "theorem generated_list_length : True := by\n  sorry"
    lean_gate_calls = []

    def fake_lean_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        lean_gate_calls.append(
            {
                "problem": problem,
                "proof_script": proof_script,
                "submission_policy": submission_policy,
                "verify_timeout_s": verify_timeout_s,
            }
        )
        if problem.extra.get("ingredient_gate_kind") == "triviality":
            return VerifyResult(passed=False, reason="compile_error")
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_lean_gate)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            statement,
            "--run-statement-gate",
            "--run-triviality-gate",
        ],
    )

    assert result.exit_code == 0, result.output
    gate_receipt = json.loads((output_dir / "gate-receipt.json").read_text(encoding="utf-8"))
    assert len(lean_gate_calls) == 2
    triviality_gate = lean_gate_calls[1]
    assert triviality_gate["problem"].extra["ingredient_gate_kind"] == "triviality"
    assert triviality_gate["problem"].extra["lean_max_heartbeats"] == 200_000
    assert "first" in triviality_gate["proof_script"]
    assert "baseline_triviality_not_solved" in gate_receipt["checks"]
    assert "bounded_triviality_reason:compile_error" in gate_receipt["checks"]
    assert gate_receipt["details"]["triviality_gate"]["baseline_solved"] is False
    assert gate_receipt["details"]["triviality_gate"]["triviality_budget_heartbeats"] == 200_000

    bundle_verify = CliRunner().invoke(
        main,
        [
            "ingredients",
            "verify-bundle",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--bundle",
            str(output_dir),
        ],
    )

    assert bundle_verify.exit_code == 0, bundle_verify.output


def test_build_ingredient_task_triviality_gate_rejects_baseline_solution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "ingredients-repo"
    component_hashes = _write_ingredient_component_tree(root)
    manifest = root / "manifest.json"
    manifest.write_text(_ingredient_manifest_json(component_hashes), encoding="utf-8")

    def fake_lean_gate(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        if problem.extra.get("ingredient_gate_kind") == "triviality":
            return VerifyResult(passed=True, reason="ok")
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_lean_gate)

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-ingredient-task",
            "--manifest",
            str(manifest),
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "challenge"),
            "--netuid",
            "467",
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
            "--difficulty-state-sha256",
            "3" * 64,
            "--difficulty-lane",
            "hard",
            "--ingredient-repo-commit",
            "abc123",
            "--active-task-id",
            "lemma.ingredient.list_length",
            "--theorem-name",
            "generated_list_length",
            "--type-expr",
            "True",
            "--statement",
            "theorem generated_list_length : True := by\n  sorry",
            "--run-statement-gate",
            "--run-triviality-gate",
        ],
    )

    assert result.exit_code != 0
    assert "ingredient triviality gate failed: baseline solved theorem type" in result.output


def test_build_fixture_ingredient_registry_command_stays_hidden() -> None:
    result = CliRunner().invoke(main, ["tasks", "--help"])

    assert result.exit_code == 0
    assert "build-ingredient-task" in result.output
    assert "sign-registry" in result.output
    assert "build-fixture-ingredient-registry" not in result.output
    assert "replay-ingredient-generation" not in result.output


def test_replay_ingredient_generation_command_reports_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    output = tmp_path / "ingredient.registry.json"
    build_result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-fixture-ingredient-registry",
            "--output",
            str(output),
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
        ],
    )
    assert build_result.exit_code == 0, build_result.output
    registry = load_task_registry(output.read_bytes())
    task = registry.tasks[0]

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", lambda _settings, *, tempo: registry)
    monkeypatch.setattr(
        "lemma.validator.active_tasks_for_validation",
        lambda registry, _settings, *, tempo: registry.tasks,
    )

    result = CliRunner().invoke(
        main,
        ["tasks", "replay-ingredient-generation", "--tempo", "42"],
        env={"LEMMA_PREFER_PROCESS_ENV": "1", "LEMMA_TASK_SUPPLY_MODE": "ingredient"},
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary == {
        "active_K": 1,
        "active_task_id": task.id,
        "active_task_ids": [task.id],
        "generation_receipt_sha256": task.metadata["generation_receipt_sha256"],
        "generation_receipt_sha256s": [task.metadata["generation_receipt_sha256"]],
        "tempo": 42,
    }


def test_replay_ingredient_generation_command_runs_production_invariant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    output = tmp_path / "ingredient.registry.json"
    build_result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-fixture-ingredient-registry",
            "--output",
            str(output),
            "--tempo",
            "42",
            "--epoch-seed",
            "epoch-seed",
        ],
    )
    assert build_result.exit_code == 0, build_result.output
    registry = load_task_registry(output.read_bytes())
    calls = {}

    def reject(settings, registry_arg):  # noqa: ANN001
        calls["protocol_mode"] = settings.protocol_mode
        calls["registry"] = registry_arg
        raise RuntimeError("production invariant failed")

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", lambda _settings, *, tempo: registry)
    monkeypatch.setattr(
        "lemma.validator.active_tasks_for_validation",
        lambda registry, _settings, *, tempo: registry.tasks,
    )
    monkeypatch.setattr("lemma.protocol_invariants.enforce_production_invariants", reject)

    result = CliRunner().invoke(
        main,
        ["tasks", "replay-ingredient-generation", "--tempo", "42"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_TASK_SUPPLY_MODE": "ingredient",
        },
    )

    assert result.exit_code != 0
    assert "production invariant failed" in result.output
    assert calls == {"protocol_mode": "production", "registry": registry}

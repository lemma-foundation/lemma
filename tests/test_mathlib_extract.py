"""Off-chain Mathlib extractor tests."""

from __future__ import annotations

import json

from click.testing import CliRunner
from lemma.cli.main import main
from lemma.supply.import_graph import ImportGraphRow, import_graph_from_rows
from lemma.supply.ingredients import (
    INGREDIENT_MANIFEST_COMPONENT_PATHS,
    INGREDIENT_QUALITY_REPORT_KEYS,
    INGREDIENT_RECIPE_ARTIFACT_PATHS,
    INGREDIENT_REPOSITORY_REPORT_PATHS,
    DefinitionIngredient,
    FactIngredient,
    IngredientManifest,
    canonical_json_bytes,
    write_mathlib_ingredient_extract,
)
from lemma.supply.mathlib_extract import (
    ExtractConfig,
    _erase_universe_levels,
    _merge_elaborated_binders,
    _parse_check_output,
    _queue_depth,
    _supported_snapshot_type,
    _type_from_check_line,
    extract_definition_rows,
    extract_snapshot_rows,
)
from lemma.supply.mathlib_snapshot import MathlibSnapshotRow


def _write_fixture_mathlib(root) -> None:
    path = root / "Mathlib" / "Data" / "Nat" / "LemmaFixture.lean"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "/-!",
                "```lean",
                "/-- Example documentation, not a declaration. -/",
                "theorem DocExample.not_extracted : True := by",
                "  trivial",
                "```",
                "-/",
                "",
                "import Mathlib",
                "",
                "namespace Nat",
                "",
                "def fixture_double (n : Nat) : Nat := n + n",
                "",
                "abbrev fixture_alias : Nat := 0",
                "",
                "theorem fixture_zero_add : ∀ n : Nat, 0 + n = n := by",
                "  simp",
                "",
                "lemma fixture_one_add (n : Nat) : 1 + n = n + 1 := by",
                "  omega",
                "",
                "theorem fixture_named_arg : associator (R := Nat) = 0 ↔ True := by",
                "  simp",
                "",
                "theorem Sub.fixture_dotted : True := by",
                "  trivial",
                "",
                "namespace Fancy₂",
                "",
                "theorem fixture_unicode_namespace : True := by",
                "  trivial",
                "",
                "end Fancy₂",
                "",
                "theorem unicode₂ : True := by",
                "  trivial",
                "",
                "end Nat",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_frontier_fixture_mathlib(root) -> None:
    path = root / "Mathlib" / "Algebra" / "LemmaFixture.lean"
    path.parent.mkdir(parents=True)
    proof_lines = [f"  have h{i} : True := by trivial" for i in range(26)]
    path.write_text(
        "\n".join(
            [
                "import Mathlib",
                "",
                "namespace AlgebraFixture",
                "",
                "theorem fixture_frontier {A B C D E F : Type}",
                "    [Semiring A] [Semiring B] [Semiring C]",
                "    (f : A -> B) (g : B -> C) (h : C -> D) (i : D -> E) (j : E -> F)",
                "    (x : A) :",
                "    ((f x = f x) ∧ (g (f x) = g (f x))) ∧",
                "      ((h (g (f x)) = h (g (f x))) ∧",
                "      (True ∧ True ∧ True ∧ True ∧ True ∧ True ∧ True ∧ True)) := by",
                *proof_lines,
                "  exact ⟨⟨rfl, rfl⟩, ⟨rfl, trivial⟩⟩",
                "",
                "end AlgebraFixture",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_duplicate_fixture_mathlib(root) -> None:
    first = root / "Mathlib" / "Data" / "First.lean"
    second = root / "Mathlib" / "Data" / "Second.lean"
    first.parent.mkdir(parents=True)
    first.write_text(
        "\n".join(["namespace Fixture", "theorem dup : True := by", "  trivial", "end Fixture", ""]),
        encoding="utf-8",
    )
    second.write_text(
        "\n".join(
            [
                "namespace Fixture",
                "theorem dup : False → False := by",
                "  intro h",
                "  exact h",
                "end Fixture",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_extract_mathlib_snapshot_rows_are_deterministic(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)

    rows = extract_snapshot_rows(
        ExtractConfig(
            mathlib_root=tmp_path,
            includes=("Mathlib/Data/Nat/*.lean",),
            mathlib_rev="abc123",
        )
    )

    assert [row.theorem_name for row in rows] == [
        "Nat.fixture_zero_add",
        "Nat.fixture_one_add",
        "Nat.fixture_named_arg",
        "Nat.Sub.fixture_dotted",
    ]
    assert rows[0].imports == ("Mathlib.Data.Nat.LemmaFixture",)
    assert rows[0].topic == "Data"
    assert rows[0].subtopic == "Nat"
    assert rows[0].queue_depth == 0
    assert rows[0].proof_sha256
    assert rows[2].type_expr == "associator (R := Nat) = 0 ↔ True"
    assert rows[1].type_expr == "∀ (n : Nat), 1 + n = n + 1"


def test_extract_mathlib_snapshot_deduplicates_public_ids(tmp_path) -> None:
    _write_duplicate_fixture_mathlib(tmp_path)

    rows = extract_snapshot_rows(
        ExtractConfig(
            mathlib_root=tmp_path,
            includes=("Mathlib/Data/*.lean",),
            mathlib_rev="abc123",
        )
    )

    assert [(row.theorem_name, row.source_path) for row in rows] == [
        ("Fixture.dup", "Mathlib/Data/First.lean")
    ]


def test_extract_mathlib_definition_rows_are_deterministic(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)

    rows = extract_definition_rows(
        ExtractConfig(
            mathlib_root=tmp_path,
            includes=("Mathlib/Data/Nat/*.lean",),
            mathlib_rev="abc123",
        )
    )

    assert [(row.definition_name, row.type_signature) for row in rows] == [
        ("Nat.fixture_double", "∀ (n : Nat), Nat"),
        ("Nat.fixture_alias", "Nat"),
    ]
    assert all(row.imports == ("Mathlib.Data.Nat.LemmaFixture",) for row in rows)
    assert all(row.source_license == "Apache-2.0" for row in rows)


def test_extract_mathlib_snapshot_drops_invalid_subtopic_labels(tmp_path) -> None:
    path = tmp_path / "Mathlib" / "Tactic" / "LinearCombination'.lean"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(["namespace Mathlib.Tactic.LinearCombination'", "theorem add_pf : True := by", "  trivial", ""]),
        encoding="utf-8",
    )

    rows = extract_snapshot_rows(
        ExtractConfig(
            mathlib_root=tmp_path,
            includes=("Mathlib/Tactic/*.lean",),
            mathlib_rev="abc123",
        )
    )

    assert rows[0].topic == "Tactic"
    assert rows[0].subtopic is None


def test_extract_mathlib_snapshot_can_emit_frontier_depth(tmp_path) -> None:
    _write_frontier_fixture_mathlib(tmp_path)

    rows = extract_snapshot_rows(
        ExtractConfig(
            mathlib_root=tmp_path,
            includes=("Mathlib/Algebra/*.lean",),
            mathlib_rev="abc123",
        )
    )

    assert len(rows) == 1
    assert rows[0].difficulty_score is not None
    assert rows[0].difficulty_score >= 7
    assert rows[0].queue_depth >= 7


def test_extract_mathlib_snapshot_uses_import_graph_signals(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    root = "Mathlib.Data.Nat.LemmaFixture"
    direct = tuple(f"Mathlib.Dep.D{i}" for i in range(6))
    chain = tuple(
        ImportGraphRow(module=f"Mathlib.Dep.D{i}", imports=(f"Mathlib.Dep.D{i + 1}",)) for i in range(13)
    )
    inbound = tuple(ImportGraphRow(module=f"Mathlib.User.U{i}", imports=(root,)) for i in range(10))
    graph = import_graph_from_rows(
        (
            ImportGraphRow(module=root, imports=direct),
            *chain,
            ImportGraphRow(module="Mathlib.Dep.D13", imports=()),
            *inbound,
        )
    )

    rows = extract_snapshot_rows(
        ExtractConfig(
            mathlib_root=tmp_path,
            includes=("Mathlib/Data/Nat/*.lean",),
            mathlib_rev="abc123",
            import_graph=graph,
        )
    )

    assert rows[0].direct_dependency_count == 6
    assert rows[0].dependency_depth >= 12
    assert rows[0].citation_weight == 10
    assert rows[0].transitive_dependency_hash
    assert max(row.queue_depth for row in rows) >= 3


def test_queue_depth_preserves_full_difficulty_ladder() -> None:
    assert _queue_depth(0) == 0
    assert _queue_depth(2) == 0
    assert _queue_depth(3) == 1
    assert _queue_depth(9) == 7


def test_extract_mathlib_snapshot_cli_writes_jsonl(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    output = tmp_path / "snapshot.jsonl"

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "extract-mathlib-snapshot",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-rev",
            "abc123",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["rows"] == 4
    assert output.read_text(encoding="utf-8").count("\n") == 4


def test_extract_mathlib_ingredients_cli_writes_raw_ingredient_files(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    output = tmp_path / "lemma-ingredients"

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary == {
        "definition_count": 2,
        "fact_count": 4,
        "mathlib_commit": "abc123",
        "output": str(output),
    }
    assert (output / "mathlib_commit.txt").read_text(encoding="utf-8") == "abc123\n"
    assert (output / INGREDIENT_MANIFEST_COMPONENT_PATHS["source_theorems_sha256"]).read_text(encoding="utf-8") == ""
    assert (output / INGREDIENT_MANIFEST_COMPONENT_PATHS["compatibility_graph_sha256"]).read_text(
        encoding="utf-8"
    ) == ""
    assert json.loads((output / INGREDIENT_RECIPE_ARTIFACT_PATHS["recipe_rules"]).read_text(encoding="utf-8")) == {
        "recipes": []
    }
    assert json.loads((output / INGREDIENT_RECIPE_ARTIFACT_PATHS["parameter_sets"]).read_text(encoding="utf-8")) == {}
    definition_lines = (output / INGREDIENT_MANIFEST_COMPONENT_PATHS["definitions_sha256"]).read_text(
        encoding="utf-8"
    ).splitlines()
    definitions = [DefinitionIngredient.model_validate_json(line) for line in definition_lines]
    assert [(definition.definition_id, definition.type_signature) for definition in definitions] == [
        ("Nat.fixture_alias", "Nat"),
        ("Nat.fixture_double", "∀ (n : Nat), Nat"),
    ]
    assert {definition.domain for definition in definitions} == {"Nat"}
    assert all(definition.mathlib_commit == "abc123" for definition in definitions)
    assert all(definition.metadata["simp_risk"] == "low" for definition in definitions)
    fact_lines = (output / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"]).read_text(
        encoding="utf-8"
    ).splitlines()
    facts = [FactIngredient.model_validate_json(line) for line in fact_lines]
    assert [fact.fact_id for fact in facts] == [
        "Nat.Sub.fixture_dotted",
        "Nat.fixture_named_arg",
        "Nat.fixture_one_add",
        "Nat.fixture_zero_add",
    ]
    assert {fact.domain for fact in facts} == {"Nat"}
    assert all(fact.mathlib_commit == "abc123" for fact in facts)
    assert all(fact.metadata["usable_as_source_fact"] is True for fact in facts)
    extraction_report = json.loads(
        (output / INGREDIENT_REPOSITORY_REPORT_PATHS["extraction_report"]).read_text(encoding="utf-8")
    )
    quality_report = json.loads(
        (output / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]).read_text(encoding="utf-8")
    )
    assert extraction_report["source_row_count"] == 6
    assert extraction_report["definition_count"] == 2
    assert extraction_report["source_license_counts"] == {"Apache-2.0": 6}
    assert set(quality_report) == INGREDIENT_QUALITY_REPORT_KEYS
    assert quality_report["definition_count"] == 2
    assert quality_report["fact_count"] == 4

    manifest_result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "write-manifest",
            "--root",
            str(output),
            "--lemma-corpus-snapshot-sha256",
            "f" * 64,
        ],
    )

    assert manifest_result.exit_code == 0, manifest_result.output
    manifest = IngredientManifest.model_validate_json((output / "manifest.json").read_bytes())
    assert manifest.facts_sha256

    inspect_result = CliRunner().invoke(
        main,
        ["ingredients", "inspect", "--manifest", str(output / "manifest.json"), "--root", str(output)],
    )

    assert inspect_result.exit_code == 0, inspect_result.output


def test_mathlib_ingredient_extract_preserves_dependency_signals(tmp_path) -> None:
    output = tmp_path / "lemma-ingredients"
    write_mathlib_ingredient_extract(
        (
            MathlibSnapshotRow(
                theorem_name="Nat.deep_dependency",
                type_expr="True",
                mathlib_rev="abc123",
                source_path="Mathlib/Data/Nat/Basic.lean",
                source_license="Apache-2.0",
                queue_depth=3,
                direct_dependency_count=4,
                dependency_depth=7,
            ),
        ),
        output,
    )

    line = (output / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"]).read_text(encoding="utf-8").strip()
    fact = FactIngredient.model_validate_json(line)

    assert fact.metadata["direct_dependency_count"] == 4
    assert fact.metadata["dependency_depth"] == 7


def test_extract_mathlib_ingredients_cli_rejects_empty_extract(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    output = tmp_path / "lemma-ingredients"

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Missing/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code != 0
    assert "ingredient mathlib extraction produced no rows" in result.output
    assert not output.exists()


def test_extract_mathlib_ingredients_cli_rejects_symlink_output_root(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    real_output = tmp_path / "real-ingredients"
    real_output.mkdir()
    output = tmp_path / "lemma-ingredients"
    output.symlink_to(real_output, target_is_directory=True)

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code != 0
    assert "ingredient root path invalid" in result.output


def test_extract_mathlib_ingredients_cli_rejects_symlink_component_output(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    output = tmp_path / "lemma-ingredients"
    component = output / INGREDIENT_MANIFEST_COMPONENT_PATHS["facts_sha256"]
    component.parent.mkdir(parents=True)
    outside = tmp_path / "outside-facts.jsonl"
    outside.write_text("", encoding="utf-8")
    component.symlink_to(outside)

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code != 0
    assert "ingredient root artifact path invalid" in result.output


def test_build_compatibility_empty_scaffold_is_manifestable(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    output = tmp_path / "lemma-ingredients"
    extract = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(output),
        ],
    )
    assert extract.exit_code == 0, extract.output
    quality_path = output / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["compatibility_edge_count"] = 7
    quality_path.write_text(json.dumps(quality) + "\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["ingredients", "build-compatibility", "--root", str(output), "--no-paid-recipes"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "compatibility_edge_count": 0,
        "recipe_count": 0,
        "root": str(output),
        "status": "empty_scaffold",
    }
    assert json.loads(quality_path.read_text(encoding="utf-8"))["compatibility_edge_count"] == 0
    assert (output / INGREDIENT_MANIFEST_COMPONENT_PATHS["compatibility_graph_sha256"]).read_text(
        encoding="utf-8"
    ) == ""

    manifest = CliRunner().invoke(
        main,
        [
            "ingredients",
            "write-manifest",
            "--root",
            str(output),
            "--lemma-corpus-snapshot-sha256",
            "f" * 64,
        ],
    )

    assert manifest.exit_code == 0, manifest.output


def test_empty_ingredient_scaffold_rejects_nonzero_theorem_space(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    output = tmp_path / "lemma-ingredients"
    extract = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(output),
        ],
    )
    assert extract.exit_code == 0, extract.output
    quality_path = output / INGREDIENT_REPOSITORY_REPORT_PATHS["ingredient_quality_report"]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["estimated_theorem_space_size"] = 1
    quality_path.write_bytes(canonical_json_bytes(quality) + b"\n")

    result = CliRunner().invoke(
        main,
        [
            "ingredients",
            "write-manifest",
            "--root",
            str(output),
            "--lemma-corpus-snapshot-sha256",
            "f" * 64,
        ],
    )

    assert result.exit_code != 0
    assert "ingredient quality report theorem space unavailable" in result.output


def test_build_compatibility_without_paid_recipe_inputs_keeps_empty_scaffold(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    output = tmp_path / "lemma-ingredients"
    extract = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(output),
        ],
    )
    assert extract.exit_code == 0, extract.output

    result = CliRunner().invoke(main, ["ingredients", "build-compatibility", "--root", str(output)])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["status"] == "empty_scaffold"


def test_build_compatibility_rejects_symlink_root(tmp_path) -> None:
    _write_fixture_mathlib(tmp_path)
    real_output = tmp_path / "real-ingredients"
    extract = CliRunner().invoke(
        main,
        [
            "ingredients",
            "extract-mathlib",
            "--mathlib-root",
            str(tmp_path),
            "--include",
            "Mathlib/Data/Nat/*.lean",
            "--mathlib-commit",
            "abc123",
            "--output",
            str(real_output),
        ],
    )
    assert extract.exit_code == 0, extract.output
    output = tmp_path / "lemma-ingredients"
    output.symlink_to(real_output, target_is_directory=True)

    result = CliRunner().invoke(
        main,
        ["ingredients", "build-compatibility", "--root", str(output), "--no-paid-recipes"],
    )

    assert result.exit_code != 0
    assert "ingredient root path invalid" in result.output


def test_build_compatibility_rejects_symlink_scaffold_artifact(tmp_path) -> None:
    output = tmp_path / "lemma-ingredients"
    component = output / INGREDIENT_MANIFEST_COMPONENT_PATHS["compatibility_graph_sha256"]
    component.parent.mkdir(parents=True)
    outside = tmp_path / "outside-compatibility.jsonl"
    outside.write_text("", encoding="utf-8")
    component.symlink_to(outside)

    result = CliRunner().invoke(
        main,
        ["ingredients", "build-compatibility", "--root", str(output), "--no-paid-recipes"],
    )

    assert result.exit_code != 0
    assert "ingredient root artifact path invalid" in result.output


def test_build_compatibility_rejects_symlink_soundness_template_dir(tmp_path) -> None:
    output = tmp_path / "lemma-ingredients"
    template_dir = output / "recipes" / "soundness_templates"
    template_dir.parent.mkdir(parents=True)
    outside = tmp_path / "outside-templates"
    outside.mkdir()
    template_dir.symlink_to(outside, target_is_directory=True)

    result = CliRunner().invoke(
        main,
        ["ingredients", "build-compatibility", "--root", str(output), "--no-paid-recipes"],
    )

    assert result.exit_code != 0
    assert "ingredient root artifact path invalid" in result.output


def test_check_output_type_parser_makes_self_contained_type() -> None:
    parsed = _type_from_check_line(
        "Associated.neg_left",
        "Associated.neg_left.{u_1} {M : Type u_1} [Monoid M] [HasDistribNeg M] "
        "{a b : M} (h : Associated a b) : Associated (-a) b",
    )

    assert parsed == (
        "∀ {M : Type _} [Monoid M] [HasDistribNeg M] {a b : M} "
        "(h : Associated a b), Associated (-a) b"
    )


def test_check_output_parser_handles_wrapped_lines() -> None:
    parsed = _parse_check_output(
        "Finset.disjoint_filter.{u_1} {α : Type u_1} {s : Finset α} {p q : α → Prop} "
        "[DecidablePred p] [DecidablePred q] :\n"
        "  Disjoint (Finset.filter p s) (Finset.filter q s) ↔ ∀ x ∈ s, p x → ¬q x\n",
        ["Finset.disjoint_filter"],
    )

    assert parsed["Finset.disjoint_filter"].endswith("∀ x ∈ s, p x → ¬q x")


def test_elaborated_type_uses_self_contained_lean_type() -> None:
    merged = _merge_elaborated_binders(
        "associator (R := R) = 0",
        "∀ {R : Type _} [NonUnitalRing R], Algebra.associator = 0",
    )

    assert merged == "∀ {R : Type _} [NonUnitalRing R], Algebra.associator = 0"


def test_supported_snapshot_type_rejects_unparseable_elaborated_binders() -> None:
    assert not _supported_snapshot_type("∀ Left {R : Type _}, True")
    assert not _supported_snapshot_type("∀ {R : Type _} ⦃inst₁ inst₂, Distrib R⦄, True")
    assert _supported_snapshot_type("∀ {R : Type _} [Semiring R] (x : R), x = x")


def test_universe_level_erasure_keeps_task_type_self_contained() -> None:
    assert _erase_universe_levels("∀ {α : Type u_1}, Eq.{u_1} α α") == "∀ {α : Type _}, Eq α α"
    assert _erase_universe_levels("∀ {α : Sort (max u_1 v)}, α → Prop") == "∀ {α : Sort _}, α → Prop"

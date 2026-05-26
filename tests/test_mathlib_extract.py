"""Off-chain Mathlib extractor tests."""

from __future__ import annotations

import json

from click.testing import CliRunner
from lemma.cli.main import main
from lemma.supply.import_graph import ImportGraphRow, import_graph_from_rows
from lemma.supply.mathlib_extract import (
    ExtractConfig,
    _erase_universe_levels,
    _merge_elaborated_binders,
    _parse_check_output,
    _queue_depth,
    _supported_snapshot_type,
    _type_from_check_line,
    extract_snapshot_rows,
)


def _write_fixture_mathlib(root) -> None:
    path = root / "Mathlib" / "Data" / "Nat" / "LemmaFixture.lean"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "import Mathlib",
                "",
                "namespace Nat",
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
    ]
    assert rows[0].imports == ("Mathlib.Data.Nat.LemmaFixture",)
    assert rows[0].topic == "Data"
    assert rows[0].subtopic == "Nat"
    assert rows[0].queue_depth == 0
    assert rows[0].proof_sha256
    assert rows[2].type_expr == "associator (R := Nat) = 0 ↔ True"
    assert rows[1].type_expr == "∀ (n : Nat), 1 + n = n + 1"


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
    assert summary["rows"] == 3
    assert output.read_text(encoding="utf-8").count("\n") == 3


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

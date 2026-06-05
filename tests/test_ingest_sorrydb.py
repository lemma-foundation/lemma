"""SorryDB source ingestion (Phase 3)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.ingest.baseline import (
    DEFAULT_BASELINE_TACTICS,
    BaselineProbe,
    PreflightBaselineProber,
    build_hole_replacement_patch,
)
from lemma.ingest.report import IngestReport
from lemma.ingest.sorrydb import ingest_sorrydb_rows
from lemma.preflight import PreflightVerdict
from lemma.source_sorries import build_patch_task_from_sorrydb_record
from lemma.tasks import LemmaTask, load_task_registry

SOURCE_ROOT = Path("tests/fixtures/lean_real_source_project")

_BASIC_THEOREM = "PublicSource.add_zero_real_source"
_BASIC_TYPE = "forall n : Nat, n + 0 = n"


def _basic_source(*, holes: int = 1, theorem_name: str = "add_zero_real_source") -> str:
    body = ["  sorry"] * max(holes, 0)
    return "\n".join(
        [
            "namespace PublicSource",
            "",
            f"theorem {theorem_name} (n : Nat) : n + 0 = n := by",
            *body,
            "",
            "end PublicSource",
            "",
        ]
    )


def _source_root(tmp_path: Path, *, basic: str | None = None, lake_files: bool = True) -> Path:
    root = tmp_path / f"source-{tmp_path.name}-{id(tmp_path)}"
    if root.exists():
        shutil.rmtree(root)
    if lake_files:
        shutil.copytree(SOURCE_ROOT, root)
    else:
        (root / "RealSource").mkdir(parents=True, exist_ok=True)
    (root / "RealSource").mkdir(parents=True, exist_ok=True)
    (root / "RealSource/Basic.lean").write_text(basic if basic is not None else _basic_source(), encoding="utf-8")
    return root


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "repo": {
            "remote": "https://github.com/SorryDB/SorryDB",
            "branch": "master",
            "commit": "0" * 40,
            "lean_version": "v4.30.0-rc2",
        },
        "location": {"path": "RealSource/Basic.lean", "start_line": 3, "start_column": 3},
        "id": "sorrydb-fixture-add-zero",
        "theorem_name": _BASIC_THEOREM,
        "type_expr": _BASIC_TYPE,
    }
    row.update(overrides)
    return row


def _ingest(rows: list[Any], source_root: Path | None, **kwargs: Any):
    return ingest_sorrydb_rows(
        rows,
        resolve_source_root=lambda _row: source_root,
        source_license=kwargs.pop("source_license", "Apache-2.0"),
        mathlib_rev="fixture-mathlib-rev",
        reproduction_command="lake build RealSource",
        **kwargs,
    )


def _only_reason(report: IngestReport) -> str:
    assert len(report.quarantined) == 1, report.quarantined
    return report.quarantined[0].reason


def test_clean_row_is_accepted_with_environment_record(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    result = _ingest([_row()], source_root)

    assert result.report.accepted_count == 1
    assert result.report.quarantined_count == 0
    candidate = result.report.accepted[0]
    assert candidate.task_id == "lemma.sorrydb.sorrydb-fixture-add-zero"
    assert candidate.theorem_name == _BASIC_THEOREM
    assert candidate.repo == "https://github.com/SorryDB/SorryDB"
    assert candidate.lean_toolchain == "leanprover/lean4:v4.30.0-rc2"
    assert len(candidate.target_sha256) == 64
    assert candidate.baseline_status == "unscreened"

    assert len(result.report.environments) == 1
    env = result.report.environments[0]
    assert env.commit == "0" * 40
    assert env.environment_sha256 is not None
    assert env.lake_manifest_present is True
    assert env.certified is True
    assert env.detail == ""

    assert isinstance(result.tasks[0], LemmaTask)
    assert result.tasks[0].queue_position == 0


def test_missing_theorem_name_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    row = _row()
    del row["theorem_name"]
    result = _ingest([row], source_root)
    assert _only_reason(result.report) == "missing_theorem_name"


def test_missing_type_expr_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    row = _row()
    del row["type_expr"]
    result = _ingest([row], source_root)
    assert _only_reason(result.report) == "missing_type_expr"


def test_missing_license_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    result = _ingest([_row()], source_root, source_license="  ")
    assert _only_reason(result.report) == "missing_license"


def test_no_target_hole_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path, basic=_basic_source(holes=0))
    result = _ingest([_row()], source_root)
    assert _only_reason(result.report) == "no_target_hole"


def test_extra_holes_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path, basic=_basic_source(holes=2))
    result = _ingest([_row()], source_root)
    assert _only_reason(result.report) == "extra_holes"


def test_decl_not_found_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    result = _ingest([_row(theorem_name="PublicSource.does_not_exist")], source_root)
    assert _only_reason(result.report) == "decl_not_found"


def test_unsafe_path_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    row = _row(location={"path": "../escape.lean", "start_line": 1})
    result = _ingest([row], source_root)
    assert _only_reason(result.report) == "unsafe_path"


def test_lean3_row_is_quarantined_as_not_lean4(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    row = _row()
    row["repo"]["lean_version"] = "3.50.3"
    result = _ingest([row], source_root)
    assert _only_reason(result.report) == "not_lean4"


def test_unstable_toolchain_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    row = _row()
    row["repo"]["lean_version"] = "nightly-2025-01-01"
    result = _ingest([row], source_root)
    assert _only_reason(result.report) == "unstable_toolchain"


def test_unstable_toolchain_allowed_when_opted_in(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    row = _row()
    row["repo"]["lean_version"] = "nightly-2025-01-01"
    result = _ingest([row], source_root, allow_unstable_toolchain=True)
    assert result.report.accepted_count == 1


def test_checkout_missing_is_quarantined(tmp_path: Path) -> None:
    result = _ingest([_row()], None)
    assert _only_reason(result.report) == "checkout_missing"


def test_missing_environment_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path, lake_files=False)
    result = _ingest([_row()], source_root)
    assert _only_reason(result.report) == "missing_environment"
    assert result.report.quarantined[0].detail == "no_environment_hash"


def test_missing_lake_manifest_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    (source_root / "lake-manifest.json").unlink()
    result = _ingest([_row()], source_root)
    assert _only_reason(result.report) == "missing_environment"
    assert result.report.quarantined[0].detail == "no_lake_manifest"
    # The environment is still recorded, marked uncertified.
    assert result.report.environments[0].certified is False
    assert "no_lake_manifest" in result.report.environments[0].detail


def test_malformed_row_is_quarantined(tmp_path: Path) -> None:
    result = _ingest(["not-a-row", {"id": "no-repo"}], _source_root(tmp_path))
    assert [row.reason for row in result.report.quarantined] == ["malformed_row", "malformed_row"]


def test_duplicate_target_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    rows = [_row(), _row(id="sorrydb-fixture-duplicate")]
    result = _ingest(rows, source_root)
    assert result.report.accepted_count == 1
    assert _only_reason(result.report) == "duplicate_target"


def test_baseline_trivial_is_quarantined(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)

    def prober(_task: LemmaTask, _root: Path) -> BaselineProbe:
        return BaselineProbe(trivial=True, tactic="simp")

    result = _ingest([_row()], source_root, baseline_prober=prober)
    assert result.report.accepted_count == 0
    assert _only_reason(result.report) == "baseline_trivial"
    assert result.report.quarantined[0].detail == "simp"


def test_baseline_nontrivial_marks_candidate(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)

    def prober(_task: LemmaTask, _root: Path) -> BaselineProbe:
        return BaselineProbe(trivial=False)

    result = _ingest([_row()], source_root, baseline_prober=prober)
    assert result.report.accepted_count == 1
    assert result.report.accepted[0].baseline_status == "nontrivial"


def test_mixed_batch_never_crashes_and_summarizes(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    bad_theorem = _row(id="bad-decl", theorem_name="PublicSource.missing")
    rows = [_row(), bad_theorem, "garbage"]
    result = _ingest(rows, source_root)

    assert result.report.total_rows == 3
    assert result.report.accepted_count == 1
    summary = result.report.summary()
    assert summary["accepted"] == 1
    assert summary["quarantine_reasons"] == {"decl_not_found": 1, "malformed_row": 1}


def test_ingest_sorrydb_cli_writes_registry_and_report(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    rows = [_row(), _row(id="bad-decl", theorem_name="PublicSource.missing")]
    row_path = tmp_path / "rows.json"
    registry_path = tmp_path / "registry.json"
    report_path = tmp_path / "report.json"
    row_path.write_text(json.dumps(rows), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "ingest-sorrydb",
            "--sorry-json",
            str(row_path),
            "--source-root",
            str(source_root),
            "--source-license",
            "Apache-2.0",
            "--mathlib-rev",
            "fixture-mathlib-rev",
            "--reproduction-command",
            "lake build RealSource",
            "--output",
            str(registry_path),
            "--report",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["accepted"] == 1
    assert payload["quarantined"] == 1
    assert payload["quarantine_reasons"] == {"decl_not_found": 1}

    registry = load_task_registry(registry_path.read_bytes())
    assert [task.id for task in registry.tasks] == ["lemma.sorrydb.sorrydb-fixture-add-zero"]

    report = IngestReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.accepted_count == 1
    assert report.quarantined[0].reason == "decl_not_found"


def _patch_task(source_root: Path) -> LemmaTask:
    return build_patch_task_from_sorrydb_record(
        _row(),
        source_root=source_root,
        theorem_name=_BASIC_THEOREM,
        type_expr=_BASIC_TYPE,
        source_license="Apache-2.0",
        mathlib_rev="fixture-mathlib-rev",
        reproduction_command="lake build RealSource",
    )


def test_build_hole_replacement_patch_applies_with_git(tmp_path: Path) -> None:
    rel_path = "RealSource/Basic.lean"
    source_text = _basic_source()
    patch = build_hole_replacement_patch(rel_path, source_text, "simp")
    assert patch is not None

    work = tmp_path / "work"
    (work / "RealSource").mkdir(parents=True)
    (work / rel_path).write_text(source_text, encoding="utf-8")
    patch_path = tmp_path / "trivial.patch"
    patch_path.write_text(patch, encoding="utf-8")

    applied = subprocess.run(
        ["git", "apply", "--unidiff-zero", str(patch_path)],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stderr

    patched = (work / rel_path).read_text(encoding="utf-8")
    assert "simp" in patched
    assert "sorry" not in patched


def test_build_hole_replacement_patch_none_without_hole() -> None:
    assert build_hole_replacement_patch("X.lean", _basic_source(holes=0), "simp") is None


def test_preflight_baseline_prober_returns_first_accepting_tactic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _source_root(tmp_path)
    task = _patch_task(source_root)
    tried: list[str] = []

    def fake_preflight(_task: LemmaTask, submission: Any, **_kwargs: Any) -> PreflightVerdict:
        patch = submission.patch_text or ""
        tactic = next(t for t in DEFAULT_BASELINE_TACTICS if t in patch)
        tried.append(tactic)
        accepted = tactic == "omega"
        return PreflightVerdict(accepted=accepted, rejection_class="ok" if accepted else "lean_compile_error")

    monkeypatch.setattr("lemma.preflight.preflight_submission", fake_preflight)

    probe = PreflightBaselineProber()(task, source_root)
    assert probe.trivial is True
    assert probe.tactic == "omega"
    # Stops at the first accepting tactic and never tries the ones after it.
    assert tried == list(DEFAULT_BASELINE_TACTICS[: DEFAULT_BASELINE_TACTICS.index("omega") + 1])


def test_preflight_baseline_prober_nontrivial_when_none_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _source_root(tmp_path)
    task = _patch_task(source_root)

    def fake_preflight(_task: LemmaTask, _submission: Any, **_kwargs: Any) -> PreflightVerdict:
        return PreflightVerdict(accepted=False, rejection_class="lean_compile_error")

    monkeypatch.setattr("lemma.preflight.preflight_submission", fake_preflight)

    probe = PreflightBaselineProber()(task, source_root)
    assert probe.trivial is False
    assert probe.tactic == ""


def test_preflight_baseline_prober_handles_missing_target_file(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    task = _patch_task(source_root)
    (source_root / "RealSource/Basic.lean").unlink()

    probe = PreflightBaselineProber()(task, source_root)
    assert probe.trivial is False

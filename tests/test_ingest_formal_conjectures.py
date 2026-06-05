"""Formal Conjectures source ingestion (Phase 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from lemma.cli.main import main
from lemma.common.config import LemmaSettings
from lemma.ingest.baseline import BaselineProbe
from lemma.ingest.formal_conjectures import ingest_formal_conjecture_records
from lemma.ingest.report import IngestReport
from lemma.lean.sandbox import VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.tasks import LemmaTask, load_task_registry

_TYPE = "forall n : Nat, n + 0 = n"
_SOURCE_MODULE = "FormalConjectures.Nat.AddZero"


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "fc-add-zero",
        "category": "textbook",
        "type_expr": _TYPE,
        "imports": ["Mathlib"],
        "source_module": _SOURCE_MODULE,
        "theorem_name": "FormalConjectures.Nat.add_zero_conj",
        "repo": {
            "remote": "https://github.com/google-deepmind/formal-conjectures",
            "commit": "0" * 40,
            "lean_version": "v4.30.0-rc2",
        },
        "references": ["textbook: Concrete Mathematics"],
    }
    record.update(overrides)
    return record


def _ingest(records: list[Any], **kwargs: Any):
    return ingest_formal_conjecture_records(
        records,
        source_license=kwargs.pop("source_license", "CC-BY-4.0"),
        mathlib_rev="fixture-mathlib-rev",
        **kwargs,
    )


def _only_reason(report: IngestReport) -> str:
    assert len(report.quarantined) == 1, report.quarantined
    return report.quarantined[0].reason


def test_clean_record_defaults_to_benchmark(tmp_path: Path) -> None:
    result = _ingest([_record()])

    assert result.report.accepted_count == 1
    task = result.tasks[0]
    assert task.id == "lemma.fc.fc-add-zero"
    assert task.task_format == "isolated_proof"
    assert task.task_class == "formal_conjecture"
    assert task.source_stream == "formal_conjectures"
    assert task.theorem_name == "target"
    assert task.imports == ("Mathlib",)
    assert task.allowed_files == ("Submission.lean",)
    assert _SOURCE_MODULE not in task.imports
    assert _SOURCE_MODULE not in task.allowed_imports
    # Benchmark by default: held out and unpaid.
    assert task.activation_status == "benchmark"
    assert task.metadata["held_out_benchmark"] is True
    assert task.source_value == "calibration"
    assert task.metadata["fc_category"] == "textbook"
    assert "import Mathlib" in task.statement
    assert "namespace Submission" in task.statement
    assert "theorem target" in task.statement

    env = result.report.environments[0]
    assert env.certified is True
    assert env.environment_sha256 is not None


def test_paid_opt_in_per_record() -> None:
    result = _ingest([_record(paid=True)])
    task = result.tasks[0]
    assert task.activation_status == "paid"
    assert task.source_value == "low"
    assert "held_out_benchmark" not in task.metadata


def test_paid_default_flag() -> None:
    result = _ingest([_record()], default_paid=True)
    assert result.tasks[0].activation_status == "paid"


def test_unsafe_category_is_quarantined() -> None:
    result = _ingest([_record(category="research_open")])
    assert _only_reason(result.report) == "unsafe_category"


def test_missing_type_expr_is_quarantined() -> None:
    record = _record()
    del record["type_expr"]
    result = _ingest([record])
    assert _only_reason(result.report) == "missing_type_expr"


def test_missing_license_is_quarantined() -> None:
    result = _ingest([_record()], source_license="  ")
    assert _only_reason(result.report) == "missing_license"


def test_unstable_toolchain_is_quarantined() -> None:
    result = _ingest([_record(lean_toolchain="leanprover/lean4:nightly-2025-01-01")])
    assert _only_reason(result.report) == "unstable_toolchain"


def test_exposes_original_is_quarantined() -> None:
    result = _ingest([_record(allowed_imports=["Mathlib", _SOURCE_MODULE])])
    assert _only_reason(result.report) == "exposes_original"


def test_requires_source_import_is_quarantined() -> None:
    result = _ingest([_record(requires_source_import=True)])
    assert _only_reason(result.report) == "requires_source_import"


def test_references_original_is_quarantined() -> None:
    result = _ingest([_record(type_expr="add_zero_conj 0 = 0")])
    assert _only_reason(result.report) == "references_original"


def test_duplicate_target_is_quarantined() -> None:
    records = [_record(), _record(id="fc-dup")]
    result = _ingest(records)
    assert result.report.accepted_count == 1
    assert _only_reason(result.report) == "duplicate_target"


def test_malformed_record_is_quarantined() -> None:
    result = _ingest(["garbage"])
    assert _only_reason(result.report) == "malformed_row"


def test_baseline_trivial_is_quarantined() -> None:
    def prober(_task: LemmaTask, _root: Any) -> BaselineProbe:
        return BaselineProbe(trivial=True, tactic="simp")

    result = _ingest([_record()], baseline_prober=prober)
    assert result.report.accepted_count == 0
    assert _only_reason(result.report) == "baseline_trivial"


def test_baseline_nontrivial_marks_candidate() -> None:
    def prober(_task: LemmaTask, _root: Any) -> BaselineProbe:
        return BaselineProbe(trivial=False)

    result = _ingest([_record()], baseline_prober=prober)
    assert result.report.accepted[0].baseline_status == "nontrivial"


def test_restated_task_rejects_importing_original_module(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _ingest([_record()]).tasks[0]

    def fake_verify(self: object, problem: object, submission_src: str, **kwargs: object) -> VerifyResult:  # noqa: ARG001
        raise AssertionError("sandbox should not run for original-module import attacks")

    monkeypatch.setattr("lemma.lean.verify_runner.LeanSandbox.verify", fake_verify)

    attack = "\n".join(
        [
            "import Mathlib",
            f"import {_SOURCE_MODULE}",
            "",
            "namespace Submission",
            "",
            f"theorem target : {_TYPE} := by",
            "  exact FormalConjectures.Nat.add_zero_conj",
            "",
            "end Submission",
            "",
        ]
    )
    result = run_lean_verify(
        LemmaSettings(_env_file=None, lean_use_docker=False),
        verify_timeout_s=60,
        problem=task.to_problem(),
        proof_script=attack,
        submission_policy=task.policy,
    )

    assert result.passed is False
    assert result.reason == "forbidden_import"


def test_restated_task_shape_passes_policy_to_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _ingest([_record()]).tasks[0]
    seen: dict[str, str] = {}

    def fake_verify(self: object, problem: object, submission_src: str, **kwargs: object) -> VerifyResult:  # noqa: ARG001
        seen["src"] = submission_src
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.verify_runner.LeanSandbox.verify", fake_verify)

    proof = task.submission_stub.replace("  sorry", "  simp")
    result = run_lean_verify(
        LemmaSettings(_env_file=None, lean_use_docker=False),
        verify_timeout_s=60,
        problem=task.to_problem(),
        proof_script=proof,
        submission_policy=task.policy,
    )

    assert result.passed is True
    assert "simp" in seen["src"]


def test_ingest_formal_conjectures_cli_writes_registry_and_report(tmp_path: Path) -> None:
    records = [_record(), _record(id="bad", category="research_open")]
    records_path = tmp_path / "records.json"
    registry_path = tmp_path / "registry.json"
    report_path = tmp_path / "report.json"
    records_path.write_text(json.dumps({"records": records}), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "ingest-formal-conjectures",
            "--records-json",
            str(records_path),
            "--source-license",
            "CC-BY-4.0",
            "--mathlib-rev",
            "fixture-mathlib-rev",
            "--output",
            str(registry_path),
            "--report",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["accepted"] == 1
    assert payload["quarantine_reasons"] == {"unsafe_category": 1}

    registry = load_task_registry(registry_path.read_bytes())
    assert [task.id for task in registry.tasks] == ["lemma.fc.fc-add-zero"]
    assert registry.tasks[0].activation_status == "benchmark"

    report = IngestReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.accepted_count == 1

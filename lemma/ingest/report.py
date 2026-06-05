"""Typed, deterministic reports for source ingestion (Phase 3).

The ingester never raises on a bad source row. Instead every row lands in one of
two buckets:

* ``accepted`` - a packaged :class:`IngestCandidate` ready to become a task.
* ``quarantined`` - a :class:`QuarantinedRow` carrying a structured
  :data:`QuarantineReason` so an operator can see exactly why a row was dropped.

Per-environment certification is recorded separately so two validators can agree
the pinned Lean toolchain is reproducible. All keys reference public provenance
(``repo@commit:path`` / source row id) and never local checkout paths.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lemma.tasks import LemmaTask

#: Why a source row was dropped instead of packaged. Layman: the rejection slip.
QuarantineReason = Literal[
    "malformed_row",  # row is not a well-formed source record
    "missing_theorem_name",  # no target declaration name supplied
    "missing_type_expr",  # no target type supplied
    "missing_license",  # no public license recorded for the source
    "checkout_missing",  # no local pinned checkout for the row's commit
    "source_file_missing",  # target file absent in the pinned checkout
    "no_target_hole",  # file has no sorry/admit gap to fill
    "extra_holes",  # file has more than one gap (ambiguous target)
    "decl_not_found",  # target declaration not present in the file
    "unsafe_path",  # source path escapes the repo / is absolute
    "not_lean4",  # source is not a Lean 4 project
    "unstable_toolchain",  # toolchain is not a pinned, reproducible release
    "missing_environment",  # no reproducible environment hash (no lake files)
    "duplicate_target",  # same target already accepted in this batch
    "baseline_trivial",  # a baseline one-line tactic already closes the gap
    "ingest_error",  # unexpected error while packaging the row
    # Formal Conjectures restatement filters:
    "unsafe_category",  # not an allowed v1 category (e.g. research-open)
    "requires_source_import",  # solving needs the original sorry-backed module
    "references_original",  # restated type still names the original declaration
    "exposes_original",  # allowed imports would expose the original declaration
]

#: Result of the baseline-tactic screen for an accepted candidate.
BaselineStatus = Literal["unscreened", "nontrivial", "trivial"]


class QuarantinedRow(BaseModel):
    """One source row that was dropped, with a structured reason."""

    model_config = ConfigDict(extra="forbid")

    row_ref: str
    reason: QuarantineReason
    detail: str = ""


class EnvironmentCertification(BaseModel):
    """Reproducibility certification for one unique ``repo@commit`` checkout."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    commit: str
    environment_sha256: str | None = None
    lean_toolchain: str = ""
    lake_manifest_present: bool = False
    certified: bool = False
    detail: str = ""


class IngestCandidate(BaseModel):
    """A packaged task candidate accepted from a source row."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    row_ref: str
    repo: str
    commit: str
    path: str
    theorem_name: str
    target_sha256: str
    target_type_sha256: str
    environment_sha256: str | None = None
    lean_toolchain: str = ""
    baseline_status: BaselineStatus = "unscreened"
    baseline_tactic: str = ""


class IngestReport(BaseModel):
    """Deterministic summary of one ingestion run for a single source."""

    model_config = ConfigDict(extra="forbid")

    source_stream: str = "sorrydb"
    total_rows: int = 0
    accepted: list[IngestCandidate] = Field(default_factory=list)
    quarantined: list[QuarantinedRow] = Field(default_factory=list)
    environments: list[EnvironmentCertification] = Field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantined)

    def reason_counts(self) -> dict[str, int]:
        """Return the count of quarantined rows per reason, sorted by reason."""
        counts = Counter(row.reason for row in self.quarantined)
        return {reason: counts[reason] for reason in sorted(counts)}

    def summary(self) -> dict[str, object]:
        """Return a compact, JSON-friendly summary of the run."""
        return {
            "source_stream": self.source_stream,
            "total_rows": self.total_rows,
            "accepted": self.accepted_count,
            "quarantined": self.quarantined_count,
            "quarantine_reasons": self.reason_counts(),
            "environments": len(self.environments),
            "certified_environments": sum(1 for env in self.environments if env.certified),
        }


@dataclass
class IngestResult:
    """Accepted tasks paired with the run's report (shared across sources)."""

    tasks: list[LemmaTask] = field(default_factory=list)
    report: IngestReport = field(default_factory=IngestReport)

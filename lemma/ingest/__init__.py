"""Source-ingestion tooling for real Lean proof tasks.

These modules turn public task sources (SorryDB rows, Formal Conjectures) into
packaged, reproducible task candidates. Every source row is either accepted as a
candidate or quarantined with a structured reason, and the pipeline emits
deterministic reports so two operators can reproduce the same task set from
public inputs alone.

No proof generation, model calls, or runtime mining happens here: this is pure
data tooling that prepares the task set the validator later verifies.
"""

from __future__ import annotations

from lemma.ingest.report import (
    EnvironmentCertification,
    IngestCandidate,
    IngestReport,
    IngestResult,
    QuarantinedRow,
    QuarantineReason,
)

__all__ = [
    "EnvironmentCertification",
    "IngestCandidate",
    "IngestReport",
    "IngestResult",
    "QuarantineReason",
    "QuarantinedRow",
]

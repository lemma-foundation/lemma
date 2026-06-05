"""Real-task Proof Atlas publishing layer.

The Proof Atlas is the public ledger and replay surface for real verified proof
work. This package builds the real-task artifacts on top of accepted proof rows
and the pinned task registry:

* task bundles - the pinned real task with full public source/environment
  metadata, so anyone can identify and reproduce the original work;
* solved ledger - which task was solved, by whom, with what proof identity, and
  how to apply the proof/patch back to the source;
* environment index - certified validation environments keyed by hash;
* sources index - source ingest reports, repo pins, and licenses.

It is pure data tooling: it reads public inputs and writes public artifacts. It
performs no upload, commit, or chain write.
"""

from __future__ import annotations

from lemma.atlas.realtask import (
    EnvironmentEntry,
    RealTaskSnapshot,
    SolvedEntry,
    TaskBundle,
    apply_instructions,
    build_env_index,
    build_real_task_snapshot,
    build_snapshot_from_paths,
    build_solved_ledger,
    build_task_bundle,
    build_task_bundles,
    load_atlas_registry_union,
    read_accepted_rows,
    read_source_reports,
    write_real_task_snapshot,
)

__all__ = [
    "EnvironmentEntry",
    "RealTaskSnapshot",
    "SolvedEntry",
    "TaskBundle",
    "apply_instructions",
    "build_env_index",
    "build_real_task_snapshot",
    "build_snapshot_from_paths",
    "build_solved_ledger",
    "build_task_bundle",
    "build_task_bundles",
    "load_atlas_registry_union",
    "read_accepted_rows",
    "read_source_reports",
    "write_real_task_snapshot",
]

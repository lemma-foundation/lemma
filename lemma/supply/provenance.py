"""Source and license provenance helpers for generated task candidates."""

from __future__ import annotations

from lemma.tasks import SourceRef


def mathlib_source(*, name: str, commit: str, path: str) -> SourceRef:
    return SourceRef(kind="mathlib", name=name, commit=commit, path=path)


def fixture_source(name: str) -> SourceRef:
    return SourceRef(kind="fixture", name=name)

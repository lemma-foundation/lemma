"""Baseline-tactic screening for ingested task candidates.

A task is only worth paying miners for if it is *not* closed by a trivial
one-line tactic. This module screens a candidate by trying each baseline tactic
in place of its single ``sorry``/``admit`` gap and checking whether the project
still verifies. Anything a baseline tactic closes is quarantined as
``baseline_trivial``.

Screening is *pluggable*: the orchestrator accepts any callable matching
:class:`BaselineProber`. The default :class:`PreflightBaselineProber` reuses the
Phase 2 preflight path (so screening uses the exact verifier validators use) but
requires a working Lean toolchain. Tests inject a lightweight fake prober.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lemma.submissions import build_patch_submission, build_submission
from lemma.tasks import LemmaTask

#: Trivial tactics tried against each gap, cheapest first.
DEFAULT_BASELINE_TACTICS: tuple[str, ...] = (
    "rfl",
    "trivial",
    "decide",
    "simp",
    "norm_num",
    "omega",
    "aesop",
)

_HOLE_RE = re.compile(r"\b(sorry|admit)\b")


@dataclass(frozen=True)
class BaselineProbe:
    """Outcome of screening one candidate against the baseline tactics."""

    trivial: bool
    tactic: str = ""


class BaselineProber(Protocol):
    """Screen one candidate; return whether a baseline tactic closes the gap.

    ``source_root`` is the pinned checkout for patch tasks, or ``None`` for
    isolated-proof tasks that carry their environment by pin instead.
    """

    def __call__(self, task: LemmaTask, source_root: Path | None) -> BaselineProbe: ...


def build_hole_replacement_patch(rel_path: str, source_text: str, tactic: str) -> str | None:
    """Return a zero-context unified diff that swaps the first gap for ``tactic``.

    Returns ``None`` when the file has no ``sorry``/``admit`` gap on a code line.
    The patch targets the patch-task applier (``git apply --unidiff-zero``).
    """
    original_lines = source_text.splitlines(keepends=True)
    target_index: int | None = None
    for index, raw in enumerate(original_lines):
        code = raw.split("--", 1)[0]
        if _HOLE_RE.search(code):
            target_index = index
            break
    if target_index is None:
        return None

    new_line = _HOLE_RE.sub(tactic, original_lines[target_index], count=1)
    if new_line == original_lines[target_index]:
        return None
    patched_lines = list(original_lines)
    patched_lines[target_index] = new_line

    diff = difflib.unified_diff(
        original_lines,
        patched_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        n=0,
    )
    patch = "".join(diff)
    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch or None


@dataclass
class PreflightBaselineProber:
    """Real prober that verifies baseline tactics through the preflight path.

    Requires a working Lean toolchain in the checkout; intended for operator
    ingestion runs, not unit tests.
    """

    tactics: tuple[str, ...] = DEFAULT_BASELINE_TACTICS
    timeout_s: int = 120

    def __call__(self, task: LemmaTask, source_root: Path) -> BaselineProbe:
        from lemma.common.config import LemmaSettings
        from lemma.preflight import preflight_submission

        rel_path = task.allowed_files[0] if task.allowed_files else ""
        target = source_root / rel_path
        if not rel_path or not target.is_file():
            return BaselineProbe(trivial=False)
        source_text = target.read_text(encoding="utf-8")
        settings = LemmaSettings()
        for tactic in self.tactics:
            patch = build_hole_replacement_patch(rel_path, source_text, tactic)
            if patch is None:
                continue
            submission = build_patch_submission(task, solver_hotkey="baseline-screen", patch_text=patch)
            verdict = preflight_submission(
                task,
                submission,
                settings=settings,
                source_root=source_root,
                run_reproduction=True,
                timeout_s=self.timeout_s,
            )
            if verdict.accepted:
                return BaselineProbe(trivial=True, tactic=tactic)
        return BaselineProbe(trivial=False)


@dataclass
class IsolatedProofBaselineProber:
    """Real prober for isolated-proof tasks (e.g. Formal Conjectures).

    Tries each baseline tactic as the proof body of the task's submission stub.
    Requires a working Lean toolchain; intended for operator ingestion runs.
    """

    tactics: tuple[str, ...] = DEFAULT_BASELINE_TACTICS
    timeout_s: int = 120

    def __call__(self, task: LemmaTask, source_root: Path | None = None) -> BaselineProbe:
        from lemma.common.config import LemmaSettings
        from lemma.preflight import preflight_submission

        if not _HOLE_RE.search(task.submission_stub):
            return BaselineProbe(trivial=False)
        settings = LemmaSettings()
        for tactic in self.tactics:
            proof_script = _HOLE_RE.sub(tactic, task.submission_stub, count=1)
            submission = build_submission(task, solver_hotkey="baseline-screen", proof_script=proof_script)
            verdict = preflight_submission(
                task,
                submission,
                settings=settings,
                source_root=source_root,
                run_reproduction=True,
                timeout_s=self.timeout_s,
            )
            if verdict.accepted:
                return BaselineProbe(trivial=True, tactic=tactic)
        return BaselineProbe(trivial=False)

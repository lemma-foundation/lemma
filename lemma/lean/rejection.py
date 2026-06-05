"""Canonical miner-facing rejection vocabulary for Lean task validation.

Every validator and the miner ``preflight`` command speak the same structured
verdict language. There is exactly one accepted class (``ok``) and the thirteen
reject classes from the real-task pivot roadmap. Internal validation code
classifies failures into these classes directly; the human-readable specifics
travel in ``detail`` / ``stderr_tail`` so the coarse class stays stable and
reproducible across two validators reading the same public inputs.
"""

from __future__ import annotations

import re
from typing import Final, Literal, get_args

RejectionClass = Literal[
    "ok",
    "patch_apply_failed",
    "target_type_changed",
    "new_sorry_detected",
    "new_admit_detected",
    "new_axiom_detected",
    "forbidden_import",
    "lean_compile_error",
    "timeout",
    "memory_limit",
    "project_already_broken",
    "duplicate_solution",
    "task_already_solved",
    "validator_internal_error",
]

#: The thirteen reject classes, in roadmap order. ``ok`` is excluded.
MINER_FACING_REJECTION_CLASSES: Final[tuple[RejectionClass, ...]] = tuple(
    cls for cls in get_args(RejectionClass) if cls != "ok"
)

#: Reasons caused by the validator environment or non-deterministic infra, not
#: by the miner's submission. Callers should treat these as retryable rather
#: than scoring them against the miner.
INFRA_REJECTION_CLASSES: Final[frozenset[RejectionClass]] = frozenset(
    {"timeout", "memory_limit", "validator_internal_error"}
)

#: Plain-language explanation for each verdict class. These are the public,
#: miner-facing descriptions surfaced by ``preflight`` and validator feedback,
#: so the coarse class is understandable without reading validator internals.
REJECTION_DESCRIPTIONS: Final[dict[RejectionClass, str]] = {
    "ok": "Accepted. The submission verified against the pinned task environment.",
    "patch_apply_failed": "The patch did not apply cleanly to the pinned source checkout.",
    "target_type_changed": "The target declaration's type no longer matches the task statement.",
    "new_sorry_detected": "The submission left or introduced a `sorry` placeholder.",
    "new_admit_detected": "The submission left or introduced an `admit` placeholder.",
    "new_axiom_detected": "The submission introduced a new axiom or unproven assumption.",
    "forbidden_import": "The submission imported a module outside the allowed set.",
    "lean_compile_error": "Lean failed to compile the submission.",
    "timeout": "Verification exceeded the time limit (retryable infra reason).",
    "memory_limit": "Verification exceeded the memory limit (retryable infra reason).",
    "project_already_broken": "The pinned project failed to build before the submission was applied.",
    "duplicate_solution": "An identical accepted proof was already submitted for this task.",
    "task_already_solved": "This task was already solved in an earlier settlement window.",
    "validator_internal_error": "The validator hit an internal error (retryable infra reason).",
}


def describe_rejection(reason: RejectionClass) -> str:
    """Return the public, plain-language explanation for a verdict class."""
    return REJECTION_DESCRIPTIONS[reason]

_SORRY_RE = re.compile(r"\bsorry\b")
_ADMIT_RE = re.compile(r"\badmit\b")


def hole_class(code_text: str) -> Literal["new_sorry_detected", "new_admit_detected"] | None:
    """Classify a residual proof hole as a ``sorry`` or ``admit`` rejection.

    ``code_text`` must already be comment-stripped Lean source. ``sorry`` takes
    precedence when both appear so the verdict is deterministic.
    """
    if _SORRY_RE.search(code_text):
        return "new_sorry_detected"
    if _ADMIT_RE.search(code_text):
        return "new_admit_detected"
    return None

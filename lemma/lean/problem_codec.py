"""JSON-safe serialization for :class:`~lemma.problems.base.Problem` (remote verify transport)."""

from __future__ import annotations

from typing import Any

from lemma.problems.base import Problem


def problem_to_payload(problem: Problem) -> dict[str, Any]:
    """Serialize a Problem for HTTP JSON bodies."""
    return {
        "id": problem.id,
        "theorem_name": problem.theorem_name,
        "type_expr": problem.type_expr,
        "split": problem.split,
        "lean_toolchain": problem.lean_toolchain,
        "mathlib_rev": problem.mathlib_rev,
        "imports": list(problem.imports),
        "extra": dict(problem.extra),
    }


def problem_from_payload(data: dict[str, Any]) -> Problem:
    """Restore a Problem from :func:`problem_to_payload`."""
    imps = data.get("imports", ["Mathlib"])
    if not isinstance(imps, list):
        raise ValueError("imports must be a list of strings")
    extra = data.get("extra")
    if extra is None:
        extra = {}
    if not isinstance(extra, dict):
        raise ValueError("extra must be an object")
    return Problem(
        id=str(data["id"]),
        theorem_name=str(data["theorem_name"]),
        type_expr=str(data["type_expr"]),
        split=str(data["split"]),
        lean_toolchain=str(data["lean_toolchain"]),
        mathlib_rev=str(data["mathlib_rev"]),
        imports=tuple(str(x) for x in imps),
        extra=dict(extra) if isinstance(extra, dict) else {},
    )

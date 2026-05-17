"""Allowlist policy for claimant-owned ``Submission.lean`` source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from lemma.problems.base import Problem

SubmissionPolicy = Literal["strict_envelope", "restricted_helpers"]
VALID_SUBMISSION_POLICIES: frozenset[str] = frozenset({"strict_envelope", "restricted_helpers"})

_DANGEROUS_TOKENS = re.compile(r"\b(sorry|admit|native_decide|unsafeCast|reduceBool)\b")
_FORBIDDEN_PREFIXES = (
    "@[",
    "attribute ",
    "axiom ",
    "constant ",
    "unsafe ",
    "extern ",
    "implemented_by ",
    "set_option ",
    "macro ",
    "syntax ",
    "elab ",
    "notation ",
    "local notation ",
    "scoped notation ",
    "open ",
    "open scoped ",
    "run_cmd ",
    "initialize ",
    "builtin_initialize ",
    "inductive ",
    "structure ",
    "class ",
    "instance ",
    "abbrev ",
    "opaque ",
)
_DECL_RE = re.compile(r"^(theorem|lemma|def)\s+([A-Za-z_][A-Za-z0-9_']*)\b")
_AXIOM_DECL_RE = re.compile(r"^(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)\b")


@dataclass(frozen=True)
class SubmissionPolicyScan:
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class _Line:
    no: int
    raw: str
    code: str

    @property
    def top_level(self) -> bool:
        return self.raw == self.raw.lstrip()


def submission_policy_for_problem(problem: Problem, policy: str | None = None) -> SubmissionPolicy:
    """Return the explicit policy, problem metadata policy, or split default."""
    candidate = (policy or problem.extra.get("submission_policy") or "").strip()
    if not candidate:
        candidate = "strict_envelope"
    if candidate not in VALID_SUBMISSION_POLICIES:
        raise ValueError(f"unknown submission policy: {candidate}")
    return candidate  # type: ignore[return-value]


def submission_policy_stderr_tail(scan: SubmissionPolicyScan, *, max_len: int = 8000) -> str:
    if scan.ok:
        return ""
    return f"submission policy violation: {scan.reason or 'rejected'}"[:max_len]


def scan_submission_policy(
    problem: Problem,
    source: str,
    *,
    policy: str | None = None,
) -> SubmissionPolicyScan:
    """Fail closed unless ``source`` matches the selected allowlist shape."""
    try:
        selected = submission_policy_for_problem(problem, policy)
    except ValueError as e:
        return SubmissionPolicyScan(False, str(e))

    lines = _code_lines(source)
    if lines is None:
        return SubmissionPolicyScan(False, "block comments are not allowed")
    if not lines:
        return SubmissionPolicyScan(False, "empty Submission.lean")

    dangerous = _dangerous_construct(lines)
    if dangerous:
        return SubmissionPolicyScan(False, dangerous)

    imports = [f"import {m}" for m in problem.imports]
    actual_imports = [line.code for line in lines if line.code.startswith("import ")]
    if actual_imports != imports:
        return SubmissionPolicyScan(False, f"imports must be exactly {imports}")

    try:
        first_body = len(imports)
        if lines[first_body].code != "namespace Submission":
            return SubmissionPolicyScan(False, "expected `namespace Submission` after imports")
        if lines[-1].code != "end Submission":
            return SubmissionPolicyScan(False, "expected final `end Submission`")
    except IndexError:
        return SubmissionPolicyScan(False, "incomplete Submission namespace")

    body = lines[first_body + 1 : -1]
    if not body:
        return SubmissionPolicyScan(False, "Submission namespace has no theorem")

    if selected == "strict_envelope":
        return _scan_strict(problem, body)
    return _scan_restricted_helpers(problem, body)


def submission_axiom_check_names(
    problem: Problem,
    source: str,
    *,
    policy: str | None = None,
) -> list[str]:
    """Names in ``Submission`` whose axiom dependencies should be audited."""
    selected = submission_policy_for_problem(problem, policy)
    if selected == "strict_envelope":
        return [problem.theorem_name]

    names: list[str] = []
    seen: set[str] = set()
    for line in _code_lines(source) or []:
        if not line.top_level:
            continue
        m = _AXIOM_DECL_RE.match(line.code)
        if m and m.group(2) not in seen:
            names.append(m.group(2))
            seen.add(m.group(2))
    if problem.theorem_name not in seen:
        names.append(problem.theorem_name)
    return names


def _code_lines(source: str) -> list[_Line] | None:
    if "/-" in source or "-/" in source:
        return None
    out: list[_Line] = []
    for no, raw in enumerate(source.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        code = raw.split("--", 1)[0].rstrip()
        if not code.strip():
            continue
        out.append(_Line(no=no, raw=code, code=code.strip()))
    return out


def _dangerous_construct(lines: list[_Line]) -> str | None:
    for line in lines:
        if _DANGEROUS_TOKENS.search(line.code):
            return f"line {line.no}: forbidden token"
        for prefix in _FORBIDDEN_PREFIXES:
            if line.code.startswith(prefix):
                return f"line {line.no}: `{prefix.strip()}` is not allowed"
    return None


def _target_decl(problem: Problem) -> str:
    return f"theorem {problem.theorem_name} : {problem.type_expr} := by"


def _top_level_target_indexes(problem: Problem, body: list[_Line]) -> list[int]:
    decl = _target_decl(problem)
    return [i for i, line in enumerate(body) if line.top_level and line.code.startswith(decl)]


def _scan_strict(problem: Problem, body: list[_Line]) -> SubmissionPolicyScan:
    target_indexes = _top_level_target_indexes(problem, body)
    if len(target_indexes) != 1:
        return SubmissionPolicyScan(False, "expected exactly one exact target theorem")
    if target_indexes[0] != 0:
        return SubmissionPolicyScan(False, "target theorem must be the only top-level declaration")
    for line in body[1:]:
        if line.top_level:
            return SubmissionPolicyScan(False, f"line {line.no}: extra top-level command")
    return SubmissionPolicyScan(True)


def _scan_restricted_helpers(problem: Problem, body: list[_Line]) -> SubmissionPolicyScan:
    if len(_top_level_target_indexes(problem, body)) != 1:
        return SubmissionPolicyScan(False, "expected exactly one exact target theorem")
    for line in body:
        if not line.top_level:
            continue
        if line.code in {"section", "end"} or line.code.startswith(("section ", "end ", "variable ")):
            continue
        if _DECL_RE.match(line.code):
            continue
        return SubmissionPolicyScan(False, f"line {line.no}: top-level command is not allowlisted")
    return SubmissionPolicyScan(True)

"""Allowlist policy for claimant-owned ``Submission.lean`` source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from lemma.lean.rejection import RejectionClass
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
_LEAN_DECL_NAME = r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*"
_DECL_RE = re.compile(rf"^(theorem|lemma|def)\s+({_LEAN_DECL_NAME})\b")
_AXIOM_DECL_RE = re.compile(rf"^(theorem|lemma)\s+({_LEAN_DECL_NAME})\b")


#: Trust-relevant forbidden prefixes that count as an added assumption/axiom.
_TRUST_PREFIXES = (
    "@[",
    "attribute ",
    "axiom ",
    "constant ",
    "unsafe ",
    "extern ",
    "implemented_by ",
    "set_option ",
    "opaque ",
)
#: Tokens that smuggle in native/unsafe trust (treated as added-axiom trust).
_TRUST_TOKENS = ("native_decide", "unsafeCast", "reduceBool")


@dataclass(frozen=True)
class SubmissionPolicyScan:
    ok: bool
    reason: str | None = None
    reason_class: RejectionClass | None = None


def submission_policy_reason_class(scan: SubmissionPolicyScan) -> RejectionClass:
    """Map a failed submission-policy scan to a canonical rejection class.

    Structural envelope violations that are neither holes, added trust, nor
    import problems are reported as ``lean_compile_error``: from the miner's
    side the fix is the same (produce a clean, compiling, well-formed proof of
    the exact target), and the human-readable specifics travel in ``reason``.
    """
    if scan.ok:
        return "ok"
    return scan.reason_class or "lean_compile_error"


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
        return SubmissionPolicyScan(False, str(e), "validator_internal_error")

    lines = _code_lines(source)
    if lines is None:
        return SubmissionPolicyScan(False, "block comments are not allowed", "lean_compile_error")
    if not lines:
        return SubmissionPolicyScan(False, "empty Submission.lean", "lean_compile_error")

    dangerous = _dangerous_construct(lines)
    if dangerous:
        return SubmissionPolicyScan(False, dangerous[0], dangerous[1])

    imports = [f"import {m}" for m in problem.imports]
    actual_imports = [line.code for line in lines if line.code.startswith("import ")]
    if actual_imports != imports:
        return SubmissionPolicyScan(False, f"imports must be exactly {imports}", "forbidden_import")

    try:
        first_body = len(imports)
        if lines[first_body].code != "namespace Submission":
            return SubmissionPolicyScan(False, "expected `namespace Submission` after imports", "lean_compile_error")
        if lines[-1].code != "end Submission":
            return SubmissionPolicyScan(False, "expected final `end Submission`", "lean_compile_error")
    except IndexError:
        return SubmissionPolicyScan(False, "incomplete Submission namespace", "lean_compile_error")

    body = lines[first_body + 1 : -1]
    if not body:
        return SubmissionPolicyScan(False, "Submission namespace has no theorem", "lean_compile_error")

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


def _dangerous_construct(lines: list[_Line]) -> tuple[str, RejectionClass] | None:
    for line in lines:
        token = _DANGEROUS_TOKENS.search(line.code)
        if token:
            return f"line {line.no}: forbidden token `{token.group(0)}`", _token_reason_class(token.group(0))
        for prefix in _FORBIDDEN_PREFIXES:
            if line.code.startswith(prefix):
                message = f"line {line.no}: `{prefix.strip()}` is not allowed"
                reason_class: RejectionClass = (
                    "new_axiom_detected" if prefix in _TRUST_PREFIXES else "lean_compile_error"
                )
                return message, reason_class
    return None


def _token_reason_class(token: str) -> RejectionClass:
    if token == "sorry":
        return "new_sorry_detected"
    if token == "admit":
        return "new_admit_detected"
    return "new_axiom_detected"


def _target_decl(problem: Problem) -> str:
    return f"theorem {problem.theorem_name} : {problem.type_expr} := by"


def _norm_decl(text: str) -> str:
    return " ".join(text.split())


def _top_level_target_indexes(problem: Problem, body: list[_Line]) -> list[int]:
    decl = _norm_decl(_target_decl(problem))
    out: list[int] = []
    prefix = f"theorem {problem.theorem_name} :"
    for i, line in enumerate(body):
        if not line.top_level or not line.code.startswith(prefix):
            continue
        parts: list[str] = []
        for candidate in body[i:]:
            if candidate is not line and candidate.top_level:
                break
            parts.append(candidate.code)
            if ":= by" in candidate.code:
                break
        if _norm_decl(" ".join(parts)).startswith(decl):
            out.append(i)
    return out


def _target_decl_end_index(start: int, body: list[_Line]) -> int:
    for i in range(start, len(body)):
        if ":= by" in body[i].code:
            return i
    return start


def _scan_strict(problem: Problem, body: list[_Line]) -> SubmissionPolicyScan:
    target_indexes = _top_level_target_indexes(problem, body)
    if len(target_indexes) != 1:
        return SubmissionPolicyScan(False, "expected exactly one exact target theorem", "lean_compile_error")
    if target_indexes[0] != 0:
        return SubmissionPolicyScan(
            False, "target theorem must be the only top-level declaration", "lean_compile_error"
        )
    target_end = _target_decl_end_index(target_indexes[0], body)
    for line in body[target_end + 1 :]:
        if line.top_level:
            return SubmissionPolicyScan(False, f"line {line.no}: extra top-level command", "lean_compile_error")
    return SubmissionPolicyScan(True)


def _scan_restricted_helpers(problem: Problem, body: list[_Line]) -> SubmissionPolicyScan:
    if len(_top_level_target_indexes(problem, body)) != 1:
        return SubmissionPolicyScan(False, "expected exactly one exact target theorem", "lean_compile_error")
    for line in body:
        if not line.top_level:
            continue
        if line.code in {"section", "end"} or line.code.startswith(("section ", "end ", "variable ")):
            continue
        if _DECL_RE.match(line.code):
            continue
        return SubmissionPolicyScan(
            False, f"line {line.no}: top-level command is not allowlisted", "lean_compile_error"
        )
    return SubmissionPolicyScan(True)

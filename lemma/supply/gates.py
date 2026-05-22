"""Generation-time gates for procedural Lean tasks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyReason, VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.problems.base import Problem
from lemma.supply.import_graph import ImportGraph, empty_import_graph
from lemma.supply.novelty import NoveltyCache, empty_novelty_cache
from lemma.supply.slot_weight import slot_weight_receipt_for_candidate
from lemma.supply.triviality_budget import (
    TrivialityBudgetReceipt,
    static_triviality_budget_receipt,
)
from lemma.supply.types import TaskCandidate

GATE_VERSION = "lemma-procedural-gates-v3"
TRIVIALITY_STACK = (
    ("trivial", "  trivial"),
    ("simp", "  simp"),
    ("simp_all", "  simp_all"),
    ("omega", "  omega"),
    ("norm_num", "  norm_num"),
    ("aesop", "  aesop"),
)
_INFRA_FAILURES: frozenset[VerifyReason] = frozenset({"timeout", "oom", "docker_error", "remote_error"})


@dataclass(frozen=True)
class ProceduralGateVerdict:
    typechecked: bool
    prop_gate_passed: bool
    triviality_checked: bool
    baseline_solved: bool
    novelty_status: str
    slot_weight: float
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return (
            self.typechecked
            and self.prop_gate_passed
            and self.triviality_checked
            and not self.baseline_solved
            and self.novelty_status == "passed"
        )


class ProceduralGateRunner(Protocol):
    def __call__(
        self,
        candidate: TaskCandidate,
        *,
        seen_canonical_hashes: Iterable[str],
    ) -> ProceduralGateVerdict:
        """Run the procedural gates for one candidate."""


class AssumedProceduralGateRunner:
    """Fast dev-only gate runner for non-production candidate previews."""

    def __init__(
        self,
        *,
        novelty_cache: NoveltyCache | None = None,
        import_graph: ImportGraph | None = None,
    ) -> None:
        self.novelty_cache = novelty_cache or empty_novelty_cache()
        self.import_graph = import_graph or empty_import_graph()

    def __call__(
        self,
        candidate: TaskCandidate,
        *,
        seen_canonical_hashes: Iterable[str],
    ) -> ProceduralGateVerdict:
        novelty = _novelty_status(candidate, seen_canonical_hashes, self.novelty_cache)
        slot_weight = slot_weight_receipt_for_candidate(candidate, import_graph=self.import_graph)
        budget = static_triviality_budget_receipt(1)
        return ProceduralGateVerdict(
            typechecked=True,
            prop_gate_passed=True,
            triviality_checked=True,
            baseline_solved=False,
            novelty_status=novelty,
            slot_weight=slot_weight.weight,
            metadata={
                "gate_runner": "assumed",
                "triviality_stack": [name for name, _body in TRIVIALITY_STACK],
                **self.novelty_cache.metadata(),
                **budget.metadata(),
                **slot_weight.metadata(),
            },
        )


class LeanProceduralGateRunner:
    """Lean-backed implementation of the four paid-production gates."""

    def __init__(
        self,
        settings: LemmaSettings,
        *,
        triviality_budget_receipt: TrivialityBudgetReceipt | None = None,
        novelty_cache: NoveltyCache | None = None,
        import_graph: ImportGraph | None = None,
    ) -> None:
        self.settings = settings
        self.gate_timeout_s = min(settings.lean_verify_timeout_s, settings.procedural_gate_timeout_s)
        self.triviality_budget_receipt = triviality_budget_receipt or static_triviality_budget_receipt(
            min(settings.lean_verify_timeout_s, settings.procedural_triviality_budget_s)
        )
        self.triviality_budget_s = self.triviality_budget_receipt.budget_s
        self.novelty_cache = novelty_cache or empty_novelty_cache()
        self.import_graph = import_graph or empty_import_graph()

    def __call__(
        self,
        candidate: TaskCandidate,
        *,
        seen_canonical_hashes: Iterable[str],
    ) -> ProceduralGateVerdict:
        novelty = _novelty_status(candidate, seen_canonical_hashes, self.novelty_cache)
        typecheck = self._compile_gate(candidate, _typecheck_gate_source(candidate))
        prop = self._compile_gate(candidate, _prop_gate_source(candidate)) if typecheck.passed else typecheck
        triviality_checked, baseline_solved, baseline_solver, baseline_reason = self._run_triviality_stack(candidate)
        slot_weight = slot_weight_receipt_for_candidate(candidate, import_graph=self.import_graph)
        return ProceduralGateVerdict(
            typechecked=typecheck.passed,
            prop_gate_passed=prop.passed,
            triviality_checked=triviality_checked,
            baseline_solved=baseline_solved,
            novelty_status=novelty,
            slot_weight=slot_weight.weight,
            metadata={
                "gate_runner": "lean",
                "typecheck_reason": typecheck.reason,
                "prop_gate_reason": prop.reason,
                "triviality_stack": [name for name, _body in TRIVIALITY_STACK],
                "triviality_reason": baseline_reason,
                "baseline_solver": baseline_solver,
                **self.novelty_cache.metadata(),
                **self.triviality_budget_receipt.metadata(),
                **slot_weight.metadata(),
            },
        )

    def _compile_gate(self, candidate: TaskCandidate, gate_source: str) -> VerifyResult:
        problem = _gate_problem(candidate, gate_source)
        return run_lean_verify(
            self.settings,
            verify_timeout_s=self.gate_timeout_s,
            problem=problem,
            proof_script=_dummy_submission(candidate),
            submission_policy="strict_envelope",
        )

    def _run_triviality_stack(self, candidate: TaskCandidate) -> tuple[bool, bool, str | None, str]:
        for name, body in TRIVIALITY_STACK:
            result = run_lean_verify(
                self.settings,
                verify_timeout_s=self.triviality_budget_s,
                problem=candidate.to_task().to_problem(),
                proof_script=_candidate_submission(candidate, body),
                submission_policy=candidate.policy,
            )
            if result.passed:
                return True, True, name, "baseline_solved"
            if result.reason in _INFRA_FAILURES:
                return False, False, None, result.reason
        return True, False, None, "baseline_failed"


def _novelty_status(candidate: TaskCandidate, seen_hashes: Iterable[str], novelty_cache: NoveltyCache) -> str:
    statement_hash = str(candidate.metadata.get("statement_hash") or "")
    canonical_hash = str(candidate.metadata.get("canonical_hash") or "")
    seen = set(seen_hashes)
    if statement_hash in seen or canonical_hash in seen:
        return "duplicate"
    return "duplicate" if novelty_cache.contains(statement_hash) else "passed"


def _gate_problem(candidate: TaskCandidate, gate_source: str) -> Problem:
    return Problem(
        id=f"{candidate.id}.gate",
        theorem_name="lemma_gate_dummy",
        type_expr="True",
        split="procedural_gate",
        lean_toolchain=candidate.lean_toolchain,
        mathlib_rev=candidate.mathlib_rev,
        imports=candidate.imports,
        extra={
            "challenge_full": gate_source,
            "submission_policy": "strict_envelope",
        },
    )


def _typecheck_gate_source(candidate: TaskCandidate) -> str:
    return "\n".join(
        [
            "namespace LemmaProceduralGate",
            "",
            f"def typecheck_gate : ({candidate.type_expr}) := by",
            "  sorry",
            "",
            "end LemmaProceduralGate",
        ]
    )


def _prop_gate_source(candidate: TaskCandidate) -> str:
    return "\n".join(
        [
            "namespace LemmaProceduralGate",
            "",
            f"theorem prop_gate : ({candidate.type_expr}) := by",
            "  sorry",
            "",
            "end LemmaProceduralGate",
        ]
    )


def _dummy_submission(candidate: TaskCandidate) -> str:
    return "\n".join(
        [
            *(f"import {module}" for module in candidate.imports),
            "",
            "namespace Submission",
            "",
            "theorem lemma_gate_dummy : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def _candidate_submission(candidate: TaskCandidate, body: str) -> str:
    return "\n".join(
        [
            *(f"import {module}" for module in candidate.imports),
            "",
            "namespace Submission",
            "",
            f"theorem {candidate.theorem_name} : {candidate.type_expr} := by",
            body,
            "",
            "end Submission",
            "",
        ]
    )

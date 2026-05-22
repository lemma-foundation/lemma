"""Generation-time gates for procedural Lean tasks."""

from __future__ import annotations

import hashlib
import json
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
_TYPECHECK_GATE_DECL = "LemmaProceduralGate.typecheck_gate"
_PROP_GATE_DECL = "LemmaProceduralGate.prop_gate"
_KERNEL_NORMAL_MARKER = "LEMMA_KERNEL_NORMAL_FORM "
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
        typecheck = self._compile_gate(
            candidate,
            _typecheck_gate_source(candidate),
            fingerprint_name=_TYPECHECK_GATE_DECL,
        )
        prop = (
            self._compile_gate(
                candidate,
                _prop_gate_source(candidate),
                fingerprint_name=_PROP_GATE_DECL,
                eval_commands=("#eval! LemmaProceduralGate.emit_kernel_normal",),
            )
            if typecheck.passed
            else typecheck
        )
        kernel_hash = _kernel_normal_hash(prop) if prop.passed else ""
        novelty = (
            _novelty_status(candidate, seen_canonical_hashes, self.novelty_cache, canonical_hash=kernel_hash)
            if kernel_hash
            else "missing_kernel_fingerprint"
        )
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
                "kernel_canonical_hash": kernel_hash,
                "kernel_canonical_name": "LemmaProceduralGate.kernel_normal_form",
                "canonical_hash": kernel_hash or candidate.metadata.get("canonical_hash"),
                "triviality_stack": [name for name, _body in TRIVIALITY_STACK],
                "triviality_reason": baseline_reason,
                "baseline_solver": baseline_solver,
                **self.novelty_cache.metadata(),
                **self.triviality_budget_receipt.metadata(),
                **slot_weight.metadata(),
            },
        )

    def _compile_gate(
        self,
        candidate: TaskCandidate,
        gate_source: str,
        *,
        fingerprint_name: str,
        eval_commands: tuple[str, ...] = (),
    ) -> VerifyResult:
        problem = _gate_problem(candidate, gate_source, fingerprint_name=fingerprint_name, eval_commands=eval_commands)
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


def _novelty_status(
    candidate: TaskCandidate,
    seen_hashes: Iterable[str],
    novelty_cache: NoveltyCache,
    *,
    canonical_hash: str | None = None,
) -> str:
    statement_hash = str(candidate.metadata.get("statement_hash") or "")
    canonical = str(canonical_hash or candidate.metadata.get("canonical_hash") or "")
    seen = set(seen_hashes)
    if canonical_hash is not None:
        if canonical in seen:
            return "duplicate"
        return "duplicate" if novelty_cache.contains(canonical) else "passed"
    if statement_hash in seen or canonical in seen:
        return "duplicate"
    return "duplicate" if novelty_cache.contains(statement_hash) or novelty_cache.contains(canonical) else "passed"


def _gate_problem(
    candidate: TaskCandidate,
    gate_source: str,
    *,
    fingerprint_name: str,
    eval_commands: tuple[str, ...] = (),
) -> Problem:
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
            "lean_build_target": "Challenge",
            "lean_fingerprint_names": (fingerprint_name,),
            "lean_eval_commands": eval_commands,
            "submission_policy": "strict_envelope",
        },
    )


def _kernel_normal_hash(result: VerifyResult) -> str:
    for line in (result.stdout_tail + "\n" + result.stderr_tail).splitlines():
        if line.startswith(_KERNEL_NORMAL_MARKER):
            payload = line.removeprefix(_KERNEL_NORMAL_MARKER).strip()
            if payload:
                return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return ""


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
            "import Lean",
            "",
            "open Lean",
            "",
            "namespace LemmaProceduralGate",
            "",
            f"def canonicalSource : String := {_json_string(candidate.type_expr)}",
            "",
            "def parseTermOrThrow (source : String) : Elab.Command.CommandElabM (TSyntax `term) := do",
            "  let env ← getEnv",
            "  match Parser.runParserCategory env `term source with",
            "  | Except.ok stx => pure ⟨stx⟩",
            "  | Except.error e => throwError e",
            "",
            "def binderInfoKey : BinderInfo → String",
            "  | BinderInfo.default => \"default\"",
            "  | BinderInfo.implicit => \"implicit\"",
            "  | BinderInfo.strictImplicit => \"strictImplicit\"",
            "  | BinderInfo.instImplicit => \"instImplicit\"",
            "",
            "def literalKey : Literal → String",
            "  | Literal.natVal n => \"nat:\" ++ toString n",
            "  | Literal.strVal value => \"str:\" ++ reprStr value",
            "",
            "partial def exprKey : Expr → String",
            "  | Expr.bvar i => \"bvar:\" ++ toString i",
            "  | Expr.fvar id => \"fvar:\" ++ toString id.name",
            "  | Expr.mvar id => \"mvar:\" ++ toString id.name",
            "  | Expr.sort level => \"sort:\" ++ toString level",
            "  | Expr.const name levels => \"const:\" ++ name.toString ++ \":\" ++ toString levels",
            "  | Expr.app fn arg => \"(app \" ++ exprKey fn ++ \" \" ++ exprKey arg ++ \")\"",
            "  | Expr.lam _ domain body info =>",
            "      \"(lam \" ++ binderInfoKey info ++ \" \" ++ exprKey domain ++ \" \" ++ exprKey body ++ \")\"",
            "  | Expr.forallE _ domain body info =>",
            "      \"(forall \" ++ binderInfoKey info ++ \" \" ++ exprKey domain ++ \" \" ++ exprKey body ++ \")\"",
            "  | Expr.letE _ type value body _ =>",
            "      \"(let \" ++ exprKey type ++ \" \" ++ exprKey value ++ \" \" ++ exprKey body ++ \")\"",
            "  | Expr.lit literal => \"lit:\" ++ literalKey literal",
            "  | Expr.mdata _ body => exprKey body",
            "  | Expr.proj structName index body =>",
            "      \"(proj \" ++ structName.toString ++ \" \" ++ toString index ++ \" \" ++ exprKey body ++ \")\"",
            "",
            "def emit_kernel_normal : Elab.Command.CommandElabM Unit := do",
            "  let stx ← parseTermOrThrow canonicalSource",
            "  Elab.Command.runTermElabM fun _ => do",
            "    let expr ← Elab.Term.elabType stx.raw",
            "    let expr ← instantiateMVars expr",
            "    let normal ← Meta.reduceAll expr",
            "    IO.println <| \"LEMMA_KERNEL_NORMAL_FORM \" ++ exprKey normal",
            "",
            f"theorem prop_gate : ({candidate.type_expr}) := by",
            "  sorry",
            "",
            "end LemmaProceduralGate",
        ]
    )


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


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

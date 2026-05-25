"""Generation-time gates for procedural Lean tasks."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
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
_PROP_GATE_DECL = "LemmaProceduralGate.prop_gate"
_KERNEL_NORMAL_MARKER = "LEMMA_KERNEL_NORMAL_FORM "
TRIVIALITY_STACK = (
    ("decide", "  decide"),
    ("simp_all", "  simp_all"),
    ("omega", "  omega"),
    ("norm_num", "  norm_num"),
    ("ring", "  ring"),
    ("linarith", "  linarith"),
    ("nlinarith", "  nlinarith"),
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
        self.gate_heartbeats = int(settings.procedural_gate_max_heartbeats)
        self.triviality_budget_receipt = triviality_budget_receipt or static_triviality_budget_receipt(
            int(settings.procedural_triviality_budget_heartbeats)
        )
        self.triviality_budget_heartbeats = self.triviality_budget_receipt.budget_s
        self.novelty_cache = novelty_cache or empty_novelty_cache()
        self.import_graph = import_graph or empty_import_graph()
        self.lean_workers = _resolve_lean_workers(settings)
        self._lean_slots = threading.BoundedSemaphore(self.lean_workers)

    def __call__(
        self,
        candidate: TaskCandidate,
        *,
        seen_canonical_hashes: Iterable[str],
    ) -> ProceduralGateVerdict:
        statement_novelty = _novelty_status(candidate, seen_canonical_hashes, self.novelty_cache)
        if statement_novelty == "duplicate":
            skipped = VerifyResult(passed=False, reason="compile_error", stderr_tail="statement novelty duplicate")
            return self._gate_verdict(
                candidate,
                seen_canonical_hashes=seen_canonical_hashes,
                typecheck=skipped,
                prop=skipped,
                kernel_hash="",
                novelty="duplicate",
                triviality_checked=False,
                baseline_solved=False,
                baseline_solver=None,
                baseline_reason="not_run",
                prop_gate_reason="skipped_statement_duplicate",
            )

        gate = self._compile_gate(
            candidate,
            _combined_gate_source(candidate),
            fingerprint_name=_PROP_GATE_DECL,
            eval_commands=("#eval! LemmaProceduralGate.emit_kernel_normal",),
        )
        kernel_hash = _gate_canonical_hash(gate) if gate.passed else ""
        novelty = (
            _novelty_status(candidate, seen_canonical_hashes, self.novelty_cache, canonical_hash=kernel_hash)
            if kernel_hash
            else "missing_kernel_fingerprint"
        )
        if gate.passed and novelty == "passed":
            triviality_checked, baseline_solved, baseline_solver, baseline_reason = self._run_triviality_stack(
                candidate
            )
        else:
            triviality_checked, baseline_solved, baseline_solver, baseline_reason = False, False, None, "not_run"
        return self._gate_verdict(
            candidate,
            seen_canonical_hashes=seen_canonical_hashes,
            typecheck=gate,
            prop=gate,
            kernel_hash=kernel_hash,
            novelty=novelty,
            triviality_checked=triviality_checked,
            baseline_solved=baseline_solved,
            baseline_solver=baseline_solver,
            baseline_reason=baseline_reason,
        )

    def _gate_verdict(
        self,
        candidate: TaskCandidate,
        *,
        seen_canonical_hashes: Iterable[str],
        typecheck: VerifyResult,
        prop: VerifyResult,
        kernel_hash: str,
        novelty: str,
        triviality_checked: bool,
        baseline_solved: bool,
        baseline_solver: str | None,
        baseline_reason: str,
        prop_gate_reason: str | None = None,
    ) -> ProceduralGateVerdict:
        _ = seen_canonical_hashes
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
                "prop_gate_reason": prop_gate_reason or prop.reason,
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
        problem = replace(problem, extra={**problem.extra, "lean_max_heartbeats": self.gate_heartbeats})
        with self._lean_slots:
            return run_lean_verify(
                self.settings,
                verify_timeout_s=self.gate_timeout_s,
                problem=problem,
                proof_script=_dummy_submission(candidate),
                submission_policy="strict_envelope",
            )

    def _run_triviality_stack(self, candidate: TaskCandidate) -> tuple[bool, bool, str | None, str]:
        if len(TRIVIALITY_STACK) <= 1 or self.lean_workers <= 1:
            return self._run_triviality_stack_sequential(candidate)

        results: dict[str, tuple[bool, VerifyReason | None]] = {}
        with ThreadPoolExecutor(max_workers=len(TRIVIALITY_STACK)) as pool:
            futures = {
                pool.submit(self._run_triviality_tactic, candidate, name, body): name
                for name, body in TRIVIALITY_STACK
            }
            for future in as_completed(futures):
                name = futures[future]
                passed, reason = future.result()
                results[name] = (passed, reason)

        for name, _body in TRIVIALITY_STACK:
            passed, reason = results[name]
            if passed:
                return True, True, name, "baseline_solved"
            if reason in _INFRA_FAILURES:
                return False, False, None, reason
        return True, False, None, "baseline_failed"

    def _run_triviality_stack_sequential(self, candidate: TaskCandidate) -> tuple[bool, bool, str | None, str]:
        for name, body in TRIVIALITY_STACK:
            passed, reason = self._run_triviality_tactic(candidate, name, body)
            if passed:
                return True, True, name, "baseline_solved"
            if reason in _INFRA_FAILURES:
                return False, False, None, reason
        return True, False, None, "baseline_failed"

    def _run_triviality_tactic(
        self,
        candidate: TaskCandidate,
        name: str,
        body: str,
    ) -> tuple[bool, VerifyReason | None]:
        with self._lean_slots:
            problem = candidate.to_task().to_problem()
            problem = replace(
                problem,
                extra={**problem.extra, "lean_max_heartbeats": self.triviality_budget_heartbeats},
            )
            result = run_lean_verify(
                self.settings,
                verify_timeout_s=self.gate_timeout_s,
                problem=problem,
                proof_script=_candidate_submission(candidate, body),
                submission_policy=candidate.policy,
            )
        if result.passed:
            return True, None
        return False, result.reason


def _resolve_lean_workers(settings: LemmaSettings) -> int:
    configured = int(getattr(settings, "procedural_lean_workers", 0) or 0)
    if configured > 0:
        return configured
    generation_workers = int(getattr(settings, "procedural_generation_workers", 0) or 0)
    if generation_workers > 0:
        return generation_workers
    return min(8, max(1, os.cpu_count() or 1))


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


def _gate_canonical_hash(result: VerifyResult) -> str:
    return _kernel_normal_hash(result) or result.declaration_fingerprints.get(_PROP_GATE_DECL, "")


def _combined_gate_source(candidate: TaskCandidate) -> str:
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
            "    unless (← Meta.isProp expr) do",
            "      throwError \"procedural gate expected Prop\"",
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

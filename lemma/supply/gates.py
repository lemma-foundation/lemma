"""Generation-time gates for procedural Lean tasks."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Protocol

from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.problems.base import Problem
from lemma.supply.import_graph import ImportGraph, empty_import_graph
from lemma.supply.novelty import NoveltyCache, empty_novelty_cache
from lemma.supply.slot_weight import slot_weight_receipt_for_candidate
from lemma.supply.source_pricing import (
    is_lean_decl_name,
    source_import_status,
    source_pricing_metadata,
    source_theorem_wrapper_exact,
)
from lemma.supply.triviality_budget import (
    TrivialityBudgetReceipt,
    static_triviality_budget_receipt,
)
from lemma.supply.types import TaskCandidate

GATE_VERSION = "lemma-procedural-gates-v7"
_TYPECHECK_GATE_DECL = "LemmaProceduralGate.typecheck_gate"
_PROP_GATE_DECL = "LemmaProceduralGate.prop_gate"
_KERNEL_NORMAL_MARKER = "LEMMA_KERNEL_NORMAL_FORM "
_TRIVIALITY_MARKER = "LEMMA_TRIVIALITY "
_BATCH_GATE_MARKER = "LEMMA_GATE_RESULT "
_BATCH_GATE_DONE_MARKER = "LEMMA_GATE_DONE "
_INFRA_GATE_REASONS = frozenset({"docker_error", "remote_error"})
TRIVIALITY_STACK = (
    ("decide", "  decide"),
    ("simp_all", "  simp_all"),
    ("tauto", "  tauto"),
    ("omega", "  omega"),
    ("norm_num", "  norm_num"),
    ("ring", "  ring"),
    ("linarith", "  linarith"),
    ("nlinarith", "  nlinarith"),
    ("aesop", "  aesop"),
)
_BatchGateInput = tuple[int, TaskCandidate]
_BatchGateRow = tuple[int, TaskCandidate, dict[str, object] | None, VerifyResult, int]


@dataclass(frozen=True)
class _BaselineCheck:
    triviality_checked: bool
    baseline_solved: bool
    baseline_solver: str | None
    baseline_reason: str
    source_oracle_checked: bool
    source_oracle_solved: bool
    source_oracle_solver: str | None
    source_import_status: str


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
        pricing = source_pricing_metadata(candidate.source_stream, {**candidate.metadata, "baseline_solved": False})
        priced = candidate.model_copy(update={"metadata": {**candidate.metadata, **pricing, "baseline_solved": False}})
        slot_weight = slot_weight_receipt_for_candidate(priced, import_graph=self.import_graph)
        budget = static_triviality_budget_receipt(1)
        import_status = _source_import_status(candidate)
        return ProceduralGateVerdict(
            typechecked=True,
            prop_gate_passed=True,
            triviality_checked=True,
            baseline_solved=False,
            novelty_status=novelty,
            slot_weight=slot_weight.weight,
            metadata={
                "gate_runner": "assumed",
                "triviality_stack": _triviality_stack_names(candidate),
                "source_oracle_checked": False,
                "source_oracle_solved": False,
                "source_oracle_solver": None,
                "source_import_status": import_status,
                **pricing,
                **self.novelty_cache.metadata(),
                **budget.metadata(),
                **slot_weight.metadata(),
            },
        )


class LeanProceduralGateRunner:
    """Lean-backed implementation of the four paid-production gates."""

    requires_serious_candidates = True

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
        self.batch_size = max(1, int(getattr(settings, "procedural_lean_batch_size", 96) or 96))
        configured_batch_parallelism = int(getattr(settings, "procedural_lean_batch_parallelism", 0) or 0)
        self.batch_parallelism = max(1, configured_batch_parallelism or 1)
        self.compile_error_split_limit = max(
            0,
            int(getattr(settings, "procedural_lean_compile_error_split_limit", 16) or 0),
        )
        self._lean_slots = threading.BoundedSemaphore(self.lean_workers)

    def batch_capacity(self, generation_workers: int) -> int:
        return max(generation_workers, self.batch_size * self.batch_parallelism)

    def batch(
        self,
        candidates: tuple[TaskCandidate, ...],
        *,
        seen_canonical_hashes: Iterable[str],
    ) -> tuple[ProceduralGateVerdict, ...]:
        if not candidates:
            return ()
        seen = tuple(seen_canonical_hashes)
        out: list[ProceduralGateVerdict | None] = [None] * len(candidates)
        pending: list[tuple[int, TaskCandidate]] = []
        for index, candidate in enumerate(candidates):
            statement_novelty = _novelty_status(candidate, seen, self.novelty_cache)
            if statement_novelty == "duplicate":
                skipped = VerifyResult(passed=False, reason="compile_error", stderr_tail="statement novelty duplicate")
                out[index] = self._gate_verdict(
                    candidate,
                    seen_canonical_hashes=seen,
                    typecheck=skipped,
                    prop=skipped,
                    kernel_hash="",
                    novelty="duplicate",
                    triviality_checked=False,
                    baseline_solved=False,
                    baseline_solver=None,
                    baseline_reason="not_run",
                    source_oracle_checked=False,
                    source_oracle_solved=False,
                    source_oracle_solver=None,
                    source_import_status=_source_import_status(candidate),
                    prop_gate_reason="skipped_statement_duplicate",
                    lean_gate_invocations=0,
                )
            else:
                pending.append((index, candidate))

        gate_groups = _batch_gate_groups(pending, batch_size=self.batch_size)
        compile_split_budget = [self.compile_error_split_limit]
        compile_split_lock = threading.Lock()
        if self.batch_parallelism <= 1 or len(gate_groups) <= 1:
            group_results = [
                self._run_batch_gate_group(
                    group,
                    compile_split_budget=compile_split_budget,
                    compile_split_lock=compile_split_lock,
                )
                for group in gate_groups
            ]
        else:
            with ThreadPoolExecutor(max_workers=min(self.batch_parallelism, len(gate_groups))) as pool:
                futures = {
                    pool.submit(
                        self._run_batch_gate_group,
                        group,
                        compile_split_budget=compile_split_budget,
                        compile_split_lock=compile_split_lock,
                    ): index
                    for index, group in enumerate(gate_groups)
                }
                grouped: list[tuple[_BatchGateRow, ...] | None] = [None] * len(gate_groups)
                for future in as_completed(futures):
                    grouped[futures[future]] = future.result()
                group_results = [group for group in grouped if group is not None]
        for group_result in group_results:
            for output_index, candidate, payload, result, batch_size in group_result:
                out[output_index] = self._batch_gate_verdict(
                    candidate,
                    payload=payload,
                    result=result,
                    seen_canonical_hashes=seen,
                    batch_size=batch_size,
                )

        return tuple(verdict for verdict in out if verdict is not None)

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
                lean_gate_invocations=0,
            )

        prop = self._compile_gate(
            candidate,
            _prop_gate_source(candidate),
            fingerprint_names=(_TYPECHECK_GATE_DECL, _PROP_GATE_DECL),
            eval_commands=(
                "#lemma_emit_kernel_normal",
                f"set_option maxHeartbeats {self.triviality_budget_heartbeats}",
                "#lemma_emit_triviality",
            ),
        )
        _raise_infra_gate_failure(prop)
        kernel_hash = _gate_canonical_hash(prop) if prop.passed else ""
        novelty = (
            _novelty_status(candidate, seen_canonical_hashes, self.novelty_cache, canonical_hash=kernel_hash)
            if kernel_hash
            else "missing_kernel_fingerprint"
        )
        if prop.passed and novelty == "passed":
            baseline = _triviality_result(prop, source_import_status=_source_import_status(candidate))
        else:
            baseline = _baseline_not_run(_source_import_status(candidate))
        return self._gate_verdict(
            candidate,
            seen_canonical_hashes=seen_canonical_hashes,
            typecheck=prop,
            prop=prop,
            kernel_hash=kernel_hash,
            novelty=novelty,
            triviality_checked=baseline.triviality_checked,
            baseline_solved=baseline.baseline_solved,
            baseline_solver=baseline.baseline_solver,
            baseline_reason=baseline.baseline_reason,
            source_oracle_checked=baseline.source_oracle_checked,
            source_oracle_solved=baseline.source_oracle_solved,
            source_oracle_solver=baseline.source_oracle_solver,
            source_import_status=baseline.source_import_status,
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
        source_oracle_checked: bool = False,
        source_oracle_solved: bool = False,
        source_oracle_solver: str | None = None,
        source_import_status: str = "unknown",
        prop_gate_reason: str | None = None,
        lean_gate_invocations: int = 1,
        lean_gate_mode: str = "combined_prop_triviality",
        lean_gate_batch_size: int | None = None,
        lean_gate_batch_seconds: float | None = None,
    ) -> ProceduralGateVerdict:
        _ = seen_canonical_hashes
        _ = lean_gate_batch_seconds
        metadata = {
            **candidate.metadata,
            "baseline_solved": baseline_solved,
            "source_oracle_checked": source_oracle_checked,
            "source_oracle_solved": source_oracle_solved,
            "source_oracle_solver": source_oracle_solver,
            "source_import_status": source_import_status,
        }
        pricing = source_pricing_metadata(candidate.source_stream, metadata)
        priced = candidate.model_copy(update={"metadata": {**metadata, **pricing}})
        slot_weight = slot_weight_receipt_for_candidate(priced, import_graph=self.import_graph)
        batch_metadata: dict[str, object] = {}
        if lean_gate_batch_size is not None:
            batch_metadata["lean_gate_batch_size"] = lean_gate_batch_size
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
                "triviality_stack": _triviality_stack_names(candidate),
                "triviality_reason": baseline_reason,
                "baseline_solver": baseline_solver,
                "source_oracle_checked": source_oracle_checked,
                "source_oracle_solved": source_oracle_solved,
                "source_oracle_solver": source_oracle_solver,
                "source_import_status": source_import_status,
                "lean_gate_mode": lean_gate_mode,
                "lean_gate_invocations": lean_gate_invocations,
                **pricing,
                **batch_metadata,
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
        fingerprint_names: tuple[str, ...],
        eval_commands: tuple[str, ...] = (),
    ) -> VerifyResult:
        problem = _gate_problem(
            candidate,
            gate_source,
            fingerprint_names=fingerprint_names,
            eval_commands=eval_commands,
        )
        problem = replace(problem, extra={**problem.extra, "lean_max_heartbeats": self.gate_heartbeats})
        with self._lean_slots:
            return run_lean_verify(
                self.settings,
                verify_timeout_s=self.gate_timeout_s,
                problem=problem,
                proof_script=_dummy_submission(candidate),
                submission_policy="strict_envelope",
            )

    def _compile_batch_gate(self, candidates: tuple[TaskCandidate, ...]) -> VerifyResult:
        first = candidates[0]
        imports = _combined_imports(candidate.imports for candidate in candidates)
        problem = Problem(
            id=f"{first.id}.gate.batch",
            theorem_name="lemma_gate_dummy",
            type_expr="True",
            split="procedural_gate",
            lean_toolchain=first.lean_toolchain,
            mathlib_rev=first.mathlib_rev,
            imports=imports,
            extra={
                "challenge_full": _batch_gate_source(candidates),
                "lean_build_target": "Challenge",
                "lean_eval_commands": (
                    f"set_option maxHeartbeats {self.triviality_budget_heartbeats}",
                    "#lemma_emit_gate_results",
                ),
                "lean_skip_axiom_check": True,
                "lean_skip_submission_axiom_check": True,
                "submission_policy": "strict_envelope",
                "lean_max_heartbeats": self.gate_heartbeats,
            },
        )
        with self._lean_slots:
            return run_lean_verify(
                self.settings,
                verify_timeout_s=self.gate_timeout_s,
                problem=problem,
                proof_script=_dummy_submission_for_imports(imports),
                submission_policy="strict_envelope",
            )

    def _run_batch_gate_group(
        self,
        group: tuple[_BatchGateInput, ...],
        *,
        compile_split_budget: list[int],
        compile_split_lock: threading.Lock,
    ) -> tuple[_BatchGateRow, ...]:
        result = self._compile_batch_gate(tuple(candidate for _index, candidate in group))
        _raise_infra_gate_failure(result)
        parsed = _batch_gate_results(result)
        batch_complete = _batch_gate_complete(result, expected_count=len(group)) and len(parsed) == len(group)
        if not result.passed and not batch_complete:
            # Lean can emit JSON before later logged elaboration errors fail the file.
            parsed = {}
        if parsed or len(group) == 1 or result.passed or not _batch_result_should_split(
            result,
            compile_split_budget=compile_split_budget,
            compile_split_lock=compile_split_lock,
        ):
            batch_size = len(group)
            return tuple(
                (output_index, candidate, parsed.get(str(local_index)), result, batch_size)
                for local_index, (output_index, candidate) in enumerate(group)
            )

        mid = len(group) // 2
        return (
            *self._run_batch_gate_group(
                group[:mid],
                compile_split_budget=compile_split_budget,
                compile_split_lock=compile_split_lock,
            ),
            *self._run_batch_gate_group(
                group[mid:],
                compile_split_budget=compile_split_budget,
                compile_split_lock=compile_split_lock,
            ),
        )

    def _batch_gate_verdict(
        self,
        candidate: TaskCandidate,
        *,
        payload: dict[str, object] | None,
        result: VerifyResult,
        seen_canonical_hashes: Iterable[str],
        batch_size: int,
    ) -> ProceduralGateVerdict:
        if payload is None:
            reason = result.reason if not result.passed else "compile_error"
            typecheck = VerifyResult(passed=False, reason=reason, build_seconds=result.build_seconds)
            return self._gate_verdict(
                candidate,
                seen_canonical_hashes=seen_canonical_hashes,
                typecheck=typecheck,
                prop=typecheck,
                kernel_hash="",
                novelty="missing_kernel_fingerprint",
                triviality_checked=False,
                baseline_solved=False,
                baseline_solver=None,
                baseline_reason="not_run",
                source_oracle_checked=False,
                source_oracle_solved=False,
                source_oracle_solver=None,
                source_import_status=_source_import_status(candidate),
                lean_gate_mode="batched_prop_triviality",
                lean_gate_invocations=1,
                lean_gate_batch_size=batch_size,
                lean_gate_batch_seconds=result.build_seconds,
            )

        typechecked = payload.get("typechecked") is True
        reason = "ok" if typechecked else "compile_error"
        typecheck = VerifyResult(passed=typechecked, reason=reason, build_seconds=result.build_seconds)
        kernel_value = str(payload.get("kernel_normal_form") or "")
        kernel_hash = hashlib.sha256(kernel_value.encode("utf-8")).hexdigest() if kernel_value else ""
        baseline = _baseline_from_payload(
            payload,
            typechecked=typechecked,
            source_import_status=_source_import_status(candidate),
        )
        if baseline.baseline_solved and not kernel_hash:
            novelty = "passed"
        elif kernel_hash:
            novelty = _novelty_status(candidate, seen_canonical_hashes, self.novelty_cache, canonical_hash=kernel_hash)
        else:
            novelty = "missing_kernel_fingerprint"
        if not (typechecked and (novelty == "passed" or baseline.baseline_solved)):
            baseline = _baseline_not_run(_source_import_status(candidate))
        return self._gate_verdict(
            candidate,
            seen_canonical_hashes=seen_canonical_hashes,
            typecheck=typecheck,
            prop=typecheck,
            kernel_hash=kernel_hash,
            novelty=novelty,
            triviality_checked=baseline.triviality_checked,
            baseline_solved=baseline.baseline_solved,
            baseline_solver=baseline.baseline_solver,
            baseline_reason=baseline.baseline_reason,
            source_oracle_checked=baseline.source_oracle_checked,
            source_oracle_solved=baseline.source_oracle_solved,
            source_oracle_solver=baseline.source_oracle_solver,
            source_import_status=baseline.source_import_status,
            lean_gate_mode="batched_prop_triviality",
            lean_gate_invocations=1,
            lean_gate_batch_size=batch_size,
            lean_gate_batch_seconds=result.build_seconds,
        )


def _triviality_stack_names(candidate: TaskCandidate) -> list[str]:
    _ = candidate
    names = [name for name, _body in TRIVIALITY_STACK]
    return names


def _combined_triviality_body() -> str:
    return "\n".join(["  first", *(f"  | {body.strip()}" for _name, body in TRIVIALITY_STACK)])


def _combined_triviality_source() -> str:
    return f"by\n{_combined_triviality_body()}"


def _combined_imports(groups: Iterable[Iterable[str]]) -> tuple[str, ...]:
    out: list[str] = []
    for group in groups:
        for module in group:
            if module not in out:
                out.append(module)
    return tuple(out)


def _source_import_status(candidate: TaskCandidate) -> str:
    return source_import_status(
        candidate.imports,
        candidate.metadata,
        source_path=candidate.source_ref.path,
    )


def _source_oracle_proofs(candidate: TaskCandidate) -> tuple[tuple[str, str], ...]:
    if _source_import_status(candidate) == "source_theorem_unavailable":
        return ()
    source = candidate.metadata.get("source_theorem_name")
    if not is_lean_decl_name(source):
        return ()
    name = str(source).strip()
    proofs = [
        ("source_exact", f"by\n  exact {name}"),
        ("source_simpa", f"by\n  simpa using {name}"),
        ("source_apply", f"by\n  apply {name}\n  all_goals first | assumption | simp | aesop"),
    ]
    wrapper = source_theorem_wrapper_exact(candidate.metadata, name, type_expr=candidate.type_expr)
    if wrapper is not None:
        proofs.insert(0, ("source_wrapper", f"by\n  exact {wrapper}"))
    return tuple(proofs)


def _lean_proof_specs(proofs: tuple[tuple[str, str], ...]) -> str:
    if not proofs:
        return "[]"
    return (
        "["
        + ", ".join(
            "{ name := " + _json_string(name) + ", source := " + _json_string(source) + " }"
            for name, source in proofs
        )
        + "]"
    )


def _resolve_lean_workers(settings: LemmaSettings) -> int:
    configured = int(getattr(settings, "procedural_lean_workers", 0) or 0)
    if configured > 0:
        return configured
    generation_workers = int(getattr(settings, "procedural_generation_workers", 0) or 0)
    if generation_workers > 0:
        return generation_workers
    return min(8, max(1, os.cpu_count() or 1))


def _raise_infra_gate_failure(result: VerifyResult) -> None:
    if result.passed or result.reason not in _INFRA_GATE_REASONS:
        return
    detail = (result.stderr_tail or result.stdout_tail or result.reason).strip()
    raise RuntimeError(f"procedural Lean gate infrastructure failed: {detail[:500]}")


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
    fingerprint_names: tuple[str, ...],
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
            "lean_fingerprint_names": fingerprint_names,
            "lean_eval_commands": eval_commands,
            "lean_skip_submission_axiom_check": True,
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


def _triviality_result(result: VerifyResult, *, source_import_status: str) -> _BaselineCheck:
    for line in (result.stdout_tail + "\n" + result.stderr_tail).splitlines():
        if not line.startswith(_TRIVIALITY_MARKER):
            continue
        try:
            payload = json.loads(line.removeprefix(_TRIVIALITY_MARKER))
        except json.JSONDecodeError:
            break
        if not isinstance(payload, dict):
            break
        return _baseline_from_payload(payload, typechecked=True, source_import_status=source_import_status)
    return _BaselineCheck(False, False, None, "missing_triviality_marker", False, False, None, source_import_status)


def _baseline_from_payload(
    payload: Mapping[str, object],
    *,
    typechecked: bool,
    source_import_status: str,
) -> _BaselineCheck:
    if not typechecked:
        return _baseline_not_run(source_import_status)
    solver = payload.get("baseline_solver")
    source_solver = payload.get("source_oracle_solver")
    source_oracle_solved = payload.get("source_oracle_solved") is True
    triviality_solved = payload.get("baseline_solved") is True
    baseline_solver = solver if isinstance(solver, str) and solver else None
    if source_oracle_solved:
        baseline_solver = source_solver if isinstance(source_solver, str) and source_solver else "source_oracle"
    return _BaselineCheck(
        triviality_checked=payload.get("checked", payload.get("triviality_checked")) is True,
        baseline_solved=source_oracle_solved or triviality_solved,
        baseline_solver=baseline_solver,
        baseline_reason=str(payload.get("triviality_reason") or "baseline_failed"),
        source_oracle_checked=payload.get("source_oracle_checked") is True,
        source_oracle_solved=source_oracle_solved,
        source_oracle_solver=source_solver if isinstance(source_solver, str) and source_solver else None,
        source_import_status=str(payload.get("source_import_status") or source_import_status),
    )


def _baseline_not_run(source_import_status: str) -> _BaselineCheck:
    return _BaselineCheck(False, False, None, "not_run", False, False, None, source_import_status)


def _batch_gate_groups(
    indexed: list[tuple[int, TaskCandidate]],
    *,
    batch_size: int,
) -> tuple[tuple[_BatchGateInput, ...], ...]:
    groups: dict[tuple[str, str, tuple[str, ...]], list[tuple[int, TaskCandidate]]] = {}
    for item in indexed:
        candidate = item[1]
        groups.setdefault((candidate.lean_toolchain, candidate.mathlib_rev, candidate.imports), []).append(item)
    out: list[tuple[_BatchGateInput, ...]] = []
    for group in groups.values():
        for start in range(0, len(group), max(1, batch_size)):
            out.append(tuple(group[start : start + batch_size]))
    return tuple(out)


def _batch_gate_results(result: VerifyResult) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for line in (result.stdout_tail + "\n" + result.stderr_tail).splitlines():
        if not line.startswith(_BATCH_GATE_MARKER):
            continue
        try:
            payload = json.loads(line.removeprefix(_BATCH_GATE_MARKER))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        key = payload.get("id")
        if isinstance(key, str) and key:
            out[key] = payload
    return out


def _batch_gate_complete(result: VerifyResult, *, expected_count: int) -> bool:
    expected = f"{_BATCH_GATE_DONE_MARKER}{expected_count}"
    return any(line.strip() == expected for line in (result.stdout_tail + "\n" + result.stderr_tail).splitlines())


def _batch_result_should_split(
    result: VerifyResult,
    *,
    compile_split_budget: list[int],
    compile_split_lock: threading.Lock,
) -> bool:
    if result.reason in {"timeout", "oom"}:
        return True
    if result.reason != "compile_error":
        return False
    with compile_split_lock:
        if compile_split_budget[0] <= 0:
            return False
        compile_split_budget[0] -= 1
        return True


def _batch_gate_source(candidates: tuple[TaskCandidate, ...]) -> str:
    specs = [
        "  { "
        f"id := {_json_string(str(index))}, "
        f"canonicalSource := {_json_string(candidate.type_expr)}, "
        f"combinedTrivialitySource := {_json_string(_combined_triviality_source())}, "
        f"sourceOracleProofs := {_lean_proof_specs(_source_oracle_proofs(candidate))}, "
        f"sourceImportStatus := {_json_string(_source_import_status(candidate))} "
        "}"
        for index, candidate in enumerate(candidates)
    ]
    spec_body = ",\n".join(specs)
    return "\n".join(
        [
            "import Lean",
            "",
            "set_option autoImplicit false",
            "",
            "open Lean",
            "",
            "namespace LemmaProceduralGate",
            "",
            "structure ProofSpec where",
            "  name : String",
            "  source : String",
            "",
            "structure CandidateSpec where",
            "  id : String",
            "  canonicalSource : String",
            "  combinedTrivialitySource : String",
            "  sourceOracleProofs : List ProofSpec",
            "  sourceImportStatus : String",
            "",
            "def candidateSpecs : List CandidateSpec := [",
            spec_body,
            "]",
            "",
            "def parseTermOrThrow (source : String) : Elab.Command.CommandElabM (TSyntax `term) := do",
            "  let env ← getEnv",
            "  match Parser.runParserCategory env `term source with",
            "  | Except.ok stx => pure ⟨stx⟩",
            "  | Except.error e => throwError e",
            "",
            "partial def containsSyntheticHole : Expr → Bool",
            "  | Expr.mvar _ => true",
            "  | Expr.const name _ => name == `sorryAx",
            "  | Expr.app fn arg => containsSyntheticHole fn || containsSyntheticHole arg",
            "  | Expr.lam _ domain body _ => containsSyntheticHole domain || containsSyntheticHole body",
            "  | Expr.forallE _ domain body _ => containsSyntheticHole domain || containsSyntheticHole body",
            "  | Expr.letE _ type value body _ =>",
            "      containsSyntheticHole type || containsSyntheticHole value || containsSyntheticHole body",
            "  | Expr.mdata _ body => containsSyntheticHole body",
            "  | Expr.proj _ _ body => containsSyntheticHole body",
            "  | _ => false",
            "",
            "def proofSourceSucceeds (typeSource proofSource : String) : Elab.Command.CommandElabM Bool := do",
            "  if proofSource == \"\" then",
            "    pure false",
            "  else",
            "    try",
            "      let typeStx ← parseTermOrThrow typeSource",
            "      let proofStx ← parseTermOrThrow proofSource",
            "      Elab.Command.runTermElabM fun _ => do",
            "        let expected ← Elab.Term.elabType typeStx.raw",
            "        let expected ← instantiateMVars expected",
            "        if containsSyntheticHole expected then",
            "          throwError \"gate type contains unresolved identifiers\"",
            "        let _ ← Elab.Term.elabTermEnsuringType proofStx.raw (some expected)",
            "        Elab.Term.synthesizeSyntheticMVarsNoPostponing",
            "        pure true",
            "    catch _ =>",
            "      pure false",
            "",
            "def firstSuccessfulProof",
            "    (typeSource : String)",
            "    (proofs : List ProofSpec) : Elab.Command.CommandElabM (Option String) := do",
            "  for proof in proofs do",
            "    let solved ← proofSourceSucceeds typeSource proof.source",
            "    if solved then",
            "      return some proof.name",
            "  pure none",
            "",
            "def nullableString : Option String → Json",
            "  | none => Json.null",
            "  | some value => Json.str value",
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
            "def emitPayload",
            "    (spec : CandidateSpec)",
            "    (typechecked : Bool)",
            "    (kernelNormal : String)",
            "    (trivialityChecked : Bool)",
            "    (baselineSolved : Bool)",
            "    (solver : Option String)",
            "    (sourceOracleChecked : Bool)",
            "    (sourceOracleSolved : Bool)",
            "    (sourceOracleSolver : Option String)",
            "    (reason : String) : IO Unit := do",
            "  let payload := Json.mkObj [",
            "    (\"id\", Json.str spec.id),",
            "    (\"typechecked\", Json.bool typechecked),",
            "    (\"kernel_normal_form\", Json.str kernelNormal),",
            "    (\"triviality_checked\", Json.bool trivialityChecked),",
            "    (\"baseline_solved\", Json.bool baselineSolved),",
            "    (\"baseline_solver\", nullableString solver),",
            "    (\"source_oracle_checked\", Json.bool sourceOracleChecked),",
            "    (\"source_oracle_solved\", Json.bool sourceOracleSolved),",
            "    (\"source_oracle_solver\", nullableString sourceOracleSolver),",
            "    (\"source_import_status\", Json.str spec.sourceImportStatus),",
            "    (\"triviality_reason\", Json.str reason),",
            "    (\"reason\", Json.str reason)",
            "  ]",
            f"  IO.println <| {_json_string(_BATCH_GATE_MARKER)} ++ payload.compress",
            "",
            "def analyzeSpec (spec : CandidateSpec) : Elab.Command.CommandElabM Unit := do",
            "  try",
            "    let typeStx ← parseTermOrThrow spec.canonicalSource",
            "    let _ ← Elab.Command.runTermElabM fun _ => do",
            "      let expr ← Elab.Term.elabType typeStx.raw",
            "      let expr ← instantiateMVars expr",
            "      if containsSyntheticHole expr then",
            "        throwError \"gate type contains unresolved identifiers\"",
            "      pure ()",
            "    let sourceOracleSolver ← firstSuccessfulProof spec.canonicalSource spec.sourceOracleProofs",
            "    let sourceOracleSolved := sourceOracleSolver.isSome",
            "    let baselineSolved ← proofSourceSucceeds spec.canonicalSource spec.combinedTrivialitySource",
            "    let solver : Option String :=",
            "      if sourceOracleSolved then",
            "        sourceOracleSolver",
            "      else if baselineSolved then",
            "        some \"triviality_stack\"",
            "      else",
            "        none",
            "    let solved := sourceOracleSolved || baselineSolved",
            "    let reason : String :=",
            "      if sourceOracleSolved then",
            "        \"source_oracle_solved\"",
            "      else if baselineSolved then",
            "        \"baseline_solved\"",
            "      else",
            "        \"baseline_failed\"",
            "    if solved then",
            "      emitPayload spec true \"\" true solved solver true sourceOracleSolved sourceOracleSolver reason",
            "    else",
            "      let kernelNormal ← Elab.Command.runTermElabM fun _ => do",
            "        let expr ← Elab.Term.elabType typeStx.raw",
            "        let expr ← instantiateMVars expr",
            "        if containsSyntheticHole expr then",
            "          throwError \"gate type contains unresolved identifiers\"",
            "        let normal ← Meta.reduceAll expr",
            "        pure (exprKey normal)",
            "      emitPayload spec true kernelNormal true solved solver true",
            "        sourceOracleSolved sourceOracleSolver reason",
            "  catch _ =>",
            "    emitPayload spec false \"\" false false none false false none \"compile_error\"",
            "",
            "def emit_gate_results : Elab.Command.CommandElabM Unit := do",
            "  for spec in candidateSpecs do",
            "    analyzeSpec spec",
            f"  IO.println <| {_json_string(_BATCH_GATE_DONE_MARKER)} ++ toString candidateSpecs.length",
            "",
            "elab \"#lemma_emit_gate_results\" : command => emit_gate_results",
            "",
            "end LemmaProceduralGate",
        ]
    )


def _prop_gate_source(candidate: TaskCandidate) -> str:
    return "\n".join(
        [
            "import Lean",
            "",
            "set_option autoImplicit false",
            "",
            "open Lean",
            "",
            "namespace LemmaProceduralGate",
            "",
            f"def canonicalSource : String := {_json_string(candidate.type_expr)}",
            f"def combinedTrivialitySource : String := {_json_string(_combined_triviality_source())}",
            f"def sourceImportStatus : String := {_json_string(_source_import_status(candidate))}",
            "",
            "structure ProofSpec where",
            "  name : String",
            "  source : String",
            "",
            f"def sourceOracleProofs : List ProofSpec := {_lean_proof_specs(_source_oracle_proofs(candidate))}",
            "",
            f"def typecheck_gate : ({candidate.type_expr}) := by",
            "  sorry",
            "",
            "def parseTermOrThrow (source : String) : Elab.Command.CommandElabM (TSyntax `term) := do",
            "  let env ← getEnv",
            "  match Parser.runParserCategory env `term source with",
            "  | Except.ok stx => pure ⟨stx⟩",
            "  | Except.error e => throwError e",
            "",
            "partial def containsSyntheticHole : Expr → Bool",
            "  | Expr.mvar _ => true",
            "  | Expr.const name _ => name == `sorryAx",
            "  | Expr.app fn arg => containsSyntheticHole fn || containsSyntheticHole arg",
            "  | Expr.lam _ domain body _ => containsSyntheticHole domain || containsSyntheticHole body",
            "  | Expr.forallE _ domain body _ => containsSyntheticHole domain || containsSyntheticHole body",
            "  | Expr.letE _ type value body _ =>",
            "      containsSyntheticHole type || containsSyntheticHole value || containsSyntheticHole body",
            "  | Expr.mdata _ body => containsSyntheticHole body",
            "  | Expr.proj _ _ body => containsSyntheticHole body",
            "  | _ => false",
            "",
            "def proofSourceSucceeds (proofSource : String) : Elab.Command.CommandElabM Bool := do",
            "  if proofSource == \"\" then",
            "    pure false",
            "  else",
            "    try",
            "      let typeStx ← parseTermOrThrow canonicalSource",
            "      let proofStx ← parseTermOrThrow proofSource",
            "      Elab.Command.runTermElabM fun _ => do",
            "        let expected ← Elab.Term.elabType typeStx.raw",
            "        let expected ← instantiateMVars expected",
            "        if containsSyntheticHole expected then",
            "          throwError \"gate type contains unresolved identifiers\"",
            "        let _ ← Elab.Term.elabTermEnsuringType proofStx.raw (some expected)",
            "        Elab.Term.synthesizeSyntheticMVarsNoPostponing",
            "        pure true",
            "    catch _ =>",
            "      pure false",
            "",
            "def firstSuccessfulProof (proofs : List ProofSpec) : Elab.Command.CommandElabM (Option String) := do",
            "  for proof in proofs do",
            "    let solved ← proofSourceSucceeds proof.source",
            "    if solved then",
            "      return some proof.name",
            "  pure none",
            "",
            "def nullableString : Option String → Json",
            "  | none => Json.null",
            "  | some value => Json.str value",
            "",
            "def emit_triviality : Elab.Command.CommandElabM Unit := do",
            "  let sourceOracleSolver ← firstSuccessfulProof sourceOracleProofs",
            "  let sourceOracleSolved := sourceOracleSolver.isSome",
            "  let baselineSolved ← proofSourceSucceeds combinedTrivialitySource",
            "  let solver : Option String :=",
            "    if sourceOracleSolved then",
            "      sourceOracleSolver",
            "    else if baselineSolved then",
            "      some \"triviality_stack\"",
            "    else",
            "      none",
            "  let solved := sourceOracleSolved || baselineSolved",
            "  let reason : String :=",
            "    if sourceOracleSolved then",
            "      \"source_oracle_solved\"",
            "    else if baselineSolved then",
            "      \"baseline_solved\"",
            "    else",
            "      \"baseline_failed\"",
            "  let payload := Json.mkObj [",
            "    (\"checked\", Json.bool true),",
            "    (\"baseline_solved\", Json.bool solved),",
            "    (\"baseline_solver\", nullableString solver),",
            "    (\"source_oracle_checked\", Json.bool true),",
            "    (\"source_oracle_solved\", Json.bool sourceOracleSolved),",
            "    (\"source_oracle_solver\", nullableString sourceOracleSolver),",
            "    (\"source_import_status\", Json.str sourceImportStatus),",
            "    (\"triviality_reason\", Json.str reason)",
            "  ]",
            f"  IO.println <| {_json_string(_TRIVIALITY_MARKER)} ++ payload.compress",
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
            "    if containsSyntheticHole expr then",
            "      throwError \"gate type contains unresolved identifiers\"",
            "    let normal ← Meta.reduceAll expr",
            "    IO.println <| \"LEMMA_KERNEL_NORMAL_FORM \" ++ exprKey normal",
            "",
            "elab \"#lemma_emit_kernel_normal\" : command => emit_kernel_normal",
            "elab \"#lemma_emit_triviality\" : command => emit_triviality",
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
    return _dummy_submission_for_imports(candidate.imports)


def _dummy_submission_for_imports(imports: tuple[str, ...]) -> str:
    return "\n".join(
        [
            *(f"import {module}" for module in imports),
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

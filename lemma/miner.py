"""Miner-side proof search helpers."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.store import append_jsonl
from lemma.submissions import LemmaSubmission, build_submission, sign_submission
from lemma.task_supply import eligible_tasks
from lemma.tasks import LemmaTask, TaskRegistry
from lemma.validator import active_tasks_for_validation, current_active_tempo, task_registry_for_validation
from lemma.verifiers.lean import verify_result_from_adapter_result
from lemma.verifiers.registry import get_verifier


class ProverError(RuntimeError):
    """Raised when a prover adapter cannot return a usable proof."""


class ProverResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    proof_script: str
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class MineOnceResult:
    task: LemmaTask
    submission: LemmaSubmission
    verification: VerifyResult


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def prover_input(task: LemmaTask, timeout_s: float) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": task.id,
        "task_version": task.task_version,
        "statement": task.statement,
        "imports": list(task.imports),
        "submission_stub": task.submission_stub,
        "timeout_s": timeout_s,
    }
    source_theorem = _source_theorem_name(task)
    if source_theorem is not None:
        payload["source_theorem_name"] = source_theorem
    return payload


def run_prover_command(command: str, task: LemmaTask, timeout_s: float) -> ProverResult:
    """Run a local prover command with JSON on stdin and JSON on stdout."""
    if not command.strip():
        raise ProverError("LEMMA_PROVER_COMMAND is not configured")
    try:
        proc = subprocess.run(  # noqa: S603
            shlex.split(command),
            input=json.dumps(prover_input(task, timeout_s)),
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ProverError(f"prover timed out after {timeout_s:g}s") from e
    if proc.returncode != 0:
        raise ProverError((proc.stderr or proc.stdout or f"prover exited {proc.returncode}").strip())
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ProverError(f"prover returned invalid JSON: {e}") from e
    return _normalize_prover_result(task, ProverResult.model_validate(payload))


def _normalize_prover_result(task: LemmaTask, proof: ProverResult) -> ProverResult:
    script = proof.proof_script.replace("\r\n", "\n").replace("\r", "\n").strip()
    if proof.task_id != task.id:
        raise ProverError(f"prover returned task_id {proof.task_id}, expected {task.id}")
    if not script:
        raise ProverError("prover returned an empty proof_script")
    if "namespace Submission" in script and not script.splitlines()[-1].strip() == "end Submission":
        script = script + "\n\nend Submission\n"
    return proof.model_copy(update={"proof_script": script})


_LEAN_DECL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")
_TRUE_ARROW_PREFIX_RE = re.compile(r"^True\s*→\s*")


def _source_theorem_name(task: LemmaTask) -> str | None:
    name = task.metadata.get("source_theorem_name")
    if not isinstance(name, str):
        return None
    name = name.strip()
    return name if _LEAN_DECL_RE.fullmatch(name) else None


def _source_theorem_wrapper_prover(task: LemmaTask) -> ProverResult | None:
    source_theorem = _source_theorem_name(task)
    if source_theorem is None or "\n  sorry" not in task.submission_stub:
        return None
    exact = _source_theorem_exact(task, source_theorem)
    intro_count = _source_theorem_wrapper_intro_count(task)
    intros = "".join("\n  intro _" for _ in range(intro_count))
    proof_script = task.submission_stub.replace("\n  sorry", f"{intros}\n  exact {exact}", 1)
    return ProverResult(
        task_id=task.id,
        proof_script=proof_script,
        metadata={"prover": "source_theorem_wrapper", "source_theorem_name": source_theorem},
    )


def _leading_true_premise_count(type_expr: str) -> int:
    text = _strip_outer_parens(type_expr.strip())
    count = 0
    while match := _TRUE_ARROW_PREFIX_RE.match(text):
        count += 1
        text = _strip_outer_parens(text[match.end() :].strip())
    return count


def _source_theorem_wrapper_intro_count(task: LemmaTask) -> int:
    count = _fresh_prop_hypothesis_intro_count(task) + _leading_true_premise_count(task.type_expr)
    for step in task.metadata.get("mutation_chain", ()):
        if not isinstance(step, dict):
            continue
        params = step.get("params")
        if isinstance(params, dict) and params.get("mode") == "peer_premise":
            count += 1
    return count


def _fresh_prop_hypothesis_intro_count(task: LemmaTask) -> int:
    count = 0
    for step in task.metadata.get("mutation_chain", ()):
        if not isinstance(step, dict):
            continue
        params = step.get("params")
        if not isinstance(params, dict):
            continue
        if params.get("target") == "fresh_prop_hypothesis" and params.get("binder_type") == "Prop":
            count += 2
    return count


def _strip_outer_parens(text: str) -> str:
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    return text
        text = text[1:-1].strip()
    return text


def _source_theorem_exact(task: LemmaTask, source_theorem: str) -> str:
    exact = source_theorem
    wraps_false = False
    for step in task.metadata.get("mutation_chain", ()):
        if not isinstance(step, dict):
            continue
        params = step.get("params")
        if not isinstance(params, dict):
            continue
        if params.get("fallback") in {"true_premise", "no_supported_type_occurrence"}:
            exact = f"(fun _ => {exact})"
        elif params.get("rule") == "conjoin_peer_conclusion":
            peer = params.get("peer_theorem_name")
            if isinstance(peer, str) and _LEAN_DECL_RE.fullmatch(peer.strip()):
                exact = f"And.intro ({exact}) {peer.strip()}"
        elif params.get("rule") == "false_disjunct":
            exact = f"Or.inl ({exact})"
            wraps_false = True
    if not wraps_false and "∨" in task.type_expr and "False" in task.type_expr:
        exact = f"Or.inl {exact}"
    return exact


def run_openai_compatible_prover(
    settings: LemmaSettings,
    task: LemmaTask,
    *,
    failed_proof: ProverResult | None = None,
    failed_verification: VerifyResult | None = None,
) -> ProverResult:
    """Minimal OpenAI-compatible fallback for miners who opt into hosted APIs."""
    if not settings.prover_base_url.strip() or not settings.prover_model.strip():
        raise ProverError("OpenAI-compatible prover endpoint is not configured")
    headers = {"Authorization": f"Bearer {settings.prover_api_key}"} if settings.prover_api_key else {}
    url = settings.prover_base_url.rstrip("/") + "/chat/completions"
    task_payload = prover_input(task, settings.prover_timeout_s)
    user_payload: dict[str, Any] = {"task": task_payload}
    if failed_proof is not None and failed_verification is not None:
        user_payload["failed_attempt"] = {
            "proof_script": failed_proof.proof_script,
            "reason": failed_verification.reason,
            "stderr_tail": failed_verification.stderr_tail[-6000:],
            "stdout_tail": failed_verification.stdout_tail[-2000:],
        }
    try:
        response = httpx.post(
            url,
            headers=headers,
            timeout=settings.prover_timeout_s,
            json={
                "model": settings.prover_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only a JSON object with task_id and proof_script, no Markdown. "
                            "proof_script must be a complete Lean file matching submission_stub exactly: keep only the "
                            "listed imports, the namespace, the exact theorem header, and the final end line, "
                            "replacing only the sorry proof. Do not use sorry, admit, axioms, unsafe features, "
                            "or extra imports. "
                            "If source_theorem_name is present and the target only wraps that theorem in extra binders "
                            "or trivial premises, introduce the wrappers and reuse source_theorem_name directly."
                        ),
                    },
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise ProverError(f"OpenAI-compatible prover request failed: {e}") from e
    content = response.json()["choices"][0]["message"]["content"]
    try:
        payload = json.loads(_strip_json_fence(content))
        if isinstance(payload, dict) and "task_id" not in payload:
            payload["task_id"] = task.id
        proof = ProverResult.model_validate(payload)
    except ValueError as e:
        raise ProverError(f"OpenAI-compatible prover returned invalid JSON: {e}") from e
    return _normalize_prover_result(task, proof)


def solve_task(settings: LemmaSettings, task: LemmaTask, *, prover_command: str | None = None) -> ProverResult:
    command = prover_command if prover_command is not None else settings.prover_command
    if command.strip():
        return run_prover_command(command, task, settings.prover_timeout_s)
    return run_openai_compatible_prover(settings, task)


def sign_submission_with_wallet(settings: LemmaSettings, submission: LemmaSubmission) -> LemmaSubmission:
    import bittensor as bt

    keypair = bt.Wallet(name=settings.wallet_cold, hotkey=settings.wallet_hot).hotkey
    bound = LemmaSubmission.model_validate(
        {**submission.model_dump(), "solver_hotkey": str(keypair.ss58_address), "signature_payload_sha256": ""}
    )
    return sign_submission(bound, keypair)


def mine_once(
    settings: LemmaSettings,
    *,
    task_id: str | None = None,
    prover_command: str | None = None,
    registry: TaskRegistry | None = None,
    solver_hotkey: str | None = None,
    sign: bool = False,
) -> MineOnceResult:
    """Fetch one active task, solve it, verify locally, and store the attempt."""
    active_tempo = current_active_tempo(settings)
    registry = registry or task_registry_for_validation(settings, tempo=active_tempo)
    if task_id:
        task = registry.get(task_id)
    elif tasks := eligible_tasks(active_tasks_for_validation(registry, settings, tempo=active_tempo)):
        task = tasks[0]
    else:
        raise ProverError("no eligible active tasks")

    verifier = get_verifier(task.domain_id, settings=settings)
    command = prover_command if prover_command is not None else settings.prover_command
    hosted_attempt_limit = 0 if command.strip() else 1 + settings.prover_repair_attempts
    source_wrapper = None if command.strip() else _source_theorem_wrapper_prover(task)
    source_wrapper_tried = False
    initial_prover_tried = False
    hosted_attempts = 0
    proof: ProverResult | None = None
    draft_submission: LemmaSubmission | None = None
    verification: VerifyResult | None = None
    attempts = 0
    while True:
        if source_wrapper is not None and not source_wrapper_tried:
            proof = source_wrapper
            source_wrapper_tried = True
        elif not initial_prover_tried:
            proof = solve_task(settings, task, prover_command=prover_command)
            initial_prover_tried = True
            if not command.strip():
                hosted_attempts += 1
        else:
            if proof is None or verification is None:
                raise ProverError("prover repair attempted before an initial proof")
            if hosted_attempts >= hosted_attempt_limit:
                break
            proof = _normalize_prover_result(
                task,
                run_openai_compatible_prover(
                    settings,
                    task,
                    failed_proof=proof,
                    failed_verification=verification,
                ),
            )
            hosted_attempts += 1
        attempts += 1
        draft_submission = build_submission(
            task,
            solver_hotkey=solver_hotkey or settings.wallet_hot,
            proof_script=proof.proof_script,
            metadata=proof.metadata,
        )
        verification = verify_result_from_adapter_result(verifier.verify(task, draft_submission))
        if verification.passed:
            break
    if proof is None or draft_submission is None or verification is None:
        raise ProverError("prover did not return a proof")
    if not verification.passed:
        raise ProverError(f"local verification failed after {attempts} attempt(s): {verification.reason}")

    submission = sign_submission_with_wallet(settings, draft_submission) if sign else draft_submission
    append_jsonl(
        settings.operator_data_dir / "miner-attempts.jsonl",
        [
            {
                "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "task_id": task.id,
                "task_version": task.task_version,
                "target_sha256": task.target_sha256,
                "proof_sha256": submission.proof_sha256,
                "passed_local_verify": verification.passed,
            }
        ],
    )
    return MineOnceResult(task=task, submission=submission, verification=verification)

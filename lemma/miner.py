"""Miner-side proof search helpers."""

from __future__ import annotations

import json
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
from lemma.tasks import LemmaTask, TaskRegistry, fetch_task_registry
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
    return {
        "task_id": task.id,
        "task_version": task.task_version,
        "statement": task.statement,
        "imports": list(task.imports),
        "submission_stub": task.submission_stub,
        "timeout_s": timeout_s,
    }


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
    result = ProverResult.model_validate(payload)
    if result.task_id != task.id:
        raise ProverError(f"prover returned task_id {result.task_id}, expected {task.id}")
    if not result.proof_script.strip():
        raise ProverError("prover returned an empty proof_script")
    return result


def run_openai_compatible_prover(settings: LemmaSettings, task: LemmaTask) -> ProverResult:
    """Minimal OpenAI-compatible fallback for miners who opt into hosted APIs."""
    if not settings.prover_base_url.strip() or not settings.prover_model.strip():
        raise ProverError("OpenAI-compatible prover endpoint is not configured")
    headers = {"Authorization": f"Bearer {settings.prover_api_key}"} if settings.prover_api_key else {}
    url = settings.prover_base_url.rstrip("/") + "/chat/completions"
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
                        "proof_script must be a complete Lean file matching submission_stub: keep the imports, "
                        "namespace, theorem header, and final end line, replacing only the sorry proof."
                    ),
                },
                {"role": "user", "content": json.dumps(prover_input(task, settings.prover_timeout_s))},
            ],
        },
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    try:
        return ProverResult.model_validate_json(_strip_json_fence(content))
    except ValueError as e:
        raise ProverError(f"OpenAI-compatible prover returned invalid JSON: {e}") from e


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
    registry = registry or fetch_task_registry(settings)
    tasks = eligible_tasks(registry.tasks)
    if task_id:
        task = registry.get(task_id)
    elif tasks:
        task = tasks[0]
    else:
        raise ProverError("no eligible active tasks")

    proof = solve_task(settings, task, prover_command=prover_command)
    verifier = get_verifier(task.domain_id, settings=settings)
    draft_submission = build_submission(
        task,
        solver_hotkey=solver_hotkey or settings.wallet_hot,
        proof_script=proof.proof_script,
        metadata=proof.metadata,
    )
    verification = verify_result_from_adapter_result(verifier.verify(task, draft_submission))
    if not verification.passed:
        raise ProverError(f"local verification failed: {verification.reason}")

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

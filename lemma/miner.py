"""Miner-side proof search helpers."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from lemma.chain.commitments import ChainCommitmentSubmission
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.store import append_jsonl
from lemma.submissions import LemmaSubmission, build_submission, sign_submission
from lemma.task_supply import eligible_tasks
from lemma.tasks import LemmaTask, TaskRegistry, fetch_task_registry
from lemma.validator import active_tasks_for_validation
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
    active_slot_index: int | None = None
    active_tempo: int | None = None


@dataclass(frozen=True)
class BucketPublishResult:
    path: Path
    reveal_merkle_root: str
    commit_block: int
    commitment: ChainCommitmentSubmission | None = None


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


def publish_bucket_reveal(
    settings: LemmaSettings,
    result: MineOnceResult,
    *,
    bucket_dir: Path,
    bucket_url: str = "",
    commit: bool = False,
    drand_round: int = 0,
) -> BucketPublishResult:
    from lemma.chain.commitments import submit_miner_bucket_commitment
    from lemma.chain.miner_buckets import (
        bucket_reveal_path,
        build_bucket_reveal,
        build_revealed_bucket_blob,
        write_bucket_reveal,
    )
    from lemma.validator import current_active_tempo

    if result.active_slot_index is None:
        raise ProverError("bucket publishing requires an active task slot")
    tempo = result.active_tempo if result.active_tempo is not None else current_active_tempo(settings)
    blob = build_revealed_bucket_blob(slot_index=result.active_slot_index, proof_script=result.submission.proof_script)
    draft = build_bucket_reveal(
        tempo=tempo,
        miner_hotkey=result.submission.solver_hotkey,
        drand_round=drand_round,
        commit_block=0,
        commit_extrinsic_hash="local",
        blobs=(blob,),
    )
    commitment = None
    commit_block = 0
    commit_hash = "local"
    if commit:
        commitment = submit_miner_bucket_commitment(
            settings,
            tempo=tempo,
            drand_round=drand_round,
            merkle_root=draft.merkle_root,
        )
        append_jsonl(
            settings.operator_data_dir / "miner-bucket-commits.jsonl",
            [
                {
                    "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "tempo": tempo,
                    "drand_round": drand_round,
                    "merkle_root": draft.merkle_root,
                    "success": commitment.success,
                    "extrinsic_hash": commitment.extrinsic_hash,
                    "block_number": commitment.block_number,
                    "message": commitment.message,
                }
            ],
        )
        if not commitment.success:
            raise ProverError(f"chain commitment failed: {commitment.message or 'unknown error'}")
        if commitment.block_number is None:
            raise ProverError("chain commitment did not return a block number")
        if not commitment.extrinsic_hash:
            raise ProverError("chain commitment did not return an extrinsic hash")
        commit_block = commitment.block_number
        commit_hash = commitment.extrinsic_hash

    path = bucket_reveal_path(bucket_dir, tempo=tempo, miner_hotkey=result.submission.solver_hotkey)
    public_url = f"{bucket_url.rstrip('/')}/{path.parent.name}/{path.name}" if bucket_url.strip() else ""
    reveal = build_bucket_reveal(
        tempo=tempo,
        miner_hotkey=result.submission.solver_hotkey,
        drand_round=drand_round,
        commit_block=commit_block,
        commit_extrinsic_hash=commit_hash,
        blobs=(blob,),
        bucket_url=public_url,
    )
    write_bucket_reveal(path, reveal)
    return BucketPublishResult(
        path=path,
        reveal_merkle_root=reveal.merkle_root,
        commit_block=commit_block,
        commitment=commitment,
    )


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
    active_tempo = None
    if registry is None and settings.protocol_mode == "production":
        from lemma.validator import current_active_tempo, production_task_registry

        active_tempo = current_active_tempo(settings)
        registry = production_task_registry(settings, tempo=active_tempo)
    registry = registry or fetch_task_registry(settings)
    active_tasks = eligible_tasks(active_tasks_for_validation(registry, settings, tempo=active_tempo))
    if task_id:
        task = registry.get(task_id)
    elif active_tasks:
        task = active_tasks[0]
    else:
        raise ProverError("no eligible active tasks")
    try:
        active_slot_index = tuple(task.id for task in active_tasks).index(task.id)
    except ValueError:
        active_slot_index = None

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
    return MineOnceResult(
        task=task,
        submission=submission,
        verification=verification,
        active_slot_index=active_slot_index,
        active_tempo=active_tempo,
    )

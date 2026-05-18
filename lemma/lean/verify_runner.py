"""Run Lean verify locally (:class:`LeanSandbox`) or via optional HTTP worker pool."""

from __future__ import annotations

import json

import httpx
from loguru import logger

from lemma.common.config import LemmaSettings
from lemma.lean.problem_codec import problem_to_payload
from lemma.lean.sandbox import LeanSandbox, VerifyResult
from lemma.lean.submission_policy import (
    scan_submission_policy,
    submission_policy_for_problem,
    submission_policy_stderr_tail,
)
from lemma.problems.base import Problem


def lean_sandbox_from_settings(settings: LemmaSettings, verify_timeout_s: int) -> LeanSandbox:
    """Construct ``LeanSandbox`` from subnet settings (same shape as ``epoch.run_epoch``)."""
    return LeanSandbox(
        image=settings.lean_sandbox_image,
        cpu=settings.lean_sandbox_cpu,
        mem_mb=settings.lean_sandbox_mem_mb,
        timeout_s=verify_timeout_s,
        network_mode=settings.lean_sandbox_network,
        use_docker=settings.lean_use_docker,
        workspace_cache_dir=settings.lean_verify_workspace_cache_dir,
        docker_worker=settings.lemma_lean_docker_worker,
        workspace_cache_include_submission_hash=settings.lemma_lean_workspace_cache_include_submission_hash,
        workspace_cache_max_dirs=settings.lemma_lean_workspace_cache_max_dirs,
        workspace_cache_max_bytes=settings.lemma_lean_workspace_cache_max_bytes,
    )


def run_lean_verify(
    settings: LemmaSettings,
    *,
    verify_timeout_s: int,
    problem: Problem,
    proof_script: str,
    submission_policy: str | None = None,
) -> VerifyResult:
    """Verify locally or via worker; the remote path pre-scans before POST."""
    policy = submission_policy_for_problem(problem, submission_policy)
    scan = scan_submission_policy(problem, proof_script, policy=policy)
    if not scan.ok:
        return VerifyResult(
            passed=False,
            reason="policy_violation",
            stderr_tail=submission_policy_stderr_tail(scan),
        )

    base = (settings.lean_verify_remote_url or "").strip()
    if base:
        return _verify_via_http(settings, verify_timeout_s, problem, proof_script, base.rstrip("/"), policy)

    sb = lean_sandbox_from_settings(settings, verify_timeout_s)
    return _with_proof_identity(sb.verify(problem, proof_script, submission_policy=policy), proof_script)


def _with_proof_identity(result: VerifyResult, proof_script: str) -> VerifyResult:
    _ = proof_script
    return result


def _verify_via_http(
    settings: LemmaSettings,
    verify_timeout_s: int,
    problem: Problem,
    proof_script: str,
    base_url: str,
    submission_policy: str,
) -> VerifyResult:
    margin = float(settings.lean_verify_remote_timeout_margin_s)
    read_s = float(verify_timeout_s) + margin
    timeout = httpx.Timeout(connect=30.0, read=read_s, write=120.0, pool=30.0)
    payload = {
        "problem": problem_to_payload(problem),
        "proof_script": proof_script,
        "verify_timeout_s": int(verify_timeout_s),
        "submission_policy": submission_policy,
    }
    headers: dict[str, str] = {"Content-Type": "application/json"}
    tok = (settings.lean_verify_remote_bearer or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    url = f"{base_url}/verify"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        logger.warning("lean remote verify transport failed: {}", e)
        return VerifyResult(
            passed=False,
            reason="remote_error",
            stderr_tail=str(e)[:8000],
        )

    try:
        data = r.json()
    except json.JSONDecodeError:
        return VerifyResult(
            passed=False,
            reason="remote_error",
            stderr_tail=(r.text or "")[:8000],
        )

    if r.status_code == 401:
        return VerifyResult(
            passed=False,
            reason="remote_error",
            stderr_tail="remote verify: 401 unauthorized (check LEMMA_LEAN_VERIFY_REMOTE_BEARER)",
        )

    if r.status_code >= 400:
        err = data if isinstance(data, dict) else {}
        detail = err.get("detail") or err.get("message") or r.text
        return VerifyResult(
            passed=False,
            reason="remote_error",
            stderr_tail=str(detail)[:8000],
        )

    if not isinstance(data, dict):
        return VerifyResult(
            passed=False,
            reason="remote_error",
            stderr_tail="remote verify: expected JSON object body",
        )

    try:
        return _with_proof_identity(VerifyResult.model_validate(data), proof_script)
    except Exception as e:  # noqa: BLE001
        return VerifyResult(
            passed=False,
            reason="remote_error",
            stderr_tail=f"invalid VerifyResult from worker: {e}"[:8000],
        )

"""Shared task-submission verdict path for validators and miner preflight.

The validator and the ``lemma preflight`` command MUST return identical
verdicts for the same public inputs (task bundle, source checkout, and the
public solved-set). Both call into the functions here so the rejection class a
miner sees locally is exactly the class a validator records on chain-settled
work.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Container
from dataclasses import dataclass, field
from pathlib import Path

from lemma.common.config import LemmaSettings
from lemma.lean.patch_task import PatchValidationResult, validate_patch_task
from lemma.lean.rejection import RejectionClass
from lemma.lean.sandbox import VerifyResult
from lemma.submissions import LemmaSubmission
from lemma.tasks import LemmaTask


@dataclass(frozen=True)
class PreflightVerdict:
    """Canonical, miner-facing verdict for one task-bound submission."""

    accepted: bool
    rejection_class: RejectionClass
    detail: str = ""
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    stdout_tail: str = ""
    stderr_tail: str = ""


def patch_source_root(task: LemmaTask, settings: LemmaSettings) -> Path | None:
    """Resolve the clean source checkout root for a patch task."""
    if settings.source_checkout_root is not None:
        from lemma.source_checkouts import source_checkout_path

        return source_checkout_path(settings.source_checkout_root, task.source_ref)
    raw = str(task.metadata.get("source_root") or "").strip()
    if raw:
        return Path(raw)
    return None


def verify_patch_submission(
    task: LemmaTask,
    submission: LemmaSubmission,
    *,
    settings: LemmaSettings,
    source_root: Path | None = None,
    run_reproduction: bool = True,
    timeout_s: int,
) -> VerifyResult:
    """Validate a patch submission and return the score-path ``VerifyResult``."""
    root = source_root if source_root is not None else patch_source_root(task, settings)
    if root is None:
        return VerifyResult(passed=False, reason="validator_internal_error", stderr_tail="invalid_source_root")
    result = validate_patch_task(
        task,
        source_root=root,
        patch_text=submission.patch_text or "",
        run_reproduction=run_reproduction,
        timeout_s=timeout_s,
    )
    return VerifyResult(
        passed=result.accepted,
        reason=result.reason,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
    )


def verify_task_submission(
    task: LemmaTask,
    submission: LemmaSubmission,
    *,
    settings: LemmaSettings,
    source_root: Path | None = None,
    run_reproduction: bool = True,
    timeout_s: int | None = None,
) -> VerifyResult:
    """Dispatch one task-bound submission to its pinned verifier.

    This is the single shared verification primitive; the validator and the
    miner ``preflight`` command both call it so their verdicts agree.
    """
    effective_timeout = timeout_s if timeout_s is not None else settings.lean_verify_timeout_s
    if task.task_format == "patch":
        return verify_patch_submission(
            task,
            submission,
            settings=settings,
            source_root=source_root,
            run_reproduction=run_reproduction,
            timeout_s=effective_timeout,
        )
    from lemma.verifiers import get_verifier
    from lemma.verifiers.lean import verify_result_from_adapter_result

    verifier = get_verifier(task.domain_id, settings=settings)
    return verify_result_from_adapter_result(verifier.verify(task, submission))


def base_project_builds(
    task: LemmaTask,
    *,
    source_root: Path,
    timeout_s: int,
) -> bool:
    """Return whether the unpatched source builds with the task's pinned command.

    A clean base that fails to build means the task is already broken; a miner
    must not be blamed (``project_already_broken``) for that.
    """
    command = shlex.split(task.reproduction_command)
    if not command:
        return True
    from lemma.lean.patch_task import _run_reproduction_command

    with tempfile.TemporaryDirectory(prefix="lemma-base-") as tmp:
        work = Path(tmp) / "source"
        shutil.copytree(source_root, work, ignore=shutil.ignore_patterns(".git", ".lake"))
        try:
            completed = _run_reproduction_command(command, cwd=work, timeout_s=timeout_s)
        except (subprocess.TimeoutExpired, OSError):
            return False
    return completed.returncode == 0


def preflight_submission(
    task: LemmaTask,
    submission: LemmaSubmission,
    *,
    settings: LemmaSettings,
    source_root: Path | None = None,
    solved_task_ids: Container[str] = frozenset(),
    solved_proof_hashes: Container[str] = frozenset(),
    run_reproduction: bool = True,
    check_project_builds: bool = False,
    timeout_s: int | None = None,
) -> PreflightVerdict:
    """Return the canonical verdict for one task-bound submission.

    Deterministic from public inputs: the solved-set short-circuits
    (``task_already_solved`` / ``duplicate_solution``) are derived from the
    public Proof Atlas, the optional base build proves the task is not already
    broken, and verification itself runs the pinned task verifier. Any
    unexpected exception is reported as ``validator_internal_error`` rather than
    crashing the caller.
    """
    if task.id in solved_task_ids:
        return PreflightVerdict(False, "task_already_solved", detail=task.id)
    if submission.proof_sha256 and submission.proof_sha256 in solved_proof_hashes:
        return PreflightVerdict(False, "duplicate_solution", detail=submission.proof_sha256)

    effective_timeout = timeout_s if timeout_s is not None else settings.lean_verify_timeout_s

    try:
        if task.task_format == "patch":
            return _preflight_patch(
                task,
                submission,
                settings=settings,
                source_root=source_root,
                run_reproduction=run_reproduction,
                check_project_builds=check_project_builds,
                timeout_s=effective_timeout,
            )
        result = verify_task_submission(
            task,
            submission,
            settings=settings,
            source_root=source_root,
            run_reproduction=run_reproduction,
            timeout_s=effective_timeout,
        )
    except Exception as e:  # noqa: BLE001 - any internal failure is validator-side, never the miner's fault.
        return PreflightVerdict(False, "validator_internal_error", detail=type(e).__name__, stderr_tail=str(e)[:4000])

    return PreflightVerdict(
        accepted=result.passed,
        rejection_class=result.reason,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
    )


def _preflight_patch(
    task: LemmaTask,
    submission: LemmaSubmission,
    *,
    settings: LemmaSettings,
    source_root: Path | None,
    run_reproduction: bool,
    check_project_builds: bool,
    timeout_s: int,
) -> PreflightVerdict:
    root = source_root if source_root is not None else patch_source_root(task, settings)
    if root is None or not root.is_dir():
        return PreflightVerdict(False, "validator_internal_error", detail="invalid_source_root")
    if check_project_builds and not base_project_builds(task, source_root=root, timeout_s=timeout_s):
        return PreflightVerdict(False, "project_already_broken", detail=task.reproduction_command)
    result: PatchValidationResult = validate_patch_task(
        task,
        source_root=root,
        patch_text=submission.patch_text or "",
        run_reproduction=run_reproduction,
        timeout_s=timeout_s,
    )
    return PreflightVerdict(
        accepted=result.accepted,
        rejection_class=result.reason,
        detail=result.detail,
        changed_files=result.changed_files,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
    )

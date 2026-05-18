"""Task activation gates for the Lean v1 paid path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lemma.license import LicenseState, license_state_for, paid_license_allowed
from lemma.tasks import LemmaTask

ActivationStatus = Literal["paid", "curriculum", "benchmark", "quarantine", "rejected"]


@dataclass(frozen=True)
class TaskRewardEligibility:
    eligible: bool
    reason: str = ""
    license_state: LicenseState = "unknown"
    activation_status: ActivationStatus = "paid"


def activation_status_for(task: LemmaTask) -> ActivationStatus:
    raw = str(task.metadata.get("activation_status") or task.activation_status).strip().lower()
    if raw in {"paid", "curriculum", "benchmark", "quarantine", "rejected"}:
        return raw  # type: ignore[return-value]
    if task.source_stream == "benchmark_practice":
        return "benchmark"
    if task.source_stream == "trivial_curriculum" or task.triviality_status == "trivial_curriculum":
        return "curriculum"
    return "quarantine"


def task_reward_eligibility(task: LemmaTask) -> TaskRewardEligibility:
    status = activation_status_for(task)
    state = license_state_for(task.source_license, str(task.metadata.get("license_state") or ""))
    if status != "paid":
        return TaskRewardEligibility(False, f"activation_status:{status}", state, status)
    if not paid_license_allowed(state):
        return TaskRewardEligibility(False, f"license_state:{state}", state, status)
    if not task.target_sha256:
        return TaskRewardEligibility(False, "missing_target_sha256", state, status)
    if not task.source_ref.kind or not task.source_ref.name:
        return TaskRewardEligibility(False, "missing_source_ref", state, status)
    return TaskRewardEligibility(True, "", state, status)

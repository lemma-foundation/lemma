"""Useful verified row metadata.

A valid row can fail these quality gates. That distinction lets validators
store accepted proofs without claiming they are full production-quality data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lemma.license import LicenseState, paid_license_allowed

DifficultyBand = Literal["easy", "medium", "hard", "frontier"]


class RowQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    useful_verified_row: bool = False
    triviality_checked: bool = False
    baseline_solvers_run: tuple[str, ...] = ("simp", "aesop", "omega", "norm_num")
    baseline_solvers_failed: bool = False
    difficulty_band: DifficultyBand = "easy"
    near_duplicate_score: float = Field(default=0.0, ge=0.0, le=1.0)
    dependency_depth: int = Field(default=0, ge=0)
    license_state: LicenseState = "unknown"
    model_lift_release: str | None = None


def build_row_quality(
    *,
    triviality_checked: bool,
    baseline_solvers_failed: bool,
    difficulty_band: DifficultyBand,
    near_duplicate_score: float,
    dependency_depth: int,
    license_state: LicenseState,
    proof_identity_strength: str,
    model_lift_release: str | None = None,
) -> RowQuality:
    useful = (
        triviality_checked
        and baseline_solvers_failed
        and near_duplicate_score < 0.9
        and paid_license_allowed(license_state)
        and proof_identity_strength == "strong"
    )
    return RowQuality(
        useful_verified_row=useful,
        triviality_checked=triviality_checked,
        baseline_solvers_failed=baseline_solvers_failed,
        difficulty_band=difficulty_band,
        near_duplicate_score=near_duplicate_score,
        dependency_depth=dependency_depth,
        license_state=license_state,
        model_lift_release=model_lift_release,
    )

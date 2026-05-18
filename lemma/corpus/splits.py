"""Deterministic dataset splits."""

from __future__ import annotations

import hashlib
from typing import Literal

SplitName = Literal["train", "validation", "test"]


def split_for_row(row_id: str, *, validation_pct: int = 5, test_pct: int = 5) -> SplitName:
    if validation_pct < 0 or test_pct < 0 or validation_pct + test_pct >= 100:
        raise ValueError("validation_pct + test_pct must be between 0 and 99")
    bucket = int(hashlib.sha256(row_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < test_pct:
        return "test"
    if bucket < test_pct + validation_pct:
        return "validation"
    return "train"

"""Roadmap reward-class bands for active-set stratification.

The roadmap groups tasks into reward classes:

* Class 0 - canaries and calibration (zero/tiny reward);
* Class 1 - micro real sorries (low reward, onboarding);
* Class 2 - small/medium real missing proofs (the main paid workload);
* Class 3 - solved-but-not-formalized / prestige statements (medium-high).

Active epoch sets should mix paid classes and always include some Class 0
canaries. This module maps a task to its class so selection can stratify.
"""

from __future__ import annotations

from typing import Final

from lemma.tasks import LemmaTask

CANARY: Final = 0
MICRO: Final = 1
SMALL_MEDIUM: Final = 2
PRESTIGE: Final = 3

REWARD_CLASS_NAMES: Final[dict[int, str]] = {
    CANARY: "canary",
    MICRO: "micro",
    SMALL_MEDIUM: "small_medium",
    PRESTIGE: "prestige",
}

_SOURCE_VALUE_CLASS: Final[dict[str, int]] = {
    "calibration": CANARY,
    "low": MICRO,
    "medium": SMALL_MEDIUM,
    "high": PRESTIGE,
}


def task_reward_class(task: LemmaTask) -> int:
    """Return the roadmap reward class (0-3) for ``task``."""
    if task.task_class == "canary":
        return CANARY
    return _SOURCE_VALUE_CLASS.get(task.source_value, MICRO)

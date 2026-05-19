"""Chain weight allocation and submission helpers."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lemma.chain.burn_or_recycle import UnearnedAllocation

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings

_CHAIN_UID_LABEL = re.compile(r"^(?:burn|recycle)_uid:(\d+)$")


@dataclass(frozen=True)
class ChainWeightPlan:
    uids: tuple[int, ...]
    weights: tuple[float, ...]


def allocation_vector(miner_weights: dict[str, float], unearned: UnearnedAllocation) -> dict[str, float]:
    out = dict(miner_weights)
    if unearned.share:
        out[unearned.chain_label] = unearned.share
    return out


def resolve_weight_plan(weights: dict[str, float], hotkeys: Sequence[str]) -> ChainWeightPlan:
    """Resolve score labels to Bittensor UID/weight vectors."""
    hotkey_uids = {hotkey: uid for uid, hotkey in enumerate(hotkeys)}
    by_uid: dict[int, float] = {}
    for label, weight in weights.items():
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"invalid weight for {label!r}: {weight}")
        if weight == 0:
            continue
        match = _CHAIN_UID_LABEL.match(label)
        if match:
            uid = int(match.group(1))
        elif label in hotkey_uids:
            uid = hotkey_uids[label]
        else:
            raise ValueError(f"cannot resolve weight label to subnet UID: {label}")
        by_uid[uid] = by_uid.get(uid, 0.0) + weight
    total = sum(by_uid.values())
    if total <= 0:
        raise ValueError("no positive weights to submit")
    if total > 1.000001:
        raise ValueError(f"weight sum exceeds 1.0: {total}")
    ordered = tuple(sorted(by_uid.items()))
    return ChainWeightPlan(
        uids=tuple(uid for uid, _ in ordered),
        weights=tuple(weight for _, weight in ordered),
    )


def submit_bittensor_weights(settings: LemmaSettings, weights: dict[str, float]) -> bool:
    """Submit resolved weights through the pinned Bittensor client."""
    import bittensor as bt

    subtensor = bt.Subtensor(network=settings.bt_network or None)
    metagraph = subtensor.metagraph(settings.netuid, lite=True)
    plan = resolve_weight_plan(weights, tuple(metagraph.hotkeys))
    wallet = bt.Wallet(name=settings.wallet_cold, hotkey=settings.wallet_hot)
    response = subtensor.set_weights(
        wallet=wallet,
        netuid=settings.netuid,
        uids=list(plan.uids),
        weights=list(plan.weights),
        raise_error=False,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    if not response.success:
        message = response.message or response.error or "unknown set_weights failure"
        raise RuntimeError(f"set_weights failed: {message}")
    return True

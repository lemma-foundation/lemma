"""Chain weight allocation and submission helpers."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from lemma.chain.burn_or_recycle import UnearnedAllocation

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings


class _SubnetHyperparameters(Protocol):
    tempo: int


class _CommitRevealSubtensor(Protocol):
    def commit_reveal_enabled(self, *, netuid: int) -> bool: ...

    def get_current_block(self) -> int: ...

    def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> object: ...


_CHAIN_UID_LABEL = re.compile(r"^(?:burn|recycle)_uid:(\d+)$")
_LOCAL_PATH = re.compile("/" + "Users" + r"/[^\s]+")
_ROOT_LOGIN = re.compile("".join(("ro", "ot")) + r"@[^\s]+")
_IP_ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SS58_ADDRESS = re.compile(r"\b5[1-9A-HJ-NP-Za-km-z]{40,}\b")


@dataclass(frozen=True)
class ChainWeightPlan:
    uids: tuple[int, ...]
    weights: tuple[float, ...]


@dataclass(frozen=True)
class ChainWeightSubmission:
    success: bool
    uids: tuple[int, ...]
    weights: tuple[float, ...]
    message: str = ""
    extrinsic_function: str = ""
    extrinsic_hash: str = ""
    block_hash: str = ""
    block_number: int | None = None
    extrinsic_fee_rao: int | None = None


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


def _response_message(response: object) -> str:
    message = getattr(response, "message", "") or getattr(response, "error", "")
    if not message:
        return ""
    text = str(message)
    text = _LOCAL_PATH.sub("[local-path]", text)
    text = _ROOT_LOGIN.sub("[ssh-login]", text)
    text = _IP_ADDRESS.sub("[ip-address]", text)
    text = _SS58_ADDRESS.sub("[ss58-address]", text)
    return text[:300]


def _receipt_value(response: object, field: str) -> object:
    receipt = getattr(response, "extrinsic_receipt", None)
    return getattr(receipt, field, None) if receipt else None


def _receipt_int_value(response: object, field: str) -> int | None:
    value = _receipt_value(response, field)
    return value if isinstance(value, int) else None


def _wait_for_commit_reveal_window(
    subtensor: _CommitRevealSubtensor, netuid: int, *, block_time: float = 12.0
) -> None:
    if not subtensor.commit_reveal_enabled(netuid=netuid):
        return
    last_reported_blocks: int | None = None
    while True:
        block = subtensor.get_current_block()
        tempo = cast(_SubnetHyperparameters, subtensor.get_subnet_hyperparameters(netuid, block=block)).tempo
        window_start = max(0, tempo - 10)
        remaining_blocks = window_start - (block % tempo)
        if remaining_blocks <= 0:
            return
        if remaining_blocks != last_reported_blocks and (
            last_reported_blocks is None or remaining_blocks <= 10 or remaining_blocks % 10 == 0
        ):
            logger.info(
                "waiting for commit-reveal weight window netuid={} blocks_until_window={}",
                netuid,
                remaining_blocks,
            )
            last_reported_blocks = remaining_blocks
        time.sleep(min(block_time, remaining_blocks * block_time))


def submit_bittensor_weights(settings: LemmaSettings, weights: dict[str, float]) -> ChainWeightSubmission:
    """Submit resolved weights through the pinned Bittensor client."""
    import bittensor as bt

    subtensor = bt.Subtensor(network=settings.bt_network or None)
    metagraph = subtensor.metagraph(settings.netuid, lite=True)
    plan = resolve_weight_plan(weights, tuple(metagraph.hotkeys))
    wallet = bt.Wallet(name=settings.wallet_cold, hotkey=settings.wallet_hot)
    _wait_for_commit_reveal_window(subtensor, settings.netuid)
    response = subtensor.set_weights(
        wallet=wallet,
        netuid=settings.netuid,
        uids=list(plan.uids),
        weights=list(plan.weights),
        raise_error=False,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    return ChainWeightSubmission(
        success=bool(response.success),
        uids=plan.uids,
        weights=plan.weights,
        message=_response_message(response),
        extrinsic_function=str(response.extrinsic_function or ""),
        extrinsic_hash=str(_receipt_value(response, "extrinsic_hash") or ""),
        block_hash=str(_receipt_value(response, "block_hash") or ""),
        block_number=_receipt_int_value(response, "block_number"),
        extrinsic_fee_rao=getattr(response.extrinsic_fee, "rao", None) if response.extrinsic_fee else None,
    )

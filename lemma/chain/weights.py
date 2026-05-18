"""Chain weight allocation helpers."""

from __future__ import annotations

from lemma.chain.burn_or_recycle import UnearnedAllocation


def allocation_vector(miner_weights: dict[str, float], unearned: UnearnedAllocation) -> dict[str, float]:
    out = dict(miner_weights)
    if unearned.share:
        out[unearned.chain_label] = unearned.share
    return out

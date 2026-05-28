"""Public receipts for procedural source-pool sampling."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from typing import Any

SOURCE_POOL_RECEIPT_VERSION = "lemma-source-pool-receipt-v1"
SOURCE_SAMPLING_VERSION = "lemma-source-sampling-v4"


def source_sampling_policy(
    *,
    citation_alpha: float,
    citation_weight_cap: float,
    citation_window_tempos: int,
) -> dict[str, int | str]:
    alpha = min(1.0, max(0.0, float(citation_alpha)))
    cap = max(1.0, float(citation_weight_cap))
    window = max(1, int(citation_window_tempos))
    return {
        "sampling_version": SOURCE_SAMPLING_VERSION,
        "citation_alpha_basis_points": round(alpha * 10_000),
        "citation_weight_cap_micros": round(cap * 1_000_000),
        "citation_window_tempos": window,
    }


def sampling_alpha(policy: dict[str, Any]) -> float:
    return int(policy["citation_alpha_basis_points"]) / 10_000


def sampling_weight_cap(policy: dict[str, Any]) -> float:
    return int(policy["citation_weight_cap_micros"]) / 1_000_000


def source_pool_receipt(
    sources: Iterable[Any],
    *,
    source_pool_sha256: str,
    citation_alpha: float,
    citation_weight_cap: float,
    citation_window_tempos: int,
) -> dict[str, object]:
    streams = Counter(str(getattr(source, "source_stream", "")) for source in sources)
    return {
        "version": SOURCE_POOL_RECEIPT_VERSION,
        "source_pool_sha256": source_pool_sha256,
        "source_count": sum(streams.values()),
        "source_stream_counts": dict(sorted(streams.items())),
        **source_sampling_policy(
            citation_alpha=citation_alpha,
            citation_weight_cap=citation_weight_cap,
            citation_window_tempos=citation_window_tempos,
        ),
    }


def source_pool_receipt_sha256(receipt: dict[str, object]) -> str:
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()

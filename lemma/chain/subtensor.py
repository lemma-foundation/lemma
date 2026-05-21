"""Small Bittensor connection helper."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings


def _is_transient_connect_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return "HTTP 429" in text or "Too Many Requests" in text or "Timeout" in text or "timed out" in text


def connect_subtensor(settings: LemmaSettings, *, attempts: int = 6) -> Any:
    """Connect to Bittensor, retrying short-lived endpoint throttles."""
    import bittensor as bt

    for attempt in range(1, attempts + 1):
        try:
            return bt.Subtensor(network=settings.bt_network or None)
        except Exception as exc:
            if attempt >= attempts or not _is_transient_connect_error(exc):
                raise
            delay = min(30.0, 5.0 * attempt)
            logger.warning("chain endpoint throttled while connecting; retrying in {:.0f}s", delay)
            time.sleep(delay)
    raise RuntimeError("unreachable subtensor connection retry state")

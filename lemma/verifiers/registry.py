"""Verifier-domain registry."""

from __future__ import annotations

import os
from typing import Any

from lemma.common.config import LemmaSettings
from lemma.verifiers.base import VerifierAdapter
from lemma.verifiers.lean import LeanVerifierAdapter
from lemma.verifiers.verus import VerusVerifierAdapter

VERIFIERS: dict[str, type[VerifierAdapter]] = {
    "lean": LeanVerifierAdapter,
}


def get_verifier(domain_id: str, *, settings: LemmaSettings | None = None, **kwargs: Any) -> VerifierAdapter:
    domain = domain_id.strip().lower()
    cfg = settings or LemmaSettings()
    if domain == "verus":
        enabled = cfg.enable_experimental_verus or os.environ.get("LEMMA_ENABLE_EXPERIMENTAL_VERUS", "") == "1"
        if not enabled:
            raise ValueError("Verus domain is experimental and disabled by default")
        return VerusVerifierAdapter(settings=cfg, **kwargs)
    adapter_cls = VERIFIERS.get(domain)
    if adapter_cls is None:
        raise ValueError(f"Unknown domain_id: {domain_id}")
    if domain not in set(cfg.enabled_domains):
        raise ValueError(f"Domain is not enabled: {domain_id}")
    return adapter_cls(settings=cfg, **kwargs)

"""Production-mode protocol invariants."""

from __future__ import annotations

from lemma.common.config import LemmaSettings
from lemma.tasks import TaskRegistry


def enforce_production_invariants(settings: LemmaSettings, registry: TaskRegistry) -> None:
    """Fail closed when production mode lacks the Lean v1 safety boundary."""

    if settings.protocol_mode != "production":
        return
    if tuple(settings.enabled_domains) != ("lean",):
        raise RuntimeError("production mode is Lean-only: LEMMA_ENABLED_DOMAINS must be lean")
    if not settings.task_registry_sha256_expected:
        raise RuntimeError("production mode requires LEMMA_TASK_REGISTRY_SHA256_EXPECTED")
    if registry.signature_status != "verified":
        raise RuntimeError("production mode requires signature-verified registry bytes")
    if settings.lean_sandbox_network.strip().lower() not in {"none", "no"}:
        raise RuntimeError("production mode requires network-disabled verifier runs")

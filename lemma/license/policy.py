"""Small, explicit license gate for paid Lean tasks."""

from __future__ import annotations

from typing import Literal

LicenseState = Literal[
    "clean_open",
    "attribution_required",
    "research_only",
    "unknown",
    "restricted",
    "rejected",
]

_CLEAN_OPEN = {
    "apache-2.0",
    "mit",
    "bsd-2-clause",
    "bsd-3-clause",
    "isc",
    "public-domain",
    "unlicense",
}
_ATTRIBUTION_REQUIRED = {"cc-by-4.0", "cc-by-3.0"}


def license_state_for(source_license: str | None, explicit_state: str | None = None) -> LicenseState:
    """Classify source license metadata into activation states.

    This is intentionally conservative. Unknown or restrictive provenance can
    still be stored for review, but it is not paid or exported as clean data.
    """

    explicit = (explicit_state or "").strip().lower().replace(" ", "_").replace("-", "_")
    if explicit in {
        "clean_open",
        "attribution_required",
        "research_only",
        "unknown",
        "restricted",
        "rejected",
    }:
        return explicit  # type: ignore[return-value]

    raw = (source_license or "").strip().lower()
    if not raw or raw in {"unknown", "n/a", "none"}:
        return "unknown"
    if "reject" in raw:
        return "rejected"
    if any(part in raw for part in ("noncommercial", "non-commercial", "cc-by-nc", "proprietary", "no-redistribution")):
        return "restricted"
    if "research" in raw:
        return "research_only"
    if raw in _ATTRIBUTION_REQUIRED:
        return "attribution_required"
    if raw in _CLEAN_OPEN:
        return "clean_open"
    return "unknown"


def paid_license_allowed(state: LicenseState) -> bool:
    """Return whether a task with this license state can enter paid activation."""

    return state in {"clean_open", "attribution_required"}

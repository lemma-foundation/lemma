"""Lean verifier axiom and environment-output helpers."""

from __future__ import annotations

import hashlib
import re

ALLOWED_AXIOMS = frozenset({"propext", "Quot.sound", "Classical.choice"})


def parse_axioms_from_lean_output(text: str) -> set[str] | None:
    """
    Parse ``#print axioms`` line from ``lake env lean AxiomCheck.lean`` output.

    Expected shape contains ``depends on axioms: [a, b, c]`` (Lean 4 pretty-print).
    Pure ``rfl`` / definitional proofs may print ``does not depend on any axioms`` instead.
    """
    matches = re.findall(r"depends on axioms:\s*\[([^\]]*)\]", text, re.IGNORECASE | re.DOTALL)
    if not matches:
        low = text.lower()
        if "does not depend on any axioms" in low:
            return set()
        return None
    out: set[str] = set()
    for inner in matches:
        out.update(p.strip().strip("`") for p in inner.split(",") if p.strip())
    return out


def lean_driver_failed(lean_output: str) -> bool:
    """True if Lean/lake failed before a usable ``#print axioms`` line."""
    t = lean_output.lower()
    return (
        "error (" in t
        or "unknown identifier" in t
        or "unknown constant" in t
        or "invalid field" in t
        or "error:" in t
        or "build failed" in t
        or "failed to build" in t
    )


def lake_build_environment_failed(lean_output: str) -> bool:
    """True when lake/git failed for network or tooling, not a rejected proof or axiom issue."""
    t = lean_output.lower()
    return (
        "could not resolve host" in t
        or "couldn't resolve host" in t
        or ("git" in t and "exit code 128" in t)
        or "network is unreachable" in t
        or "failed to download" in t
        or "tls handshake" in t
    )


def axiom_scan_ok(lean_output: str) -> tuple[bool, set[str] | None]:
    """True iff parsed axiom set is a subset of ALLOWED_AXIOMS (empty allowed)."""
    found = parse_axioms_from_lean_output(lean_output)
    if found is None:
        return False, None
    if not found.issubset(ALLOWED_AXIOMS):
        return False, found
    return True, found


def structural_fingerprint_from_lean_output(lean_output: str) -> str | None:
    """Hash Lean-printed declaration output emitted by `AxiomCheck.lean`."""
    blocks: list[str] = []
    active: list[str] | None = None
    for line in lean_output.splitlines():
        text = line.strip()
        if text.startswith("LEMMA_DECL_FINGERPRINT_START "):
            active = []
            continue
        if text.startswith("LEMMA_DECL_FINGERPRINT_END "):
            if active is not None:
                blocks.append("\n".join(active).strip())
            active = None
            continue
        if active is not None:
            active.append(line.rstrip())
    payload = "\n\x1e\n".join(block for block in blocks if block)
    if not payload:
        return None
    normalized = re.sub(r"\s+", " ", payload.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

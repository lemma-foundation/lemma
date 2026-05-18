"""Proof identity helpers for duplicate-proof scoring.

The production target is a Lean-derived canonical proof-term hash. Until that
extractor is wired in, the validator keeps a clearly labelled script-hash
fallback so downstream data does not confuse it for structural identity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ProofIdentity:
    value: str
    source: str
    proof_term_hash: str | None


def proof_identity(*, proof_sha256: str, proof_term_hash: str | None = None) -> ProofIdentity:
    term = (proof_term_hash or "").strip() or None
    if term:
        return ProofIdentity(value=term, source="lean_proof_term", proof_term_hash=term)
    return ProofIdentity(value=proof_sha256, source="proof_sha256_fallback", proof_term_hash=None)


def canonical_proof_term_hash(proof_script: str) -> str:
    """Return a deterministic source-normalized proof identity fallback."""
    normalized = _SPACE_RE.sub(" ", proof_script.strip())
    return hashlib.sha256(normalized.encode()).hexdigest()

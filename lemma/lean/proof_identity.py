"""Proof identity helpers for duplicate-proof scoring."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

_SPACE_RE = re.compile(r"\s+")

ProofIdentitySource = Literal[
    "script_sha256",
    "normalized_script_sha256",
    "proof_term_hash",
    "normalized_proof_term_hash",
    "structural_fingerprint",
]
ProofIdentityStrength = Literal["weak", "medium", "strong"]


@dataclass(frozen=True)
class ProofIdentity:
    value: str
    source: ProofIdentitySource
    strength: ProofIdentityStrength
    proof_term_hash: str | None


def identity_strength(source: str) -> ProofIdentityStrength:
    if source in {"proof_term_hash", "normalized_proof_term_hash", "structural_fingerprint"}:
        return "strong"
    return "weak"


def proof_identity(
    *,
    proof_sha256: str,
    proof_term_hash: str | None = None,
    structural_fingerprint: str | None = None,
    proof_script: str | None = None,
) -> ProofIdentity:
    term = (proof_term_hash or "").strip() or None
    if term:
        return ProofIdentity(value=term, source="proof_term_hash", strength="strong", proof_term_hash=term)
    structural = (structural_fingerprint or "").strip() or None
    if structural:
        return ProofIdentity(
            value=structural,
            source="structural_fingerprint",
            strength="strong",
            proof_term_hash=None,
        )
    if proof_script is not None:
        value = normalized_script_sha256(proof_script)
        return ProofIdentity(value=value, source="normalized_script_sha256", strength="weak", proof_term_hash=None)
    return ProofIdentity(value=proof_sha256, source="script_sha256", strength="weak", proof_term_hash=None)


def full_reward_eligible(strength: str) -> bool:
    return strength == "strong"


def normalize_proof_script(proof_script: str) -> str:
    return _SPACE_RE.sub(" ", proof_script.strip())


def normalized_script_sha256(proof_script: str) -> str:
    return hashlib.sha256(normalize_proof_script(proof_script).encode()).hexdigest()


def canonical_proof_term_hash(proof_script: str) -> str:
    """Compatibility alias for the normalized-script fallback.

    This is not a Lean proof-term hash and must not be labelled as one.
    """

    return normalized_script_sha256(proof_script)

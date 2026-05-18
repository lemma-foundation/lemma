"""Verifier-domain adapters."""

from lemma.verifiers.base import VerificationResult, VerifierAdapter
from lemma.verifiers.registry import get_verifier

__all__ = ["VerificationResult", "VerifierAdapter", "get_verifier"]

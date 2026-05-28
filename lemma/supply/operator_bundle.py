"""Chain-pinned procedural mutation bundle metadata."""

from __future__ import annotations

import hashlib
import json

OPERATOR_BUNDLE_VERSION = "lemma-procedural-depth2-v19"
OPERATOR_NAMES = ("symm", "specialize")
MUTATION_ENGINE = "structural_reversible_v3"
TYPE_SUBSTITUTIONS = (
    ("Complex.re", "Complex.im"),
    ("Complex.im", "Complex.re"),
)
SMALL_VALUES_BY_TYPE = {
    "Nat": ("1", "2"),
    "\u2115": ("1", "2"),
    "Int": ("1", "-1"),
    "\u2124": ("1", "-1"),
    "Rat": ("1",),
    "\u211A": ("1",),
    "Real": ("1",),
    "\u211D": ("1",),
    "Bool": ("true", "false"),
}


def procedural_operator_bundle_hash() -> str:
    payload = {
        "version": OPERATOR_BUNDLE_VERSION,
        "mutation_engine": MUTATION_ENGINE,
        "operators": OPERATOR_NAMES,
        "chain_depth": 2,
        "small_values_by_type": SMALL_VALUES_BY_TYPE,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()

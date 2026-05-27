"""Chain-pinned procedural mutation bundle metadata."""

from __future__ import annotations

import hashlib
import json

OPERATOR_BUNDLE_VERSION = "lemma-procedural-depth2-v15"
OPERATOR_NAMES = ("symm", "generalize")
MUTATION_ENGINE = "structural_reversible_v2"
TYPE_SUBSTITUTIONS = (
    ("Complex.re", "Complex.im"),
    ("Complex.im", "Complex.re"),
)
SMALL_VALUES_BY_TYPE = {
    "Nat": ("0", "1", "2", "Nat.zero", "Nat.succ Nat.zero"),
    "\u2115": ("0", "1", "2", "Nat.zero", "Nat.succ Nat.zero"),
    "Int": ("0", "1", "-1"),
    "\u2124": ("0", "1", "-1"),
    "Rat": ("0", "1"),
    "\u211A": ("0", "1"),
    "Real": ("0", "1"),
    "\u211D": ("0", "1"),
    "Bool": ("true", "false"),
    "Prop": ("True", "False"),
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

"""Chain-pinned procedural mutation bundle metadata."""

from __future__ import annotations

import hashlib
import json

OPERATOR_BUNDLE_VERSION = "lemma-procedural-depth2-v2"
OPERATOR_NAMES = ("generalize", "specialize", "conjoin", "substitute-type", "strengthen", "weaken")
TYPE_SUBSTITUTIONS = (
    ("Nat", "Int"),
    ("Int", "Rat"),
    ("Rat", "Real"),
)
SMALL_VALUES_BY_TYPE = {
    "Nat": ("0", "1", "2", "Nat.zero", "Nat.succ Nat.zero"),
    "Int": ("0", "1", "-1"),
    "Rat": ("0", "1"),
    "Real": ("0", "1"),
    "Bool": ("true", "false"),
    "Prop": ("True", "False"),
}


def procedural_operator_bundle_hash() -> str:
    payload = {
        "version": OPERATOR_BUNDLE_VERSION,
        "operators": OPERATOR_NAMES,
        "chain_depth": 2,
        "type_substitutions": TYPE_SUBSTITUTIONS,
        "small_values_by_type": SMALL_VALUES_BY_TYPE,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()

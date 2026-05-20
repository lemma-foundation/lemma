# Proof Identity

Proof identity decides whether two accepted proofs are the same rewardable artifact.

Identity levels:

- `script_sha256`: weak script hash.
- `normalized_script_sha256`: weak normalized-script fallback.
- `proof_term_hash`: strong Lean-derived identity.
- `normalized_proof_term_hash`: strong canonical Lean identity.
- `structural_fingerprint`: strong theorem/dependency/proof fingerprint.

Weak identities can be stored and exported. They are not full production-reward identities. Production reward requires `proof_identity_strength: strong`.

The verifier path must not label a normalized script hash as a proof-term hash. The current strong fallback is a Lean-derived `structural_fingerprint` built from Lean's printed declaration output after the submitted proof verifies. If neither a proof-term hash nor a structural fingerprint is available, rows are marked with `proof_identity_source: normalized_script_sha256` and `proof_identity_strength: weak`.

Production rewards require strong identity. Weak script identity can remain in corpus metadata for replay and review, but it is not paid mainnet work.

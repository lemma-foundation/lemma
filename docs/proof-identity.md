# Proof Identity

Proof identity decides whether two accepted proofs are the same rewardable artifact.

Identity levels:

- `script_sha256`: weak script hash.
- `normalized_script_sha256`: weak normalized-script fallback.
- `proof_term_hash`: strong Lean-derived identity from the kernel proof expression.
- `normalized_proof_term_hash`: strong canonical Lean identity.
- `structural_fingerprint`: medium Lean print receipt retained for diagnostics and replay.

Weak identities can be stored and exported. They are not full production-reward identities. Production reward requires `proof_identity_strength: strong`.

The verifier path must not label a normalized script hash as a proof-term hash. Strong paid identity comes from the Lean-emitted kernel proof expression hash after the submitted proof verifies. Corpus rows keep named declaration-fingerprint receipts in metadata when Lean emits them, but those print receipts are not strong paid identity. If no Lean proof-term hash is available, rows are marked below strong identity and cannot earn production rewards.

Production rewards require strong identity. Weak script identity can remain in corpus metadata for replay and review, but it is not paid mainnet work.

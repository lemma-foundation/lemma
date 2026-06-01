# Lean Domain

Lean theorem proving is the active Lemma path.

## Why Lean First

- deterministic verifier;
- mature Mathlib ecosystem;
- clean accepted theorem/proof rows;
- useful records for theorem provers, proof search, and mathematical retrieval.

## Runtime

- `domain_id`: `lean`
- `verifier_id`: `lake-build`
- `verifier_version`: `lemma-lean-v1`
- active adapter: `lemma.verifiers.lean.LeanVerifierAdapter`

Validators run the pinned Lean environment through Docker, a configured Lean worker, or explicitly allowed host Lean for local debugging.

## Proof Atlas Artifact

```json
{
  "proof": "string",
  "imports": ["Mathlib"],
  "full_file": "string",
  "proof_sha256": "sha256",
  "proof_term_hash": "string"
}
```

# Lean Domain

Lean theorem proving is the first production Lemma domain.

## Why Lean First

- deterministic verifier;
- mature Mathlib ecosystem;
- clean theorem/proof corpus rows;
- direct link to AlphaProof-style training loops;
- useful data for theorem provers and broader reasoning models.

## Runtime

- `domain_id`: `lean`
- `verifier_id`: `lake-build`
- `verifier_version`: `lemma-lean-v1`
- active adapter: `lemma.verifiers.lean.LeanVerifierAdapter`

Validators run the pinned Lean environment through Docker, a configured Lean worker, or explicitly allowed host Lean for local debugging.

## Corpus Artifact

```json
{
  "proof": "string",
  "imports": ["Mathlib"],
  "full_file": "string",
  "proof_sha256": "sha256",
  "proof_term_hash": "string"
}
```

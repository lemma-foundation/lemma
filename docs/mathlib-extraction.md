# Mathlib Extraction Contract

Mathlib extraction is an off-chain source-preparation step. Validators consume pinned task registries; they do not crawl Mathlib, run source extraction, or trust extracted proofs during scoring.

The launch path is:

```text
public Lean source row -> proof-erased task row -> pinned registry -> validator -> accepted proof export
```

## Source Rows

A source row describes a public theorem target with proof material erased:

```json
{"theorem_name":"Nat.zero_add","type_expr":"∀ n : Nat, 0 + n = n","imports":["Mathlib.Data.Nat.Basic"],"mathlib_rev":"<mathlib-commit>","source_path":"Mathlib/Data/Nat/Basic.lean","source_line":12,"source_license":"Apache-2.0","proof_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","queue_depth":0}
```

Required fields:

- `theorem_name`: ASCII dotted Lean identifier used for the task theorem.
- `type_expr`: Lean theorem type.
- `mathlib_rev`: pinned Mathlib revision string.
- `source_path`: repo-relative `.lean` path inside the public checkout.
- `source_license`: source license for the row.

Optional fields:

- `imports`: Lean modules needed by the target. Defaults to `["Mathlib"]`.
- `source_line`: 1-based line in the source file.
- `proof_sha256`: 64-hex hash of erased source proof material, kept only as provenance metadata.
- `queue_depth`: nonnegative difficulty/frontier bucket. Defaults to `0`.
- `topic` / `subtopic`: deterministic topic labels from the source path.
- `difficulty_score`: deterministic classifier score used to assign `queue_depth`.
- `direct_dependency_count`, `dependency_depth`, `transitive_dependency_hash`: source hints for local analysis. Rewarded production slot weights are recomputed from the accepted proof's verifier-recorded Lean kernel dependencies.
- `baseline_solved`: whether an operator baseline tactic stack solved the task before paid activation.

## Proof Erasure

The source row must not carry a proof script into the validator path. The registry row exposes the missing proof as a `sorry` target and a submission stub:

```lean
theorem Nat.zero_add : ∀ n : Nat, 0 + n = n := by
  sorry
```

`proof_sha256` is provenance, not proof identity. Rewarded submissions are identified from the miner artifact checked by the validator.

## Registry Boundary

Production validators read a pinned registry and validate task-bound submissions against the active deterministic K-slot window. They reject rows outside the active window, mismatched task versions, mismatched target hashes, duplicate winning proofs, and policy failures.

Externally produced `signed_by` and `signature` metadata can be attached to a registry, but signatures do not replace the SHA pin. Production operators should set `LEMMA_TASK_REGISTRY_SHA256_EXPECTED`, and set `LEMMA_VERIFY_REGISTRY_SIGNATURES=1` when they expect signed registry distribution.

Solved active slots earn their deterministic active slot share. Unsolved-slot value is not redistributed to current solvers; the production default routes it to burn.

## Fixtures

The dev seed in `tasks/registry.json` and the tiny fixture in [examples/operator-smoke](../examples/operator-smoke/README.md) are safe for local smoke tests. They are not production Mathlib source snapshots.

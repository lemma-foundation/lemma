# Mathlib Extraction Contract

Mathlib extraction is an off-chain supply step. Validators consume pinned task artifacts; they do not crawl Mathlib, run auto-formalization, or trust extracted proofs during scoring.

The intended path is:

```text
pinned Mathlib checkout -> proof-erased snapshot JSONL -> pinned task registry -> validator -> corpus export
```

## Snapshot Rows

Each JSONL row describes one theorem statement from a pinned Mathlib checkout:

```json
{"theorem_name":"Nat.zero_add","type_expr":"∀ n : Nat, 0 + n = n","imports":["Mathlib.Data.Nat.Basic"],"mathlib_rev":"<mathlib-commit>","source_path":"Mathlib/Data/Nat/Basic.lean","source_line":12,"source_license":"Apache-2.0","proof_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","queue_depth":0}
```

Required fields:

- `theorem_name`: ASCII dotted Lean identifier used for the generated task theorem.
- `type_expr`: Lean theorem type.
- `mathlib_rev`: pinned Mathlib revision string. Production snapshots should use the exact commit.
- `source_path`: repo-relative `.lean` path inside the Mathlib checkout.
- `source_license`: source license for the row.

Optional fields:

- `imports`: Lean modules needed by the target. Defaults to `["Mathlib"]`.
- `source_line`: 1-based line in the source file.
- `proof_sha256`: 64-hex hash of the erased source proof, kept only as provenance metadata.
- `queue_depth`: non-negative difficulty/frontier bucket. Defaults to `0`.

The public importer currently accepts only ASCII dotted theorem and import names. That keeps task ids, generated Lean files, and corpus replay stable while the production extractor is still a separate audited tool.

## Proof Erasure

The snapshot row must not carry a proof script into the validator path. The importer turns each row into a `sorry` target and a submission stub:

```lean
theorem Nat.zero_add : ∀ n : Nat, 0 + n = n := by
  sorry
```

`proof_sha256` is provenance, not proof identity. Rewarded submissions are identified from the miner artifact checked by the validator.

## Registry Build

Build a deterministic registry from a snapshot:

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input snapshot.jsonl \
  --output tasks/mathlib-snapshot.registry.json
```

The builder validates each row, orders shallow tasks before deeper tasks, writes deterministic `queue_position` values, and prints `registry_sha256`. Operators should pin the registry bytes and expected SHA256 before validation.

Externally produced `signed_by` and `signature` metadata can be attached during registry build, but this command does not perform production signing or verification. Validators must still pin `registry_sha256`.

## Validator Boundary

The validator reads the pinned registry and validates task-bound submissions against the active deterministic K-slot window. It rejects rows outside the active window, mismatched task versions, mismatched target hashes, duplicate winning proofs, and policy failures.

Solved active slots earn `credit / K`. Unsolved-slot value is not redistributed to current solvers; the production default routes it to burn.

## Fixtures

The tiny fixture in [examples/operator-smoke](../examples/operator-smoke/README.md) follows this contract and is safe for local smoke tests. It is not a production Mathlib snapshot.

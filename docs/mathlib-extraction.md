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
- `topic` / `subtopic`: deterministic topic labels from the Mathlib source path.
- `difficulty_score`: deterministic classifier score used to assign `queue_depth`.
- `citation_weight`: pinned dependency in-degree or capped sampling weight for procedural source selection.
- `direct_dependency_count`, `dependency_depth`, `transitive_dependency_hash`: pinned dependency-graph fields used by deterministic paid-slot weight receipts.
- `baseline_solved`: whether an operator baseline tactic stack solved the task before paid activation.

The public importer currently accepts only ASCII dotted theorem and import names. That keeps task ids, generated Lean files, and corpus replay stable while the production extractor is still a separate audited tool.

## Proof Erasure

The snapshot row must not carry a proof script into the validator path. The importer turns each row into a `sorry` target and a submission stub:

```lean
theorem Nat.zero_add : ∀ n : Nat, 0 + n = n := by
  sorry
```

`proof_sha256` is provenance, not proof identity. Rewarded submissions are identified from the miner artifact checked by the validator.

## Extraction

Extract snapshot rows from a pinned Mathlib checkout:

```bash
uv run lemma tasks extract-mathlib-snapshot \
  --mathlib-root /path/to/mathlib \
  --lake-root /path/to/lake-project \
  --elaborate-types \
  --include 'Mathlib/Data/Nat/*.lean' \
  --depth0-limit 10 \
  --depth1-limit 20 \
  --depth2-limit 20 \
  --output snapshot.jsonl
```

The extractor reads theorem and lemma declarations, erases proofs to hashes, derives topic labels from source paths, and assigns `queue_depth` from statement shape, import topic, and proof-block span. Use `--elaborate-types` for live batches so Lean `#check` output supplies self-contained theorem types instead of relying on source text that may reference file-local variables. It is an off-chain operator tool. Validators still consume only pinned snapshot and registry artifacts.

## Registry Build

Build a deterministic registry from a snapshot:

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input snapshot.jsonl \
  --output tasks/mathlib-snapshot.registry.json
```

The builder validates each row, orders shallow tasks before deeper tasks, writes deterministic `queue_position` values, and prints `registry_sha256`. This is useful for local smoke tests and cache artifacts.

Externally produced `signed_by` and `signature` metadata can be attached during registry build, but signatures do not make a registry production-authoritative. Production validators use procedural supply mode with a pinned source-pool hash.

## Validator Boundary

In dev registry mode, the validator reads the pinned registry and validates task-bound submissions against the active deterministic K-slot window. In production mode, it rebuilds procedural tasks from the pinned source pool plus epoch randomness first. It rejects rows outside the active window, mismatched task versions, mismatched target hashes, duplicate winning proofs, and policy failures.

Solved active slots earn their deterministic active slot share. Unsolved-slot value is not redistributed to current solvers; the production default routes it to burn.

## Fixtures

The tiny fixture in [examples/operator-smoke](../examples/operator-smoke/README.md) follows this contract and is safe for local smoke tests. It is not a production Mathlib snapshot.

# Corpus

The Lemma Corpus is the main product of the subnet: replayable Lean theorem/proof rows that validators accepted.

## Purpose

Corpus rows should be useful for supervised fine-tuning, retrieval, proof repair, reinforcement learning, and evaluation. A row is valuable only if another operator can reconstruct the task and rerun the checker.

## Schema

The launch schema is `spec/corpus-row.schema.json` with `schema_version: 1`.

Required fields:

```text
schema_version
row_id
task_id
task_version
theorem_name
type_expr
statement
imports
lean_toolchain
mathlib_rev
policy
target_sha256
source_stream
source_ref
source_license
proof_script
proof_sha256
proof_term_hash
axiom_set
solver_hotkey
validator_hotkey
epoch
tempo
accepted_at
rewarded
verification
metadata
```

`row_id` is the SHA256 of `target_sha256`, `proof_sha256`, `solver_hotkey`, and `validator_hotkey`. `proof_term_hash` is nullable at launch. `rewarded` is true only for proofs that received epoch credit. Valid alternates can be stored with `rewarded: false`.

Failed proofs are not public corpus rows.

## Replay

```bash
uv run lemma corpus validate corpus.jsonl
uv run lemma corpus replay corpus.jsonl
uv run lemma corpus export --input corpus --output corpus/corpus-index.json
```

Replay uses the task fields embedded in each row, the pinned toolchain metadata, and the Lean verifier.

## Licensing

Every task and row carries `source_license`. The default dev seed uses `CC-BY-4.0`; imported sources must be checked before activation.

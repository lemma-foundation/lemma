# Corpus

The Lemma Corpus is the main product of the subnet: replayable Lean theorem/proof rows that validators accepted.

## Purpose

Corpus rows should be useful for supervised fine-tuning, retrieval, proof repair, reinforcement learning, and evaluation. A row is valuable only if another operator can reconstruct the task and rerun the checker.

## Schema

The launch schema is `spec/corpus-row.schema.json` with `schema_version: 1`.

Core fields:

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
proof_identity
proof_identity_source
solver_hotkey
validator_hotkey
rewarded
verification
```

Queue and difficulty metadata can include:

```text
active_K
queue_position
queue_depth
frontier_depth
ema_solve_rate
```

`row_id` is the SHA256 of `target_sha256`, `proof_sha256`, `solver_hotkey`, and `validator_hotkey`. `proof_sha256` is the script hash. `proof_term_hash` is filled when the Lean proof-term extractor provides it. Until then, `proof_identity_source` must make any fallback explicit.

Failed proofs are not public corpus rows. Valid alternates can be stored with `rewarded: false`.

## Replay

```bash
uv run lemma corpus validate corpus.jsonl
uv run lemma corpus replay corpus.jsonl
uv run lemma corpus export --input corpus --output corpus/corpus-index.json
uv run lemma corpus benchmark-export --input corpus --output exports/lemma-proofs.jsonl --index exports/index.json
```

Replay uses the task fields embedded in each row, the pinned toolchain metadata, and the Lean verifier.

`benchmark-export` writes compact JSONL records for downstream training or evaluation jobs. Each record contains task metadata, source/license metadata, proof text and hashes, reward context, verification summary, and public provenance. It is an export surface, not a claim that the rows are held-out benchmark tasks.

## Licensing

Every task and row carries `source_license`. The default dev seed uses `CC-BY-4.0`; imported sources must be checked before activation.

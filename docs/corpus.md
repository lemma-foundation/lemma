# Machine-Verified Mathematics Corpus

The Lemma Corpus is the main product of the network: replayable Lean theorem/proof records that validators accepted.

A corpus row is a replayable record of a verified mathematical proof.

The public smoke corpus is published at [lemma-foundation/lemma-corpus](https://github.com/lemma-foundation/lemma-corpus).

## Publishing Snapshots

The current public storage shape is:

- Content-addressed canonical artifacts under `canonical/<netuid>/`.
- Hippius S3/Arion as the current byte resolver until Hippius IPFS pinning is available in the public toolchain.
- GitHub immutable releases as the public mirror.
- `MANIFEST.sha256` and `canonical/<netuid>/storage-index.json` as the hash checklist for each timestamped snapshot.

Keep Hippius writes append-only in practice: publish a new `snapshots/<timestamp>/` prefix and do not sync with `--delete`.

The publisher builds one deterministic directory per accepted epoch:

```text
canonical/sn467/tempos/tempo-000001/
  entries/
  manifest.json
canonical/sn467/commitments/tempo-000001.json
```

`manifest.json` records per-entry SHA256 hashes and the accepted-entry Merkle root. `commitments/tempo-*.json` records the payload shape that should later be committed on chain. Today the resolver label defaults to `hippius-s3-arion`; the directory shape is CID-ready, so the resolver can move to Hippius IPFS without changing the corpus entry bytes.

From the Lemma repo, publish a prepared `lemma-corpus` checkout with:

```bash
uv run python scripts/publish_corpus_snapshot.py --repo ~/lemma-corpus --netuid sn467
```

For a no-upload preview:

```bash
uv run python scripts/publish_corpus_snapshot.py --repo ~/lemma-corpus --netuid sn467 --dry-run
```

The script expects Hippius credentials in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`; it does not store them. It uses `https://s3.hippius.com`, region `decentralized`, bucket `lemma-corpus-sn467`, GitHub repo `lemma-foundation/lemma-corpus`, and resolver label `hippius-s3-arion` by default.

## Purpose

Corpus rows should be useful for theorem-prover training, retrieval, repair loops, reinforcement learning, and evaluation. A row is valuable only if another operator can reconstruct the task and rerun the pinned Lean verifier.

## Simple Example

```json
{
  "task_id": "lemma.sample.true_intro",
  "domain_id": "lean",
  "proof_script": "by trivial",
  "verification": {
    "passed": true,
    "verifier_version": "lemma-lean-v1"
  },
  "source_license": "CC-BY-4.0",
  "rewarded": true
}
```

The full row carries more replay and attribution metadata, but the meaning is simple: this theorem task had a proof, the Lean verifier accepted it, and the row can be reused as machine-verified mathematics.

## Schema

The compatibility schema is `spec/corpus-row.schema.json` with `schema_version: 1`. It is the Lean corpus row used by the current validator path.

The v2 dataset schema is `lemma/schemas/corpus_row_v2.json` with `schema_version: 2`.

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
proof_identity_strength
full_reward_eligible
solver_hotkey
validator_hotkey
rewarded
quality
dependencies
graph
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

`row_id` is the SHA256 of `target_sha256`, `proof_sha256`, `solver_hotkey`, and `validator_hotkey`. `proof_sha256` is the script hash. `proof_term_hash` is filled only when the Lean proof-term extractor provides it. Until then, `proof_identity_source` is `normalized_script_sha256` or `script_sha256`, and `proof_identity_strength` is `weak`.

`dependencies` and `graph` make each row part of the mathematical corpus graph. The initial graph links task, proof, proof identity, source, verifier, solver, and validator nodes. Future mechanisms should extend this graph rather than creating disconnected state.

Failed proofs are not public corpus rows. Valid alternates can be stored with `rewarded: false`.

## Replay

```bash
uv run lemma corpus validate corpus.jsonl
uv run lemma corpus replay corpus.jsonl
uv run lemma corpus export --input corpus --output corpus/corpus-index.json
uv run lemma corpus benchmark-export --input corpus --output exports/lemma-proofs.jsonl --index exports/index.json
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Replay uses the task fields embedded in each row, the pinned toolchain metadata, and the Lean verifier. Lean is the production domain.

`benchmark-export` writes compact JSONL records for downstream training or evaluation jobs. Each record contains task metadata, source/license metadata, proof text and hashes, quality metadata, graph links, reward context, verification summary, and public provenance. It is an export surface, not a claim that the rows are held-out benchmark tasks.

`lemma.corpus.affine_export` converts v2 rows into simple `input` / `target` JSONL records for model-training consumers. That relationship is data-consumer oriented: external model systems can train on Lemma corpora, but Lemma does not depend on them for validation or payouts.

## Licensing

Every task and row carries `source_license`. The default dev seed uses `CC-BY-4.0`; imported sources must be checked before activation.

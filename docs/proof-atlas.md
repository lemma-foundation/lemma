# Lemma Proof Atlas

The Lemma Proof Atlas is the public data repository for accepted proof work and replay artifacts.

It contains:

- accepted proofs: Lean theorem tasks solved by miners and verified by validators;
- task registries: SHA-pinned active-task registry snapshots;
- exports: compact downstream JSONL views;
- canonical storage artifacts: active-pool, accepted-proof, curriculum, and commitment digests.

## Repository Layout

The public repo is `lemma-foundation/lemma-proof-atlas`.

```text
proofs/<netuid>/accepted/        accepted proof JSONL rows by epoch
proofs/<netuid>/index.json       accepted proof row index
tasks/<netuid>/registries/       pinned task registries by hash
exports/<netuid>/                compact downstream JSONL exports
canonical/<netuid>/              active-pool, accepted-proof, curriculum, and commitment artifacts
MANIFEST.sha256                  hash checklist for public snapshot files
```

Accepted proof rows are canonical network output. Failed submissions, local verifier logs, and operator state are not Proof Atlas data.

## Publishing

From the Lemma repo, publish a prepared Proof Atlas checkout with:

```bash
uv run python scripts/publish_proof_atlas_snapshot.py --repo ~/lemma-proof-atlas --netuid sn467 --push-repo
```

From a live validator, sync public outputs first:

```bash
uv run python scripts/publish_proof_atlas_snapshot.py \
  --repo "$LEMMA_PROOF_ATLAS_REPO" \
  --netuid "sn${BT_NETUID}" \
  --sync-proof-dir "$LEMMA_CORPUS_OUTPUT_DIR" \
  --sync-canonical-dir "$LEMMA_CANONICAL_OUTPUT_DIR/sn${BT_NETUID}" \
  --sync-registry-cache-dir "$LEMMA_ACTIVE_REGISTRY_CACHE_DIR" \
  --push-repo
```

`LEMMA_CORPUS_OUTPUT_DIR` is the current internal validator setting for accepted proof JSONL output. Treat the name as legacy internal plumbing; it writes the `proofs/<netuid>/accepted/` layer in the Proof Atlas.

For a no-upload preview:

```bash
uv run python scripts/publish_proof_atlas_snapshot.py --repo ~/lemma-proof-atlas --netuid sn467 --dry-run
```

The publisher regenerates `proofs/<netuid>/index.json`, exports, `canonical/<netuid>/storage-index.json`, and `MANIFEST.sha256`; uploads a timestamped Hippius snapshot; creates an immutable GitHub release mirror; and can sync a compact Hugging Face dataset mirror containing the export JSONL, benchmark index, storage index, and manifest.

Credentials must stay in deployment environment variables, never in repo files.

## Storage Roots

The publisher indexes one deterministic directory per accepted chain tempo. If proof rows carry `tempo`, that chain tempo is authoritative; the `epoch-*.jsonl` filename is only a fallback for rows without a tempo.

```text
canonical/sn467/tempos/tempo-000001/
  entries/
  manifest.json
canonical/sn467/commitments/tempo-000001.json
```

`manifest.json` records per-entry SHA256 hashes and the accepted-proof Merkle root. `commitments/tempo-*.json` records the compact payload committed on chain.

## Accepted Proof Rows

An accepted proof row is a replayable record of a theorem task, submitted proof, validator result, provenance, quality metadata, dependencies, and graph links.

Minimal meaning:

```json
{
  "task_id": "lemma.sample.true_intro",
  "proof_script": "by trivial",
  "verification": {
    "passed": true,
    "verifier_version": "lemma-lean-v1"
  },
  "source_license": "CC-BY-4.0",
  "rewarded": true
}
```

The full row also carries task identity, Lean imports, toolchain and Mathlib pins, proof hashes, solver and validator hotkeys, difficulty metadata, dependency metadata, graph nodes, and quality checks.

Failed proofs are not accepted proof rows. Valid alternate proofs can be stored with `rewarded: false`.

## Replay And Exports

```bash
uv run lemma corpus validate proofs/sn467/accepted/epoch-000001.jsonl
uv run lemma corpus replay proofs/sn467/accepted/epoch-000001.jsonl
uv run lemma corpus benchmark-export --input proofs/sn467/accepted --output exports/sn467/lemma-proofs.jsonl --index exports/sn467/benchmark-index.json
```

The CLI group is still named `lemma corpus` internally for now. Its job is validating and exporting accepted proof rows in the Proof Atlas.

`benchmark-export` writes compact JSONL records for downstream training or evaluation. It is an export surface, not a claim that the rows are held-out benchmark tasks.

## Privacy Boundary

The Proof Atlas is public data only. Never publish operator state, environment files, wallets, logs, raw failed submissions, local machine paths, private notes, or validator spool data.

Run the project leak check before any commit or push.

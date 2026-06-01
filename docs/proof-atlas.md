# Lemma Proof Atlas

The Lemma Proof Atlas is the public data repository for the subnet.

It combines two layers that used to be described separately:

- accepted proofs: Lean theorem tasks solved by miners and verified by validators;
- generated graph data: facts, definitions, dependency records, recipes, policies, and reports used to build future tasks.

Public copy should call this `Data` or `Proof Atlas`. Avoid presenting `corpus` or `ingredients` as product concepts. In code, `CorpusRow` and ingredient manifests may still appear as internal schema names, but the public artifact is one atlas.

## Repository Layout

The public repo is `lemma-foundation/lemma-proof-atlas`.

```text
proofs/<netuid>/accepted/        accepted proof JSONL rows by epoch
proofs/<netuid>/index.json       accepted proof row index
tasks/<netuid>/registries/       pinned active-task registries by hash
tasks/<netuid>/bundles/          replayable generated task artifact bundles
graph/mathlib/                   extracted Mathlib facts, definitions, and compatibility graph
graph/<netuid>/roots/            content-addressed generated graph roots
generation/                      task recipes, policies, reports, and soundness templates
exports/<netuid>/                compact downstream JSONL exports
canonical/<netuid>/              active-pool, accepted-proof, curriculum, and commitment artifacts
MANIFEST.sha256                  hash checklist for public snapshot files
```

The boundary is simple:

```text
proofs/ = what miners proved and validators accepted
graph/ + generation/ = reproducible task-generation view of public proof data
```

Accepted proof rows are canonical network output. Graph and generation files are derived public artifacts. They should be reproducible from Mathlib pins, accepted Lemma proof rows, recipes, and policies.

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
  --sync-graph-root-dir "$LEMMA_INGREDIENT_ROOT_DIR" \
  --sync-task-bundle-dir "$LEMMA_INGREDIENT_TASK_BUNDLE_DIR" \
  --push-repo
```

`LEMMA_CORPUS_OUTPUT_DIR` is still the current internal validator setting for accepted proof JSONL output. Treat the name as legacy internal plumbing; it writes the `proofs/<netuid>/accepted/` layer in the Proof Atlas.

For a no-upload preview:

```bash
uv run python scripts/publish_proof_atlas_snapshot.py --repo ~/lemma-proof-atlas --netuid sn467 --dry-run
```

The publisher regenerates `proofs/<netuid>/index.json`, exports, `canonical/<netuid>/storage-index.json`, and `MANIFEST.sha256`; uploads a timestamped Hippius snapshot; creates an immutable GitHub release mirror; and can sync a compact Hugging Face dataset mirror containing the export JSONL, benchmark index, storage index, and manifest. It defaults to:

- Hippius bucket: `lemma-proof-atlas-sn467`
- GitHub repo: `lemma-foundation/lemma-proof-atlas`
- resolver label: `hippius-s3-arion`

Credentials must stay in deployment environment variables, never in repo files.

## Storage Roots

The publisher indexes one deterministic directory per accepted chain tempo. If proof rows carry `tempo`, that chain tempo is authoritative; the `epoch-*.jsonl` filename is only a fallback for rows without a tempo.

```text
canonical/sn467/tempos/tempo-000001/
  entries/
  manifest.json
canonical/sn467/commitments/tempo-000001.json
```

`manifest.json` records per-entry SHA256 hashes and the accepted-proof Merkle root. `commitments/tempo-*.json` records the compact payload committed on chain. When IPFS publishing is configured, that payload binds the active-pool CID, accepted-proof CID, their directory hashes, and the accepted Merkle root.

To anchor a published storage root on Bittensor, first dry-run the latest commitment:

```bash
uv run python scripts/publish_chain_commitment.py --repo ~/lemma-proof-atlas --netuid sn467 --bt-netuid 467
```

Submit only after checking the payload:

```bash
uv run python scripts/publish_chain_commitment.py --repo ~/lemma-proof-atlas --netuid sn467 --bt-netuid 467 --submit
```

A mirror-only publisher can verify readback without local wallet files:

```bash
uv run python scripts/publish_chain_commitment.py --repo ~/lemma-proof-atlas --netuid sn467 --bt-netuid 467 --readback --hotkey <validator-hotkey-address>
```

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

## Graph And Generation Data

The graph layer is the reusable structure extracted from Mathlib and accepted Lemma proofs:

- facts and definitions;
- compatibility edges;
- dependency records;
- source theorem and source lemma rows;
- quality reports;
- recipe selectors and policy files;
- soundness templates used by generated tasks.

This is what earlier implementation notes called `ingredients`. Keep that term for schema internals only. Public docs should call it the proof graph or generation graph.

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

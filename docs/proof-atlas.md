# Lemma Proof Atlas

The Lemma Proof Atlas is the public data repository for accepted Lean work and replay artifacts.

It contains:

- accepted artifacts: Lean proof or patch tasks solved by miners and verified by validators;
- task registries: SHA-pinned active-task registry snapshots;
- exports: compact downstream JSONL views;
- canonical storage artifacts: active-pool, accepted-proof, curriculum, and commitment digests.

## Repository Layout

The public repo is `lemma-foundation/lemma-proof-atlas`.

```text
proofs/<netuid>/accepted/        accepted artifact JSONL rows by epoch
proofs/<netuid>/index.json       accepted artifact row index
proofs/<netuid>/solved-ledger.json  real tasks that have an accepted, rewarded proof
tasks/<netuid>/registries/       pinned task registries by hash
tasks/<netuid>/bundles/index.json   public real task bundles (source + environment + replay)
envs/<netuid>/index.json         certified validation environments keyed by hash
sources/<netuid>/index.json      source ingest reports, repo pins, and licenses
exports/<netuid>/                compact downstream JSONL exports
canonical/<netuid>/              active-pool, accepted-proof, curriculum, and commitment artifacts
MANIFEST.sha256                  hash checklist for public snapshot files
```

Accepted artifact rows are canonical network output. Failed submissions, local verifier logs, and operator state are not Proof Atlas data.

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

`LEMMA_CORPUS_OUTPUT_DIR` is the current internal validator setting for accepted artifact JSONL output. Treat the name as legacy internal plumbing; it writes the `proofs/<netuid>/accepted/` layer in the Proof Atlas.

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

## Accepted Artifact Rows

An accepted artifact row is a replayable record of a task, submitted proof or patch, validator result, provenance, quality metadata, dependencies, and graph links.

Minimal meaning:

```json
{
  "task_id": "lemma.sample.true_intro",
  "artifact_kind": "proof",
  "proof_script": "by trivial",
  "verification": {
    "passed": true,
    "verifier_version": "lemma-lean-v1"
  },
  "source_license": "CC-BY-4.0",
  "rewarded": true
}
```

Patch tasks use `artifact_kind: "patch"` with `patch_text` and `patch_sha256`; they do not store patch text in `proof_script`.

The full row also carries task identity, Lean imports, toolchain and Mathlib pins, artifact hashes, solver and validator hotkeys, difficulty metadata, dependency metadata, graph nodes, and quality checks.

Failed submissions are not accepted artifact rows. Valid alternates can be stored with `rewarded: false`.

## Replay And Exports

```bash
uv run lemma corpus validate proofs/sn467/accepted/epoch-000001.jsonl
uv run lemma corpus replay proofs/sn467/accepted/epoch-000001.jsonl
uv run lemma corpus benchmark-export --input proofs/sn467/accepted --output exports/sn467/lemma-proofs.jsonl --index exports/sn467/benchmark-index.json
```

The CLI group is still named `lemma corpus` internally for now. Its job is validating and exporting accepted proof rows in the Proof Atlas.

`benchmark-export` writes compact JSONL records for downstream training or evaluation. It is an export surface, not a claim that the rows are held-out benchmark tasks.

## Real-Task Layer

Lemma's product is verified Lean progress on real, public, useful formalization tasks. The real-task layer makes that progress reproducible from public inputs alone: the pinned task registry plus the accepted proof rows. It is pure data tooling and performs no upload, commit, or chain write.

It builds four artifacts:

- `tasks/<netuid>/bundles/index.json` — one public **task bundle** per pinned task. A bundle carries the real source reference (`source_ref`: kind, name, url, commit, path), license, imports, editable files, the exact target (`target_sha256`, `target_type_sha256`), the certified environment (`environment_sha256`, `lean_toolchain`, `mathlib_rev`), and the `reproduction_command`. It carries no operator state, secrets, or local paths.
- `proofs/<netuid>/solved-ledger.json` — the **solved ledger**: each real task that earned an accepted, rewarded proof, joined by `task_id` to its bundle. Each entry names the winning miner and validator hotkeys, block, artifact kind, artifact hash, proof identity, and plain-language **apply/replay instructions** for putting the proof or patch back on the original source.
- `envs/<netuid>/index.json` — the **environment index**: certified validation environments keyed by `environment_sha256`, with the toolchain, Mathlib revision, source kinds, and the task IDs that share each environment.
- `sources/<netuid>/index.json` — the **sources index**: the source ingest reports (repo pins, licenses, quarantine counts) for the tasks in the snapshot.

Build a dry-run snapshot (prints a manifest of every file it would write with its SHA256, touches nothing):

```bash
uv run lemma atlas snapshot \
  --registry tasks/sn467/registries/<hash>.json \
  --accepted proofs/sn467/accepted \
  --source-report sources/sn467/sorrydb-report.json
```

Add `--repo ~/lemma-proof-atlas` to write the artifacts into a Proof Atlas checkout. Two validators that start from the same registry hash and accepted rows produce byte-identical artifacts.

`scripts/publish_proof_atlas_snapshot.py` builds this layer automatically: after preparing the accepted-row indexes it merges the pinned registries (`tasks/<netuid>/registries/`, newest wins on a task-id collision), reads the accepted rows, and writes the bundles, solved ledger, environment index, and sources index. Those files then flow through `MANIFEST.sha256`, the Hippius/GitHub/Hugging Face mirrors, and the public commit like the rest of the snapshot. The step is skipped automatically when no registry is present yet, and can be turned off with `--skip-real-task`. Drop source ingest reports into `sources/<netuid>/` to have them indexed (the builder ignores `index.json` and `snapshot.json`).

## Static Site Preview

The public site (`lemmasub.net`) is a real-task board and solved-proof explorer rendered directly from the real-task artifacts. The generator reads the task bundles and solved ledger and writes plain static HTML with no build step and no client-side data fetch, matching the site's static deployment.

```bash
uv run lemma site build --atlas ~/lemma-proof-atlas --out ~/lemma-proof-atlas/site
```

It writes three pages into the output directory:

- `index.html` — the product statement and headline open/solved counts;
- `board.html` — each real task with its source project and type, task class, validation environment, open/solved status, a link to the task bundle, a link to the source repo at the pinned commit, and the reproduction command;
- `solved.html` — each accepted proof or patch with its source commit, proof identity, plain-language apply instructions, the replay command, and mirror links.

All dynamic content is HTML-escaped, and the pages render only public bundle/ledger fields. Pass `--atlas-base-url`, `--hippius-url`, and `--huggingface-url` to point the links and mirror badges at the real public locations. When no artifacts are present yet, the pages render honest empty states.

## Privacy Boundary

The Proof Atlas is public data only. Never publish operator state, environment files, wallets, logs, raw failed submissions, local machine paths, private notes, or validator spool data.

Run the project leak check before any commit or push.

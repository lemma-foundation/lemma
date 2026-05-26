# Validator Guide

Validators verify and publish accepted solutions.

Validators run the pinned Lean checker, score rank-0 accepted proofs, and write replayable corpus rows.

## Basic Flow

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
```

The file spool is a local smoke path. Live production validation uses miner bucket reveals:

```bash
LEMMA_ENABLE_SET_WEIGHTS=1 uv run lemma validate --once --bucket-reveals-jsonl bucket-reveals.jsonl --set-weights
```

Use `--no-set-weights` for smoke passes and corpus-only validation.

`lemma validate` loads the task registry, validates miner submissions, dispatches to the domain verifier adapter, writes verification results, writes score events, writes a public-safe `validator-runs.jsonl` summary row, publishes corpus JSONL deltas, and writes canonical active-pool plus accepted-entry directories.
If `LEMMA_CANONICAL_PUBLISH_IPFS_API_URL` is set, the validator uploads the active-pool and accepted-entry directories to IPFS, reads each file back by CID, and rewrites the tempo commitment so the chain payload binds both CIDs plus the directory hashes. If `LEMMA_CANONICAL_PUBLISH_S3_URI` is set, it also uploads the active-pool directory, accepted-entry directory, and tempo commitment file to that S3-compatible Hippius prefix and reads back each object before any chain commitment. When `--set-weights` is enabled, each chain-write attempt also appends a public-safe `weight-submissions.jsonl` receipt under `LEMMA_OPERATOR_DATA_DIR` with the resolved UID vector, weights, network, netuid, success flag, sanitized client message, and extrinsic hash when available. When `--set-commitment` is enabled with `LEMMA_ENABLE_SET_COMMITMENT=1`, the validator submits the compact tempo commitment binding the active-pool artifact and accepted-entry artifact.
On commit-reveal subnets, the chain writer waits until the final 10 blocks of the tempo before submitting.
Validator operation should be boring: configure the environment, start `lemma validate`, and watch the receipts. Internal preflight, diagnostics, and worker commands remain available for development and operator debugging, but the public validator path is the single validation command.

Production validation fails unless `LEMMA_TASK_SUPPLY_MODE=procedural`, the public source pool is SHA-pinned, `LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR` points at the prior accepted-entry substrate mirror, paid tasks are procedural depth-2 and generated from chain/drand epoch randomness with the chain-pinned Lean AST/elaborator mutation bundle, drand-keyed mutation params, public source-pool receipts, public novelty-cache receipts, and Lean-elaborated kernel-normal `kernel_canonical_hash`, Lean verifier networking is disabled, live miner authentication is required, commit/reveal fields are required, and strong proof identity is required for paid rewards. Accepted proofs record their actual Lean kernel dependencies, and rewarded slot weights come from those recorded dependencies rather than from the public import graph estimate. Registry files are caches for distribution and replay, not production authority.
For a file-based smoke loop in development mode, set `LEMMA_SUBMISSION_SPOOL_DIR` or pass `--submission-spool`. Pending top-level `.json` and `.jsonl` files are read once and moved to `processed/` after validation succeeds.
For the production loop, pass `--bucket-reveals-dir` for the live reveal inbox, or `--bucket-reveals-jsonl` for a fixture file. One validator pass scores one reveal tempo: directory intake selects the newest tempo, moves successfully processed files to `processed/`, and moves older top-level reveal files to `stale/`. The validator recomputes each miner's Merkle root from `(slot_index, ciphertext_sha256)` pairs before turning revealed proofs into submissions. Add `--verify-chain-commitments` to read the miner's on-chain bucket commitment, and add `--verify-drand-reveals` to decrypt each bucket ciphertext and require it to match the revealed proof; production mode enables both checks for bucket reveals. Binary ciphertexts should be JSON-encoded as `base64:<payload>` or `0x<hex>`. To poll live buckets directly, pass `--miner-buckets-json`, `--bucket-drand-round`, and `--bucket-drand-signature`; miners can build those public `tempo_<t>/slot_<i>.bin` objects with `lemma miner bucket publish`, and the validator fetches them before reusing the same reveal validation path.
The file spool is suitable for local smoke tests. SN467 burn-in and mainnet settlement are bucket/commitment-shaped: proof packages must be authenticated by the miner's chain commitment and must carry commit/reveal fields.
Run `uv run python scripts/refresh_site_current_problems.py --site-repo /opt/lemmasub.net --commit --push` from the validator-side publish timer to refresh the public website's active-problem dashboard.
For a live website feed, run `uv run python scripts/serve_current_problems.py --host localhost --port 8731` behind a TLS proxy such as `api.lemmasub.net`; the static site falls back to the committed JSON snapshot if that API is unavailable.

## Runtime Steps

1. Load the active task registry.
2. Load the latest eligible public curriculum tempo state when retargeting is enabled. A retarget row must be at least one full tempo old (`record.tempo < active_tempo - 1`) before it can affect the active window. Then select the deterministic active window from active `K`, frontier depth, target window length, `LEMMA_ACTIVE_QUEUE_SEED`, and production chain/drand epoch randomness.
3. Read miner bucket reveals in production, or local smoke submissions in development.
4. Reject submissions outside the active window.
5. Reject task-version and target-hash mismatches.
6. Require hotkey authentication for live miner responses.
7. Run the domain submission policy scan before the verifier.
8. Verify with the adapter-selected pinned runtime.
9. Score rank-0 unique proof per active task by commit block and deterministic active slot weight.
10. Track `unearned_share = 1 - sum(miner_weights)`.
11. Burn unearned share by default; do not redistribute it to current solvers.
12. Write corpus rows for valid unique proofs after the scoring window closes.

## Internal Worker

Remote Lean workers are an internal scaling surface for validators. Non-loopback worker binds require `LEMMA_LEAN_VERIFY_REMOTE_BEARER` unless explicitly allowed for development.

## No Subjective Scoring

Validators score proofs, not reasoning prose, model names, proof style, or claimed effort. Lean remains the only production verifier today.

# Production

Production Lemma is the Lean proof competition loop:

1. rebuild the active task pool from public procedural inputs;
2. read miner bucket reveals after commitment/reveal;
3. verify each proof with the pinned Lean environment;
4. score rank-0 unique proofs by miner commit block;
5. compute miner weights from deterministic active slot weights and burn unearned share by default;
6. write accepted proof records and, when snapshot publishing is configured, publish the configured corpus/index artifacts.

Production mode is stricter than local smoke mode. SN467 testnet burn-in must run this same production mode with `BT_NETWORK=test` and `BT_NETUID=467`; mainnet cutover should only change the chain target. Production mode fails closed unless procedural supply is rebuilt from a pinned public source pool, an explicit prior-substrate mirror, and chain/drand epoch randomness, live miner submissions are hotkey-authenticated, commit/reveal fields are present, Lean verifier networking is disabled, and paid rewards require strong Lean-derived proof identity.
The launch gate sequence is tracked in [Mainnet Readiness](mainnet-readiness.md).

## Operator Rules

- Do not route subnet owner emissions through a contract.
- Do not use escrow-style reward custody for Lemma rewards.
- Do not score prose, model branding, or claimed effort.
- Keep task, submission, verifier, scoring, and corpus artifacts replayable.
- Delay public proof release until the scoring window closes.
- Keep `.env`, wallets, local state, logs, caches, and machine paths out of commits.

## Production Readiness Gates

The chain is the authority for epoch correctness. Timers are only wakeups: each
registry-cache warmer, miner, and validator pass must recompute the active tempo from chain
state, then exit idempotently when there is nothing new to do. Bucket intake
timers should poll often enough that miner/validator ordering jitter costs
minutes, not an epoch. Weight writes stay block-gated; on commit-reveal subnets,
the writer waits for the final weight window before submitting.
Active-registry cache warmers must allow slow Lean gates to finish; miners
should idle on missing cache rather than rebuilding the same registry in
parallel.

Generic validators only need the protocol path:

1. rebuild or load the active registry from public pinned inputs;
2. consume live bucket reveals;
3. verify submitted proofs with the pinned Lean environment;
4. write local verification, score, run, corpus, and canonical tempo artifacts;
5. set weights from accepted Lean proofs.

Snapshot publishing is a separate deployment setup. The subnet owner uses the
repo's Hippius, GitHub release, and Hugging Face tooling for the canonical
public corpus service. Validators can run the same publishing tools for their
own mirrors if they configure storage and credentials, but that is optional;
validation itself only requires the protocol path above.

Before calling SN467 production-ready, prove:

- one full natural tempo with no manual starts: registry cache warming, miner bucket
  delivery, validator intake, Lean verification, weight write, and commitment
  readback;
- a 24- to 72-hour burn-in with repeated natural tempos and no operator nudges;
- at least one second validator or clean rebuild that reproduces active-registry
  and Lean-verification behavior from public inputs;
- a short runbook for active tempo, registry cache, miner marker, reveal queue,
  validator result, corpus artifacts, and chain readback.

## Commands

```bash
uv run lemma status
uv run lemma worker --check
uv run lemma operator preflight
uv run lemma worker --serve --host localhost --port 8787
uv run lemma validate --once --bucket-reveals-jsonl bucket-reveals.jsonl --no-set-weights
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Production launch settings:

```bash
LEMMA_PROTOCOL_MODE=production
LEMMA_TASK_SUPPLY_MODE=procedural
LEMMA_PROCEDURAL_SOURCE_JSONL=snapshot.jsonl
LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR=corpus
LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED=<source-pool-sha256>
LEMMA_PROCEDURAL_CITATION_ALPHA=0.5
LEMMA_PROCEDURAL_CITATION_WEIGHT_CAP=64
LEMMA_PROCEDURAL_CITATION_WINDOW_TEMPOS=2000
LEMMA_REQUIRE_SUBMISSION_SIGNATURES=1
LEMMA_REQUIRE_COMMIT_REVEAL=1
LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1
LEMMA_ACTIVE_TEMPO_SOURCE=chain
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand
LEMMA_ACTIVE_REGISTRY_CACHE_DIR=active-registries
LEMMA_CURRICULUM_RETARGET=1
LEMMA_CURRICULUM_STATE_JSONL=curriculum-state.jsonl
LEMMA_CURRICULUM_STATE_PUBLIC=1
LEMMA_VALIDATOR_CAPACITY=20
LEMMA_CURRICULUM_COST_BUDGET_S=2700
LEMMA_CURRICULUM_BASE_TASK_COST_S=180
LEMMA_CURRICULUM_DEPTH_COST_MULTIPLIER=2
LEMMA_PROCEDURAL_GATE_TIMEOUT_S=120
LEMMA_PROCEDURAL_TRIVIALITY_BUDGET_S=120
LEMMA_PROCEDURAL_LEAN_BATCH_SIZE=96
LEMMA_PROCEDURAL_LEAN_BATCH_PARALLELISM=1
LEMMA_PROCEDURAL_LEAN_COMPILE_ERROR_SPLIT_LIMIT=16
LEMMA_PROCEDURAL_YIELD_HISTORY_JSONL=procedural-yield-history.jsonl
LEMMA_PROCEDURAL_YIELD_HISTORY_SHA256_EXPECTED=<yield-history-sha256>
LEMMA_PROCEDURAL_NOVELTY_CACHE_JSONL=public-entry-cache.jsonl
LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL=public-import-graph.jsonl
LEMMA_CANONICAL_OUTPUT_DIR=canonical
LEAN_SANDBOX_NETWORK=none
```

`lemma operator preflight` checks the pinned source snapshot against the requested `frontier_depth`. If the snapshot only contains shallow rows, preflight fails before the operator can accidentally ask for frontier tasks from a shallow source pool.

Rebuild a launch registry cache from deterministic procedural depth-2 candidates:

```bash
uv run lemma tasks rebuild-procedural-registry \
  --mathlib-snapshot snapshot.jsonl \
  --generation-seed "$EPOCH_SEED" \
  --epoch-randomness "$EPOCH_RANDOMNESS_JSON" \
  --tempo "$TEMPO" \
  --count "$K" \
  --prior-corpus-dir corpus \
  --citation-alpha 0.5 \
  --citation-weight-cap 64 \
  --citation-window-tempos 2000 \
  --novelty-cache-jsonl public-entry-cache.jsonl \
  --import-graph-jsonl public-import-graph.jsonl \
  --output tasks/mainnet.registry.json
```

The cache can be mirrored publicly, but production validators do not trust it
as the problem authority. They rebuild the same rows locally from the pinned
source snapshot, prior accepted-entry substrate mirror, chain state, drand, the
public novelty cache, and the public import graph. Paid rows must include a
source-pool receipt and a Lean-backed generation receipt: typecheck, Prop,
kernel-canonical novelty, pre-proof import-graph slot estimate, and triviality-stack gates run
before the candidate can enter the active paid pool. Accepted proof verification records actual
Lean kernel dependencies, and rewarded slot weights are recomputed from that recorded dependency set.

For a live loop, warm the current active tempo registry that miners and validators share:

```bash
uv run lemma tasks warm-active-procedural-registry
```

For faster procedural generation, keep the Lean verifier warm instead of paying
per-check container startup. Start a long-lived Docker worker with
`scripts/start_lean_docker_worker.sh`, set `LEMMA_LEAN_DOCKER_WORKER` in the
service environment, and keep `LEMMA_LEAN_VERIFY_WORKSPACE_CACHE_DIR` mounted at
the same worker path. The procedural gate also supports explicit parallel Lean
batches with `LEMMA_PROCEDURAL_LEAN_BATCH_PARALLELISM`; leave it at `1` unless a
host has enough Lean worker capacity to run multiple batch modules at once.
On the local warm Docker worker, a synthetic 96-candidate gate probe accepted
all 96 candidates in one batch at roughly half the wall time of the 50-wide
configuration, so `96` is the default batch size.
`LEMMA_PROCEDURAL_YIELD_HISTORY_JSONL` is optional public telemetry from prior
generation attempts. When set, validators hash it and use accepted source-family
and operator-chain counts only to order current-epoch candidates; it does not
generate future task sets.

Procedural speed checklist:

- [x] Keep the source-theorem baseline out of the procedural acceptance gate.
- [x] Batch Lean gate checks so one Lean module can evaluate many candidates.
- [x] Make Lean gate batch size configurable with the current live default preserved.
- [x] Split an oversized Lean batch on timeout/OOM and salvage smaller valid batches.
- [x] Split a compile-error batch with a bounded salvage budget.
- [x] Add cheap pre-Lean rejection for mutation-chain drift that would fail production invariants later.
- [x] Emit operator-chain and source-family yield telemetry when procedural telemetry is enabled.
- [x] Run procedural gate batches in parallel when `LEMMA_PROCEDURAL_LEAN_BATCH_PARALLELISM` is explicitly set.
- [x] Document the long-lived Lean verifier worker as the preferred live path when available.
- [x] Keep future-epoch prebuild out of scope; warm only the current tempo after public randomness exists.
- [x] Remove generated source-theorem baseline code that does not affect the proof/triviality decision.
- [x] Use public yield telemetry to promote high-yield source families and operator chains deterministically.
- [x] Reject alpha-equivalent generated duplicates before paying the Lean gate cost.
- [x] Measure whether batch sizes above 50 improve the live worker without lowering yield.

`LEMMA_ACTIVE_REGISTRY_JSON` pins one exact active registry file and fails closed if the file is missing. `LEMMA_ACTIVE_REGISTRY_CACHE_DIR` loads `tempo-<tempo>.registry.json` when present, and otherwise lets builder validators rebuild from the pinned public inputs. The warm command writes only the current tempo cache after that tempo's randomness is available, then skips it when already present. Use `--force` to refresh an existing current cache. Future paid task sets must not be generated privately before their tempo randomness exists. The lower-level rebuild command ignores active-cache settings so it can write a manually chosen output file.
If `LEMMA_ACTIVE_REGISTRY_CACHE_INDEX_URL` points at a public corpus `registries/<netuid>/index.json`, miners, validators, and cache warmers hydrate the tempo cache from that public mirror before falling back to local generation. The downloaded registry is still treated only as a cache: it must match the published SHA256 and the effective public curriculum state before it is written locally. In production, prefer one designated builder validator to generate and publish the current registry, and run ordinary validators as auditors with `LEMMA_ACTIVE_REGISTRY_ROLE=auditor`. Auditor validators use the current public/cache registry, verify it, and idle/fail closed if the current cache is unavailable instead of spending Lean compute. Keep extra builder validators only when intentionally cross-checking generation capacity.

`LEMMA_CURRICULUM_RETARGET=1` retargets `K` and frontier depth. Solve-rate history moves frontier depth. Validator capacity and the optional curriculum cost budget cap `K`, so harder frontiers can automatically run fewer tasks. Each validator pass writes the next retarget row into the canonical public curriculum artifacts. Later active sets use the latest eligible public row where `record.tempo < active_tempo - 1`, giving the row one full tempo of public replay lag before it can affect active selection. In production, `LEMMA_CURRICULUM_STATE_PUBLIC=1` is required and `LEMMA_CURRICULUM_STATE_JSONL` must point at a replay cache synced from the public corpus artifacts, outside `LEMMA_CANONICAL_OUTPUT_DIR`/`LEMMA_OPERATOR_DATA_DIR/canonical`; a private local row or a replay path inside canonical publish output can make miners and validators drift.

Corpus deltas are written under `LEMMA_CORPUS_OUTPUT_DIR`. Canonical active-pool and accepted-entry directories are written under `LEMMA_CANONICAL_OUTPUT_DIR` when set, otherwise under `LEMMA_OPERATOR_DATA_DIR/canonical`. Local receipts are written under `LEMMA_OPERATOR_DATA_DIR`. If `LEMMA_SUBMISSION_SPOOL_DIR` is set, validators consume pending `.json` or `.jsonl` submission files from that directory and move them to `processed/` after a successful pass. These paths should remain ignored unless an operator intentionally publishes sanitized artifacts.
The file spool remains a local/operator-smoke path. The production adapters are `--bucket-reveals-dir` for a live reveal inbox and `--bucket-reveals-jsonl` for a fixture file. Each reveal row carries miner hotkey, tempo, drand round, drand signature, commit block, committed Merkle root, and revealed bucket blobs. One validation pass scores one tempo: directory intake picks the newest top-level reveal tempo, archives processed files under `processed/`, and moves older reveal files to `stale/`. Binary ciphertexts should be encoded as `base64:<payload>` or `0x<hex>`. The validator recomputes the Merkle root over decoded ciphertext bytes, confirms the miner's on-chain bucket commitment in production, decrypts bucket ciphertexts in production, requires the decrypted proof to match the reveal, and ranks winners by commit block. A live miner can publish ciphertexts with `uv run lemma miner bucket publish --submission submission.json --tempo <tempo> --drand-round <round> --miner-hotkey <hotkey> --output-dir validator-data/miner-bucket --s3-uri s3://<bucket>/<miner-prefix> --verify-upload --submit-commitment`. A live validator can then pass `--miner-buckets-json miner-buckets.json --bucket-drand-round <round> --bucket-drand-signature <signature>` to poll public bucket objects directly before converting them into the same reveal path.
Set `LEMMA_CANONICAL_PUBLISH_IPFS_API_URL=http://<ipfs-node>:5001` to have the validator upload the active-pool, accepted-entry, and curriculum directories to IPFS, read each file back by CID, and write a CID-bound tempo commitment for the active/accepted pair. Set `LEMMA_CANONICAL_PUBLISH_S3_URI=s3://<bucket>/<canonical-prefix>` to also mirror the active-pool directory, accepted-entry directory, curriculum directory, and tempo commitment file to Hippius S3 before it writes the chain commitment. `LEMMA_CANONICAL_PUBLISH_ENDPOINT_URL` defaults to `https://s3.hippius.com`, and `LEMMA_CANONICAL_PUBLISH_VERIFY=1` reads uploaded IPFS/S3 objects back and compares bytes.
Live weight writes require both `LEMMA_ENABLE_SET_WEIGHTS=1` and `--set-weights`; the live bucket wrapper stays on `--no-set-weights` unless `LEMMA_VALIDATOR_SET_WEIGHTS=1` is also set. Live tempo artifact commitments require both `LEMMA_ENABLE_SET_COMMITMENT=1` and `--set-commitment`. Keep production smoke and corpus-only passes on `--no-set-weights` without `--set-commitment`. On commit-reveal subnets, the chain writer waits until the final 10 blocks of the tempo before submitting weights. Each attempted live write appends a public-safe receipt with the resolved UID vector or tempo commitment payload, client result, and extrinsic hash when available.

For the full registry-to-validator-to-export sequence, see [Operator Registry Flow](operator-registry-flow.md).

Publish the current public corpus snapshot after a closed SN467 production-mode pass:

```bash
uv run python scripts/publish_corpus_snapshot.py --repo ~/lemma-corpus --netuid sn467 --push-repo
```

For a live validator, pass `--sync-corpus-dir`, `--sync-canonical-dir`, and `--sync-registry-cache-dir` so the public checkout receives the validator's latest corpus rows, canonical tempo artifacts, and active registry caches before upload. This regenerates the public index/export, writes `MANIFEST.sha256`, uploads a timestamped Hippius snapshot, creates the GitHub immutable release mirror, and syncs an append-only Hugging Face dataset snapshot. Hippius, GitHub, and Hugging Face credentials must stay in the deployment environment, never in repo files.

Refresh the public website's active-problem dashboard from the validator host:

```bash
uv run python scripts/refresh_site_current_problems.py --site-repo /opt/lemmasub.net --commit --push
```

The script only writes `data/current-problems.json` in the site checkout. It refuses to commit if the site repo already has staged changes, and it scans the staged dashboard diff before committing.

For a live dashboard endpoint, run the narrow JSON server on the validator host and put a TLS proxy in front of it:

```bash
uv run python scripts/serve_current_problems.py --host localhost --port 8731
```

The server exposes `GET /current-problems.json` and `GET /healthz`, sends CORS headers for the static website, and does not expose submissions, proofs, wallets, or operator state. Keep `refresh_site_current_problems.py` as the fallback snapshot publisher.

After checking the published snapshot, anchor the latest storage root on Bittensor:

```bash
uv run python scripts/publish_chain_commitment.py --repo ~/lemma-corpus --netuid sn467 --bt-netuid 467 --submit
```

Run it without `--submit` first to print the payload without writing chain state.
For mirror-only readback without wallet files, pass `--readback --hotkey <validator-hotkey-address>`.

Run the leak check before any commit or push:

```bash
uv run python scripts/leak_check.py
```

For the full local launch gate, run:

```bash
uv run python scripts/workstream_audit.py --profile mainnet --skip-site
```

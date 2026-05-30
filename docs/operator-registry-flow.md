# Operator Registry Flow

This is the end-to-end path for rebuilding a deterministic task registry cache, running a validator against the active procedural pool, and exporting accepted proof data.

## 1. Build A Registry

Start from proof-erased Mathlib snapshot rows that follow the [Mathlib Extraction Contract](mathlib-extraction.md):

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input snapshot.jsonl \
  --output tasks/mathlib-snapshot.registry.json
```

The command writes deterministic `queue_position` values and prints `registry_sha256`. Keep the JSON artifact and the SHA256 together for dev smoke and replay.

`signed_by` and `signature` fields are archived metadata unless the validator is configured with an explicit registry-signature verifier. They are cache checks, not production authority.

For production-shaped supply, rebuild depth-2 procedural candidates from the
public source snapshot and the tempo's epoch seed:

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
  --triviality-retarget-jsonl public-settlements.jsonl \
  --novelty-cache-jsonl public-entry-cache.jsonl \
  --import-graph-jsonl public-import-graph.jsonl \
  --output tasks/mainnet.registry.json
```

The procedural builder rejects paid candidates that do not carry procedural
depth-2 metadata, chain/drand anchoring, clean license state, deterministic
public import-graph slot-weight estimate metadata, Lean-elaborated kernel-normal `kernel_canonical_hash`,
deterministic public novelty-cache metadata,
deterministic `T(t)` retarget metadata, and a Lean-backed generation receipt. The receipt
must come from the `lean` gate runner, which runs typecheck, Prop, kernel-canonical novelty, the
pinned triviality stack at the public burn-rate-retargeted budget, and
pre-proof dependency estimation before paid activation. Accepted proof verification records the actual
Lean kernel dependencies, and rewarded slot weights are recomputed from that recorded dependency set.

The mixed builder remains available for local smoke and curriculum work. It is not the paid production path.

The rebuilt registry can be signed and mirrored as a cache, but procedural
production mode does not use the signature as authority. Validators rebuild
the active pool locally from the pinned source snapshot, chain state, and
drand.

## 2. Configure The Active Window

Set the procedural source pin and deterministic active-window controls:

```bash
LEMMA_TASK_SUPPLY_MODE=procedural
LEMMA_PROCEDURAL_SOURCE_JSONL=snapshot.jsonl
LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR=corpus
LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED=<source-pool-sha256>
LEMMA_PROCEDURAL_CITATION_ALPHA=0.5
LEMMA_PROCEDURAL_CITATION_WEIGHT_CAP=64
LEMMA_PROCEDURAL_CITATION_WINDOW_TEMPOS=2000
LEMMA_PROCEDURAL_TRIVIALITY_RETARGET_JSONL=public-settlements.jsonl
LEMMA_PROCEDURAL_NOVELTY_CACHE_JSONL=public-entry-cache.jsonl
LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL=public-import-graph.jsonl
LEMMA_ACTIVE_REGISTRY_CACHE_DIR=active-registries
LEMMA_ACTIVE_K=10
LEMMA_FRONTIER_DEPTH=0
LEMMA_ACTIVE_QUEUE_SEED=lemma-active-queue
LEMMA_ACTIVE_TEMPO_SOURCE=chain
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand
LEMMA_CORPUS_OUTPUT_DIR=corpus
LEMMA_OPERATOR_DATA_DIR=validator-data
```

`LEMMA_ACTIVE_K` is validator throughput. `LEMMA_FRONTIER_DEPTH` and generated queue depth control difficulty. Payment uses deterministic active slot weights, not subjective validator scores.
When `LEMMA_ACTIVE_REGISTRY_CACHE_DIR` is set, miners and validators load `tempo-<tempo>.registry.json` for the active tempo only if it is current for the effective production settings. `LEMMA_ACTIVE_REGISTRY_JSON` pins one exact registry file and fails closed if that file is missing. The recommended production shape is one designated builder validator plus auditor validators. Builder validators use the same deterministic generation path as the local warm command and rebuild stale current-tempo caches. The live validator bucket wrapper defaults to builder mode; set `LEMMA_ACTIVE_REGISTRY_ROLE=auditor` only for an auditor validator that should hydrate the public cache, verify it against the published hash and effective curriculum state, and refuse local generation if the current cache is not available. Run extra builders only when intentionally cross-checking generation capacity.

For live curriculum retargeting, the state log updates throughput and difficulty after each completed tempo:

```bash
LEMMA_CURRICULUM_RETARGET=1
LEMMA_CURRICULUM_STATE_JSONL=validator-data/curriculum.jsonl
LEMMA_CURRICULUM_STATE_PUBLIC=1
LEMMA_VALIDATOR_CAPACITY=20
LEMMA_CURRICULUM_COST_BUDGET_S=2700
LEMMA_CURRICULUM_BASE_TASK_COST_S=180
LEMMA_CURRICULUM_DEPTH_COST_MULTIPLIER=2
```

The retarget loop records one row per tempo. Later tempos load the latest prior row: solve rate moves `frontier_depth`, and `K` is capped by validator capacity plus the configured cost budget. A frontier increase never grows `K` in the same retarget step; if the estimated cost at the new frontier is too high, the cost cap lowers `K` immediately. Production mode accepts retargeting only when `LEMMA_CURRICULUM_STATE_PUBLIC=1`; operators should set `LEMMA_CURRICULUM_STATE_JSONL` to a state file synced from the canonical public corpus artifacts, not a private scratch log.

Plain difficulty rules:

- `frontier_depth` is the difficulty dial for the active set.
- `queue_depth` is the difficulty score on each generated task.
- `queue_depth <= 1` is easy, `<= 3` is medium, `<= 6` is hard, and `>= 7` is frontier.
- The validator only generates from source rows with `queue_depth <= frontier_depth`.
- The validator does not restrict production source rows to lightweight Data/Logic topics; any source row that supports the public depth-2 mutation path and passes gates can enter.
- Active ordering interleaves frontier and foundation levels, then balances source families inside each level.
- Slot weights use a capped `sqrt(queue_depth + 1)` depth prior, before normalization across the active set.
- Source-derived tasks are stamped with `source_reuse_class`, `source_oracle_*`, `source_import_status`, and `task_pool`; if the source theorem remains importable and gives a direct wrapper/source-oracle proof, the task is calibration/bootstrap, not serious paid frontier work.
- The current production chain turns a source relation into a proof-witness target, then specializes one public binder. It is serious paid work only when the public import graph trims the challenge imports so the source theorem's own module is not inside the strict submission envelope.
- The generator targets at least `LEMMA_ACTIVE_K`; `LEMMA_PROCEDURAL_CANDIDATE_COUNT` can ask for a larger cache, but it cannot shrink the paid active set.
- Generation gets at most 50 attempts per target task. If it cannot fill the target count, it fails closed instead of filling slots with weaker tasks.
- If miners solve enough slots, the next public retarget row can raise `frontier_depth`. If no slots are solved, frontier advancement stops and the system asks for variants.
- `K` is paid throughput. It is capped by validator capacity and the public cost budget so harder task sets can automatically become smaller.

Warm the current live tempo cache before miners run:

```bash
uv run lemma tasks warm-active-procedural-registry
```

This command is a current-tempo cache warmer. It must run only after that tempo's chain/drand randomness is available. It is not a future-task prebuild path.

Inspect registry depth before accepting submissions:

```bash
uv run lemma operator registry-inspect
```

The command reports total, active, eligible, waiting, parked, and per-depth task counts from the configured registry.

Run the operator preflight before accepting submissions:

```bash
uv run lemma operator preflight
```

The command fails if the procedural source pool is not pinned in production mode, generated rows do not carry the chain-pinned mutation bundle plus drand-keyed mutation params, the active window cannot fill `K`, output directories cannot be prepared, or the Lean verifier backend is not configured.
It emits a versioned JSON report with `schema_version`, `ok`, `registry_sha256`, `active_K`, `frontier_depth`, and `checks`. The checks include the curriculum controller status so operators can see whether retargeting is disabled, capped, or able to raise `K`.

For reproducible support/debugging, write a diagnostics file before accepting submissions:

```bash
uv run lemma operator diagnostics --output operator-diagnostics-before.json
```

The diagnostics file contains the preflight report, registry summary, artifact counts, registry hash, and current active task ids. It does not include environment variables, credentials, wallet names, hostnames, IPs, or local filesystem paths. The before-run file proves the validator was configured against the intended pinned source pool and active window.

Before opening submissions each tempo, capture health alerts over recent artifacts:

```bash
uv run lemma operator alerts --recent-runs 8 --recent-failures 3
```

The alert command surfaces zero-reveal/zero-accepted windows, cache divergence, and chain-publish/chain-write risk patterns in a machine-safe JSON report.

## 3. Validate Submissions

Run the validator against task-bound miner submissions. Production uses bucket reveals:

```bash
uv run lemma validate \
  --once \
  --bucket-reveals-jsonl bucket-reveals.jsonl \
  --no-set-weights
```

For a development file inbox, use `--submission-spool submission-spool` instead. The spool accepts top-level `.json` and `.jsonl` submission files and moves consumed files to `processed/` after a successful validator pass. For a mainnet-shaped bucket reveal fixture, use `--bucket-reveals-jsonl bucket-reveals.jsonl`; the validator checks the miner Merkle root before scoring. Add `--verify-chain-commitments` to read miner commitments from chain, and add `--verify-drand-reveals` to decrypt ciphertexts and require the decrypted proof to match the reveal; production mode enables both checks for bucket reveals.

Miners can publish those bucket objects with:

```bash
uv run lemma miner bucket publish \
  --submission submission.json \
  --tempo <tempo> \
  --drand-round <round> \
  --miner-hotkey <hotkey> \
  --output-dir validator-data/miner-bucket \
  --s3-uri s3://<public-bucket>/<miner-prefix> \
  --verify-upload \
  --submit-commitment
```

For live bucket polling, pass `--miner-buckets-json miner-buckets.json --bucket-commit-blocks-json commit-blocks.json --bucket-drand-round <round> --bucket-drand-signature <signature>`. The first JSON object maps miner hotkey to public bucket URL; the second maps miner hotkey to the positive chain block where that bucket commitment was written. The validator reads the miners' chain Merkle commitments, fetches canonical `tempo_<t>/slot_<i>.bin` objects, decrypts after drand reveal, and feeds the resulting rows through the same bucket reveal checks.

The validator rejects submissions outside the active window, task-version mismatches, target-hash mismatches, duplicate winning proofs, and policy failures. Rank-0 accepted proofs earn their deterministic active slot share; unsolved-slot value becomes `unearned_share` and is burned by default. Each pass appends one public-safe row to `validator-runs.jsonl` with the registry hash, active K, frontier depth, verified count, accepted unique count, corpus row count, unearned share, unearned policy, canonical active/accepted digests, and `weights_set`. Smoke passes should use `--no-set-weights` and omit `--set-commitment`; live weight writes require both `LEMMA_ENABLE_SET_WEIGHTS=1` and `--set-weights`, and live tempo commitments require `LEMMA_CANONICAL_PUBLISH_IPFS_API_URL`, `LEMMA_ENABLE_SET_COMMITMENT=1`, and `--set-commitment`. On commit-reveal subnets, the writer waits until the final 10 blocks of the tempo before submitting weights. Each attempted live write appends a public-safe receipt with the resolved UID vector or tempo commitment payload, network, netuid, success flag, sanitized client message, and extrinsic hash when available.
In production, set `LEMMA_REQUIRE_SUBMISSION_SIGNATURES=1`, `LEMMA_REQUIRE_COMMIT_REVEAL=1`, and `LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1`. Live miner authentication comes from the miner's chain commitment to the bucket Merkle root. Weak script identity can still be written as corpus metadata, but it does not receive paid reward.

After the validator pass, capture diagnostics again:

```bash
uv run lemma operator diagnostics --output operator-diagnostics-after.json
```

The after-run file carries the same public-safe readiness fields plus artifact counts for validator runs, verification receipts, score events, corpus JSONL files, and corpus rows written by the run.

## 4. Check And Publish Corpus Artifacts

Validate and index the corpus rows before sharing them:

```bash
uv run lemma corpus validate corpus/epoch-1.jsonl
uv run lemma corpus export --input corpus --output corpus/corpus-index.json
uv run lemma corpus benchmark-export \
  --input corpus \
  --output exports/lemma-proofs.jsonl \
  --index exports/index.json
```

The benchmark export is a dataset surface for downstream training and evaluation jobs. It is not a held-out benchmark claim.

Run the fixture-backed smoke for this full path with:

```bash
uv run pytest tests/test_operator_registry_flow.py -q
```

For copy-paste CLI commands against a tiny public fixture, see [Operator Smoke Example](../examples/operator-smoke/README.md).

## 5. Before Commit Or Push

Run the normal mechanical checks:

```bash
uv run ruff check .
uv run mypy lemma
uv run bandit -q -r lemma scripts -ll
uv run pip-audit --ignore-vuln PYSEC-2025-49 --ignore-vuln PYSEC-2022-42969
uv run pytest tests -q
uv run python scripts/leak_check.py
```

Do not publish wallets, local receipts, caches, logs, machine paths, private state, or unsanitized operator context.

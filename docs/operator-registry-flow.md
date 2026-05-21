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
  --citation-alpha 0.25 \
  --citation-weight-cap 100 \
  --output tasks/mainnet.registry.json
```

The procedural builder rejects paid candidates that do not carry procedural
depth-2 metadata, chain/drand anchoring, clean license state, deterministic
slot-weight receipt metadata, and a Lean-backed generation receipt. The receipt
must come from the `lean` gate runner, which runs typecheck, Prop, novelty, the
pinned triviality stack, and import/dependency slot-weight calculation before
paid activation.

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
LEMMA_PROCEDURAL_CITATION_ALPHA=0.25
LEMMA_PROCEDURAL_CITATION_WEIGHT_CAP=100
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

Inspect registry depth before accepting submissions:

```bash
uv run lemma operator registry-inspect
```

The command reports total, active, eligible, waiting, parked, and per-depth task counts from the configured registry.

Run the operator preflight before accepting submissions:

```bash
uv run lemma operator preflight
```

The command fails if the procedural source pool is not pinned in production mode, the active window cannot fill `K`, output directories cannot be prepared, or the Lean verifier backend is not configured.
It emits a versioned JSON report with `schema_version`, `ok`, `registry_sha256`, `active_K`, `frontier_depth`, and `checks`.

For reproducible support/debugging, write a diagnostics file before accepting submissions:

```bash
uv run lemma operator diagnostics --output operator-diagnostics-before.json
```

The diagnostics file contains the preflight report, registry summary, artifact counts, registry hash, and current active task ids. It does not include environment variables, credentials, wallet names, hostnames, IPs, or local filesystem paths. The before-run file proves the validator was configured against the intended pinned source pool and active window.

## 3. Validate Submissions

Run the validator against task-bound miner submissions. Production uses bucket reveals:

```bash
uv run lemma validate \
  --once \
  --bucket-reveals-jsonl bucket-reveals.jsonl \
  --no-set-weights
```

For a development file inbox, use `--submission-spool submission-spool` instead. The spool accepts top-level `.json` and `.jsonl` submission files and moves consumed files to `processed/` after a successful validator pass. For a mainnet-shaped bucket reveal fixture, use `--bucket-reveals-jsonl bucket-reveals.jsonl`; the validator checks the miner Merkle root before scoring. Add `--verify-chain-commitments` to read miner commitments from chain, and add `--verify-drand-reveals` to decrypt ciphertexts and require the decrypted proof to match the reveal; production mode enables both checks for bucket reveals.

The validator rejects submissions outside the active window, task-version mismatches, target-hash mismatches, duplicate winning proofs, and policy failures. Rank-0 accepted proofs earn their deterministic active slot share; unsolved-slot value becomes `unearned_share` and is burned by default. Each pass appends one public-safe row to `validator-runs.jsonl` with the registry hash, active K, frontier depth, verified count, accepted unique count, corpus row count, unearned share, unearned policy, and `weights_set`. Smoke passes should use `--no-set-weights`; live chain writes require both `LEMMA_ENABLE_SET_WEIGHTS=1` and `--set-weights`. On commit-reveal subnets, the writer waits until the final 10 blocks of the tempo before submitting. Each attempted live write appends a public-safe `weight-submissions.jsonl` receipt with the resolved UID vector, weights, network, netuid, success flag, sanitized client message, and extrinsic hash when available.
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

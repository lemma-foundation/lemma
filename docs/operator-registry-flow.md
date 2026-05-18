# Operator Registry Flow

This is the end-to-end path for publishing a deterministic task registry, running a validator against pinned tasks, and exporting accepted proof data.

## 1. Build A Registry

Start from proof-erased Mathlib snapshot rows that follow the [Mathlib Extraction Contract](mathlib-extraction.md):

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input snapshot.jsonl \
  --output tasks/mathlib-snapshot.registry.json
```

The command writes deterministic `queue_position` values and prints `registry_sha256`. Keep the JSON artifact and the SHA256 together; validators should pin both.

`signed_by` and `signature` fields are archived metadata unless the validator is configured with an explicit registry-signature verifier. They are not a substitute for `LEMMA_TASK_REGISTRY_SHA256_EXPECTED`.

## 2. Configure The Active Window

Set the registry, its expected hash, and the deterministic active-window controls:

```bash
LEMMA_TASK_REGISTRY_URL=tasks/mathlib-snapshot.registry.json
LEMMA_TASK_REGISTRY_SHA256_EXPECTED=<registry_sha256>
LEMMA_ACTIVE_K=10
LEMMA_FRONTIER_DEPTH=0
LEMMA_ACTIVE_QUEUE_SEED=lemma-active-queue-v1
LEMMA_CORPUS_OUTPUT_DIR=corpus
LEMMA_OPERATOR_DATA_DIR=validator-data
```

`LEMMA_ACTIVE_K` is the paid throughput denominator. `LEMMA_FRONTIER_DEPTH` and registry depth control difficulty. Increasing queue depth must not change the reward denominator.

Inspect registry depth before accepting submissions:

```bash
uv run lemma operator registry-inspect
```

The command reports total, active, eligible, waiting, parked, and per-depth task counts from the configured registry.

Run the operator preflight before accepting submissions:

```bash
uv run lemma operator preflight
```

The command fails if the registry is not SHA-pinned, the active window cannot fill `K`, output directories cannot be prepared, or the Lean verifier backend is not configured.
It emits a versioned JSON report with `schema_version`, `ok`, `registry_sha256`, `active_K`, `frontier_depth`, and `checks`.

For reproducible support/debugging, write a diagnostics file before accepting submissions:

```bash
uv run lemma operator diagnostics --output operator-diagnostics-before.json
```

The diagnostics file contains the preflight report, registry summary, artifact counts, registry hash, and current active task ids. It does not include environment variables, credentials, wallet names, hostnames, IPs, or local filesystem paths. The before-run file proves the validator was configured against the intended pinned registry and active window.

## 3. Validate Submissions

Run the validator against task-bound miner submissions:

```bash
uv run lemma validate \
  --once \
  --submissions-jsonl submissions.jsonl \
  --no-set-weights
```

For a live file inbox, use `--submission-spool submission-spool` instead. The spool accepts top-level `.json` and `.jsonl` submission files and moves consumed files to `processed/` after a successful validator pass.

The validator rejects submissions outside the active window, task-version mismatches, target-hash mismatches, duplicate winning proofs, and policy failures. Accepted unique proofs earn `credit / K`; unsolved-slot value becomes `unearned_share` and is burned by default. Each pass appends one public-safe row to `validator-runs.jsonl` with the registry hash, active K, frontier depth, verified count, accepted unique count, corpus row count, unearned share, unearned policy, and `weights_set`. In the current rewrite, weights are computed and `weights_set` remains false until a tested chain writer is added.

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
uv run ruff check lemma tests
uv run mypy lemma
uv run pytest tests -q
uv run python scripts/leak_check.py
```

Do not publish wallets, local receipts, caches, logs, machine paths, private state, or unsanitized operator context.

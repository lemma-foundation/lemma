# Operator Registry Flow

This is the end-to-end path for publishing a deterministic task registry, running a validator against pinned tasks, and exporting accepted proof data.

## 1. Build A Registry

Start from proof-erased Mathlib snapshot rows:

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input snapshot.jsonl \
  --output tasks/mathlib-snapshot.registry.json
```

The command writes deterministic `queue_position` values and prints `registry_sha256`. Keep the JSON artifact and the SHA256 together; validators should pin both.

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

## 3. Validate Submissions

Run the validator against task-bound miner submissions:

```bash
uv run lemma validate \
  --once \
  --submissions-jsonl submissions.jsonl \
  --no-set-weights
```

The validator rejects submissions outside the active window, task-version mismatches, target-hash mismatches, duplicate winning proofs, and policy failures. Accepted unique proofs earn `credit / K`; unsolved-slot value becomes `unearned_share` and is burned by default.

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

## 5. Before Commit Or Push

Run the normal mechanical checks:

```bash
uv run ruff check lemma tests
uv run mypy lemma
uv run pytest tests -q
uv run python scripts/leak_check.py
```

Do not publish wallets, local receipts, caches, logs, machine paths, private state, or unsanitized operator context.

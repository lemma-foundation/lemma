# Operator Smoke Example

This example runs the public registry-to-validator-to-corpus loop with a tiny proof-erased Mathlib-style snapshot that follows the [Mathlib extraction contract](../../docs/mathlib-extraction.md).

The fixture has 10 active `True` tasks at `queue_depth=0` and one deeper parked task at `queue_depth=2`. With `LEMMA_ACTIVE_K=10`, one accepted proof earns `0.1`, and the remaining `0.9` routes to the default burn rail.

## Fast Contract Smoke

Run the fixture-backed test without Docker:

```bash
uv run pytest tests/test_operator_registry_flow.py -q
```

## Manual CLI Flow

Create a local scratch directory:

```bash
export WORK=.lemma-operator-smoke
mkdir -p "$WORK/tasks" "$WORK/corpus" "$WORK/operator" "$WORK/exports"
```

Build a deterministic registry and capture its SHA256:

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input examples/operator-smoke/snapshot.jsonl \
  --output "$WORK/tasks/mathlib-snapshot.registry.json" \
  --seed operator-smoke \
  --frontier-depth 0 \
  | tee "$WORK/build.json"

export LEMMA_PREFER_PROCESS_ENV=1
export LEMMA_TASK_REGISTRY_URL="$WORK/tasks/mathlib-snapshot.registry.json"
export LEMMA_TASK_REGISTRY_SHA256_EXPECTED="$(
  uv run python -c 'import json, pathlib, sys; print(json.loads((pathlib.Path(sys.argv[1]) / "build.json").read_text())["registry_sha256"])' "$WORK"
)"
export LEMMA_ACTIVE_K=10
export LEMMA_FRONTIER_DEPTH=0
export LEMMA_ACTIVE_QUEUE_SEED=operator-smoke
export LEMMA_CORPUS_OUTPUT_DIR="$WORK/corpus"
export LEMMA_OPERATOR_DATA_DIR="$WORK/operator"
```

Inspect supply and run preflight:

```bash
uv run lemma operator registry-inspect
uv run lemma operator preflight
uv run lemma operator diagnostics --output "$WORK/operator-diagnostics-before.json"
```

Build one task-bound submission:

```bash
uv run lemma submit \
  lemma.mathlib_snapshot.operator_smoke_true_0 \
  --submission examples/operator-smoke/Submission.lean \
  --solver-hotkey miner-active \
  --output "$WORK/submission.json"

uv run python -c 'import json, pathlib; w=pathlib.Path(".lemma-operator-smoke"); w.joinpath("submissions.jsonl").write_text(json.dumps(json.loads(w.joinpath("submission.json").read_text()), sort_keys=True)+"\n")'
```

Run one validator pass:

```bash
uv run lemma validate \
  --once \
  --submissions-jsonl "$WORK/submissions.jsonl" \
  --validator-hotkey validator-smoke \
  --no-set-weights

uv run lemma operator diagnostics --output "$WORK/operator-diagnostics-after.json"
```

Expected output fragments:

```json
{
  "accepted_unique": 1,
  "scores": {
    "miner-active": 0.1
  },
  "weights": {
    "burn_uid:0": 0.9,
    "miner-active": 0.1
  },
  "weights_set": false
}
```

Validate and export the corpus:

```bash
uv run lemma corpus validate "$WORK/corpus/epoch-local.jsonl"
uv run lemma corpus export --input "$WORK/corpus" --output "$WORK/exports/corpus-index.json"
uv run lemma corpus benchmark-export \
  --input "$WORK/corpus" \
  --output "$WORK/exports/lemma-proofs.jsonl" \
  --index "$WORK/exports/benchmark-index.json"
```

The manual validator pass uses the configured Lean verifier. The pytest smoke uses a verifier test double and checks the protocol plumbing without Docker.

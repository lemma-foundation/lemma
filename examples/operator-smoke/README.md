# Operator Smoke Example

This example runs the public registry-to-validator-to-accepted-proof loop with the fixed dev task in `tasks/registry.json`.

The fixture has one active `True` task. One accepted proof earns its verifier-recorded slot weight, and the remaining share routes to the default burn rail when more paid slots are configured.

## Fast Contract Smoke

Run the registry-backed operator test without Docker:

```bash
uv run pytest tests/test_operator_registry_flow.py -q
```

## Manual CLI Flow

Create a local scratch directory:

```bash
export WORK=.lemma-operator-smoke
mkdir -p "$WORK/corpus" "$WORK/operator"
```

Configure the dev registry and capture its SHA256:

```bash
export LEMMA_PREFER_PROCESS_ENV=1
export LEMMA_TASK_REGISTRY_URL=tasks/registry.json
export LEMMA_TASK_REGISTRY_SHA256_EXPECTED="$(
  uv run python -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("tasks/registry.json").read_bytes()).hexdigest())'
)"
export LEMMA_TASK_SUPPLY_MODE=registry
export LEMMA_ACTIVE_K=1
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
  lemma.sample.true_intro \
  --submission examples/operator-smoke/Submission.lean \
  --solver-hotkey miner-active \
  --output "$WORK/submission.json"

uv run python -c 'import json, pathlib, sys; w=pathlib.Path(sys.argv[1]); w.joinpath("submissions.jsonl").write_text(json.dumps(json.loads(w.joinpath("submission.json").read_text()), sort_keys=True)+"\n")' "$WORK"
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

The validator pass appends one public-safe run row to `$WORK/operator/validator-runs.jsonl`; the after diagnostics file counts that row with the other local artifacts.

Expected output fragments:

```json
{
  "accepted_unique": 1,
  "corpus_rows": 1,
  "scores": {
    "miner-active": "<slot weight>"
  },
  "weights_set": false
}
```

# Validator Guide

Validators verify and publish accepted solutions.

In v1, validators run the pinned Lean checker, score accepted proofs, and write replayable corpus rows.

## Basic Flow

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma worker --check
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
```

Live weight submission is an explicit operator action:

```bash
LEMMA_ENABLE_SET_WEIGHTS=1 uv run lemma validate --once --submission-spool submission-spool --set-weights
```

Use `--no-set-weights` for smoke passes and corpus-only validation.

`lemma validate` loads the task registry, validates miner submissions, dispatches to the domain verifier adapter, writes verification results, writes score events, writes a public-safe `validator-runs.jsonl` summary row, and publishes corpus JSONL deltas.
When `--set-weights` is enabled, each chain-write attempt also appends a public-safe `weight-submissions.jsonl` receipt under `LEMMA_OPERATOR_DATA_DIR` with the resolved UID vector, weights, network, netuid, success flag, sanitized client message, and extrinsic hash when available.
After configuring a pinned registry hash, `lemma operator preflight` checks registry pinning, active-window size, local output directories, and Lean verifier configuration.
Use `lemma operator diagnostics --output operator-diagnostics-before.json` before a validator pass and `lemma operator diagnostics --output operator-diagnostics-after.json` after it. The before file captures registry readiness; the after file adds public-safe artifact counts for the run.
For a file-based live loop, set `LEMMA_SUBMISSION_SPOOL_DIR` or pass `--submission-spool`. Pending top-level `.json` and `.jsonl` files are read once and moved to `processed/` after validation succeeds.

## Runtime Steps

1. Load the active task registry.
2. Select the deterministic active window from `LEMMA_ACTIVE_K`, `LEMMA_FRONTIER_DEPTH`, and `LEMMA_ACTIVE_QUEUE_SEED`.
3. Query or receive miner submissions.
4. Reject submissions outside the active window.
5. Reject task-version and target-hash mismatches.
6. Require signatures for live miner responses.
7. Run the domain submission policy scan before the verifier.
8. Verify with the adapter-selected pinned runtime.
9. Score first accepted unique proof per active task as `credit / K`.
10. Track `unearned_share = 1 - sum(miner_weights)`.
11. Burn unearned share by default; do not redistribute it to current solvers.
12. Write corpus rows for valid unique proofs after the scoring window closes.

## Worker

```bash
uv run lemma worker --serve --host localhost --port 8787
```

Non-loopback worker binds require `LEMMA_LEAN_VERIFY_REMOTE_BEARER` unless explicitly allowed for development.

## No Subjective Scoring

Validators score proofs, not reasoning prose, model names, proof style, or claimed effort. Lean remains the only production verifier today.

# Validator Guide

Validators are proof checkers and corpus publishers.

## Basic Flow

```bash
uv sync --extra btcli
uv run lemma setup
uv run lemma worker --check
uv run lemma validate --once --no-set-weights
```

`lemma validate` loads the task registry, validates miner submissions, runs Lean, writes verification results, writes score events, and publishes corpus JSONL deltas.

## Runtime Steps

1. Load the active task registry.
2. Query or receive miner submissions.
3. Reject inactive task IDs.
4. Reject task-version and target-hash mismatches.
5. Require signatures for live miner responses.
6. Run the submission policy scan before Lean.
7. Verify in Docker or a configured Lean worker.
8. Score first accepted unique proof per task as `verified_unique_wins / K`.
9. Normalize credits into Bittensor weights for miners that earned credit.
10. Leave weights unchanged if no miner earns credit.
11. Write corpus rows for valid unique proofs after the scoring window closes.

## Worker

```bash
uv run lemma worker --serve --host localhost --port 8787
```

Non-loopback worker binds require `LEMMA_LEAN_VERIFY_REMOTE_BEARER` unless explicitly allowed for development.

## No Subjective Scoring

Validators score proof artifacts, not reasoning prose, model names, proof style, or claimed effort.

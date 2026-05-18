# Production

Production Lemma is the verifier-grounded data loop:

1. publish an active task registry;
2. receive miner artifact submissions;
3. verify each artifact with the pinned domain environment;
4. score first accepted unique artifacts;
5. compute miner weights as `credit / K` and burn unearned share by default;
6. publish accepted corpus rows and a small corpus index.

## Operator Rules

- Do not route subnet owner emissions through a contract.
- Do not use escrow-style reward custody for v1 rewards.
- Do not score prose, model branding, or claimed effort.
- Keep task, submission, verifier, scoring, and corpus artifacts replayable.
- Delay public proof release until the scoring window closes.
- Keep `.env`, wallets, local state, logs, caches, and machine paths out of commits.

## Commands

```bash
uv run lemma status
uv run lemma worker --check
uv run lemma operator preflight
uv run lemma worker --serve --host localhost --port 8787
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Corpus deltas are written under `LEMMA_CORPUS_OUTPUT_DIR`. Local receipts are written under `LEMMA_OPERATOR_DATA_DIR`. If `LEMMA_SUBMISSION_SPOOL_DIR` is set, validators consume pending `.json` or `.jsonl` submission files from that directory and move them to `processed/` after a successful pass. These paths should remain ignored unless an operator intentionally publishes sanitized artifacts.

For the full registry-to-validator-to-export sequence, see [Operator Registry Flow](operator-registry-flow.md).

Run the leak check before any commit or push:

```bash
uv run python scripts/leak_check.py
```

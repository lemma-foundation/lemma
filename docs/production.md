# Production

Production Lemma is the proof-data loop:

1. publish an active task registry;
2. receive miner proof submissions;
3. verify each proof with the pinned Lean environment;
4. score first accepted unique proofs;
5. set miner weights as `credit / K` and burn unearned share by default;
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
uv run lemma validate --once --no-set-weights
```

Corpus deltas are written under `LEMMA_CORPUS_OUTPUT_DIR`. Local receipts are written under `LEMMA_OPERATOR_DATA_DIR`; both paths should remain ignored unless an operator intentionally publishes sanitized artifacts.

For the full registry-to-validator-to-export sequence, see [Operator Registry Flow](operator-registry-flow.md).

Run the leak check before any commit or push:

```bash
uv run python scripts/leak_check.py
```

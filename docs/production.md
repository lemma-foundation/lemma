# Production

Lemma v1 production is the proof-data loop:

1. publish an active task registry;
2. receive miner proof submissions;
3. verify each proof with the pinned Lean environment;
4. score first accepted unique proofs;
5. set normal Bittensor miner weights;
6. publish accepted rows to the Lemma Corpus.

## Operator Rules

- Do not route subnet owner emissions through a contract.
- Do not use bounty escrow for v1 rewards.
- Do not score prose, model branding, or claimed effort.
- Keep task, submission, verifier, scoring, and corpus artifacts replayable.
- Delay public proof release until the scoring window closes.

## Verifier Worker

Run a local verifier preflight:

```bash
uv run lemma validate --check
```

Run the optional HTTP worker:

```bash
uv run lemma validate --worker --host localhost --port 8787
```

Non-loopback worker binds require `LEMMA_LEAN_VERIFY_REMOTE_BEARER` unless explicitly allowed for development.

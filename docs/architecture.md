# Architecture

Lemma v1 is a small proof-data pipeline.

```text
task registry -> miner prover -> task-bound submission -> Lean verifier
              -> verification result -> score event -> Bittensor weights
              -> corpus JSONL
```

## Implemented Modules

- `lemma.tasks`: task schema, registry loading, registry hash, task-to-problem conversion.
- `lemma.task_supply`: deterministic dev-seed tasks and activation gates.
- `lemma.submissions`: proof package schema, proof hashing, target binding, signing payloads.
- `lemma.protocol`: task request and proof response payloads.
- `lemma.miner`: local-command prover adapter, local verification, one-shot submission build.
- `lemma.validator`: submission validation, Lean verification calls, scoring, corpus writing.
- `lemma.scoring`: verification-result and score-event models plus pure first-valid-unique proof scoring.
- `lemma.corpus`: row building, JSONL validation/replay, corpus index building.
- `lemma.store`: append-only local JSONL store helpers.
- `lemma.lean`: Docker or worker-backed Lean verification.

## Boundaries

Scoring is pure. Lean verification does not know about Bittensor weights. Provider/model logic stays on the miner side. Validators score artifacts, not providers.

Lemma v1 does not custody funds and does not route owner emissions through contracts. Rewards flow through normal Bittensor miner and validator mechanics.

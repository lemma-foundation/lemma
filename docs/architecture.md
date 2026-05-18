# Architecture

Lemma is an open Lean proof-data engine.

```text
supply streams
  -> task filter
  -> deterministic active pool
  -> miners search for proofs
  -> validators run Lean
  -> proof-unit scoring
  -> unearned-share burn/recycle policy
  -> replayable public corpus
```

## Implemented Spine

- `lemma.tasks`: task schema, provenance, registry loading, target hashing.
- `lemma.task_supply`: dev-seed tasks and activation gates.
- `lemma.supply`: deterministic queue, curriculum controller, and fixture-backed supply stream interfaces.
- `lemma.submissions`: task-bound proof package schema and signing payloads.
- `lemma.miner`: local-command prover adapter, local verification, one-shot submission build.
- `lemma.validator`: submission validation, Lean verification calls, scoring, corpus writing.
- `lemma.scoring`: first-valid-unique scoring with `credit / K` miner weights and unearned-share accounting.
- `lemma.corpus`: replayable row building, JSONL validation/replay, corpus indexing.
- `lemma.lean`: Docker or worker-backed Lean verification.
- `lemma.chain`: typed future interfaces for commitments, drand, weights, and burn/recycle rails.

## Controllers

`frontier_depth` is the protocol difficulty proxy and is driven by EMA solve rate. `active_K` is the throughput target and is driven by validator capacity. A low or zero solve rate halts frontier advancement and requests hard-target variants; it does not step the queue head backward into already exposed tasks.

## Boundaries

Scoring is pure. Lean verification does not know about Bittensor weights. Provider/model logic stays on the miner side. Validators score artifacts, not providers.

Lemma v1 does not custody funds and does not route owner emissions through contracts. Rewards flow through normal Bittensor miner and validator mechanics.

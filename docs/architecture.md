# Architecture

Lemma is a verifier-grounded data engine. Lean theorem proving is the only production domain for now.

```text
supply streams
  -> task filter
  -> deterministic active pool
  -> miners search for accepted artifacts
  -> validators dispatch to the verifier adapter
  -> verified-unit scoring
  -> unearned-share burn/recycle policy
  -> replayable graph-shaped public corpus
```

## Implemented Spine

- `lemma.tasks`: task schema, provenance, registry loading, target hashing.
- `lemma.task_supply`: dev-seed tasks and activation gates.
- `lemma.supply`: deterministic queue, curriculum controller, and fixture-backed supply stream interfaces.
- `lemma.submissions`: task-bound proof package schema and signing payloads.
- `lemma.miner`: local-command prover adapter, adapter-backed local verification, one-shot submission build.
- `lemma.validator`: submission validation, verifier registry calls, scoring, corpus writing.
- `lemma.scoring`: first-valid-unique scoring with `credit / K` miner weights and unearned-share accounting.
- `lemma.verifiers`: domain-neutral verifier adapter contract, Lean adapter, registry, disabled Verus stub.
- `lemma.corpus`: replayable row building, JSONL validation/replay, corpus indexing, v2 row/export helpers.
- `lemma.graph`: row-level graph nodes and dependency edges used by corpus exports.
- `lemma.lean`: Docker or worker-backed Lean verification.
- `lemma.chain`: typed future interfaces for commitments, drand, weights, and burn/recycle rails.

## Controllers

`frontier_depth` is the protocol difficulty proxy and is driven by EMA solve rate. `active_K` is the throughput target and is driven by validator capacity. A low or zero solve rate halts frontier advancement and requests hard-target variants; it does not step the queue head backward into already exposed tasks.

## Boundaries

Scoring is pure. Verifiers do not know about Bittensor weights. Provider/model logic stays on the miner side. Validators score artifacts, not providers.

Lemma does not custody funds and does not route owner emissions through contracts. Rewards flow through normal Bittensor miner and validator mechanics.

Lean is the only enabled production domain. Any future domain has to enter through the verifier adapter contract and publish the same graph-shaped corpus row v2 shape.

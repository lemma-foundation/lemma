# Architecture

Lemma has three production layers: Lean theorem supply, Lean proof verification, and reusable proof-data publication.

Lean theorem proving is the active path.

```text
Lean theorem supply
  -> miner proof-search agent
  -> task-bound Lean submission
  -> pinned Lean verification
  -> first-accepted scoring
  -> corpus row export
  -> dependency/citation graph
```

## Implemented Spine

- `lemma.tasks`: Lean task schema, provenance, registry loading, target hashing.
- `lemma.task_supply`: dev-seed tasks and activation gates.
- `lemma.supply`: deterministic queue, curriculum controller, and fixture-backed supply stream interfaces.
- `lemma.supply.procedural`: production-shaped procedural depth-2 registry builder.
- `lemma.supply.mixed`: non-production mixed-supply builder for local and curriculum work.
- `lemma.submissions`: task-bound proof package schema and signing payloads, including commit/reveal fields.
- `lemma.miner`: local-command prover adapter, adapter-backed local verification, one-shot submission build.
- `lemma.validator`: submission validation, verifier registry calls, scoring, corpus writing.
- `lemma.scoring`: first-valid-unique scoring with deterministic active slot weights and unearned-share accounting.
- `lemma.verifiers`: verifier adapter contract, Lean adapter, registry, disabled research adapters.
- `lemma.corpus`: replayable row building, JSONL validation/replay, corpus indexing, v2 row/export helpers.
- `lemma.graph`: row-level graph nodes and dependency edges used by corpus exports.
- `lemma.lean`: Docker or worker-backed Lean verification.
- `lemma.chain`: typed future interfaces for commitments, drand, weights, and burn/recycle rails.

## Controllers

`frontier_depth` is the protocol difficulty proxy and `active_K` is the throughput target. In production they come from the latest published curriculum tempo state before the active chain tempo. Validators write that retarget state into canonical public artifacts after each pass, and miners/validators replay the same state before prebuilding the next active registry. Tempo is the chain tempo; on SN467 that is currently 360 blocks.

## Boundaries

Scoring is pure. Verifiers do not know about Bittensor weights. Provider/model logic stays on the miner side. Validators score proofs, not providers.

Lemma does not custody funds and does not route owner emissions through contracts. Rewards flow through normal Bittensor miner and validator mechanics.

The production architecture is Lean-first and proof-agent-first. Generic verifier adapters are internal/research extension points, not the public product. Public docs should describe the active Lean competition unless they are explicitly marked as research.

`LEMMA_PROTOCOL_MODE=production` fails closed unless `LEMMA_ENABLED_DOMAINS` is exactly `lean`, procedural supply uses a pinned public source pool, explicit prior-substrate mirror, public source-pool receipt, public novelty cache, and public import graph, paid tasks are depth-2 and generated from chain/drand epoch randomness with the chain-pinned Lean AST/elaborator mutation bundle and drand-keyed params, live miner submissions are hotkey-authenticated, commit/reveal fields are required, strong proof identity is required for reward, and Lean verifier networking is disabled. File submissions authenticate by signature; bucket-path submissions authenticate by the miner's chain commitment.

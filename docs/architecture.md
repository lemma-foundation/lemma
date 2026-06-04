# Architecture

Lemma has three production layers: Lean task supply, Lean proof verification, and Proof Atlas publication.

Lean theorem proving is the active path.

```text
pinned real-task registry
  -> miner proof-search agent
  -> task-bound Lean submission
  -> pinned Lean verification
  -> first-accepted scoring
  -> accepted proof row
  -> Proof Atlas export
```

## Implemented Spine

- `lemma.tasks`: Lean task schema, provenance, registry loading, target hashing.
- `lemma.task_supply`: fixed local smoke fixtures and activation gates.
- `lemma.task_activation`: active-window eligibility, balancing, and slot weights.
- `lemma.submissions`: task-bound proof package schema and signing payloads, including commit/reveal fields.
- `lemma.miner`: local-command prover adapter, adapter-backed local verification, one-shot submission build.
- `lemma.validator`: submission validation, verifier registry calls, scoring, accepted-proof writing.
- `lemma.scoring`: first-valid-unique scoring with deterministic active slot weights and unearned-share accounting.
- `lemma.verifiers`: verifier adapter contract, Lean adapter, registry, disabled research adapters.
- `lemma.corpus`: internal accepted-proof row building, JSONL validation/replay, indexing, and v2 export helpers.
- `lemma.graph`: row-level graph nodes and dependency edges used by Proof Atlas exports.
- `lemma.lean`: Docker or worker-backed Lean verification.
- `lemma.chain`: typed interfaces for commitments, drand, weights, and burn/recycle rails.

## Controllers

`frontier_depth` is the protocol difficulty proxy and `active_K` is the throughput target. When curriculum retargeting is enabled, production validators load the latest eligible public curriculum state with one full tempo of replay lag before a retarget row can affect active selection. Solve rate moves the frontier; validator capacity and the public cost budget cap `K`, so deeper frontiers can run fewer tasks without making validation expensive. The subnet tempo stays fixed.

Active selection starts from the pinned registry and public randomness. Registry cache files speed startup, but they do not create task authority.

## Boundaries

Scoring is pure. Verifiers do not know about Bittensor weights. Provider/model logic stays on the miner side. Validators score proofs, not providers.

Lemma does not custody funds and does not route owner emissions through contracts. Rewards flow through normal Bittensor miner and validator mechanics.

The production architecture is Lean-first and proof-agent-first. Generic verifier adapters are internal/research extension points, not the public product. Public docs should describe the active Lean competition unless they are explicitly marked as research.

`LEMMA_PROTOCOL_MODE=production` fails closed unless `LEMMA_ENABLED_DOMAINS` is exactly `lean`, `LEMMA_TASK_SUPPLY_MODE=registry`, the registry bytes match `LEMMA_TASK_REGISTRY_SHA256_EXPECTED`, paid tasks are real missing-proof rows with public source metadata, live miner submissions are hotkey-authenticated, commit/reveal fields are required, strong proof identity is required for reward, and Lean verifier networking is disabled. File submissions authenticate by signature; bucket-path submissions authenticate by the miner's chain commitment.

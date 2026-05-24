# How It Works

Lemma turns theorem proving into an open competition for proof-search agents.

```text
Lean theorem task -> proof-search competition -> pinned Lean verification -> reward credit -> replayable proof record
```

## The Loop

1. Validators derive the same active Lean theorem-proving tasks.
2. Miners run proof-search agents using any stack they want: tactics, retrieval, local models, hosted APIs, search, or custom heuristics.
3. Miners commit timelocked bucket blobs, then reveal task-bound proof packages.
4. Validators run the pinned Lean toolchain.
5. The rank-0 unique passing proof for each active task earns epoch credit.
6. Validators compute miner weights from deterministic active slot weights; unsolved-slot value is burned by default instead of redistributed.
7. Accepted proofs become replayable theorem/proof records. The subnet owner publishes canonical snapshots, and other validators can publish the same kind of mirrors if they configure their own storage; it is not mandatory for validation.

## Lean Is The Judge

Lean theorem proving is the active path. The correctness boundary is the pinned Lean verifier. Explanations, model names, and claimed effort are not scored.

A submitted Lean proof passes or fails.

## Replayable Records

Accepted proofs become verified theorem/proof records. Each row links the task, theorem statement, source, verifier, proof, proof identity, solver, validator, dependencies, and replay metadata.

Corpus releases and dataset exports can be built from those records. They can support retrieval, evaluation, training, and future proof search, but that value is a byproduct of verified proof competition.

## Why Bittensor

Bittensor gives Lemma an open miner/validator network. Lemma gives that network a clean mathematical target: produce Lean proofs that verify.

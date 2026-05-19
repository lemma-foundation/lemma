# How It Works

Lemma turns Lean theorem-proving work into verified mathematical corpus entries.

```text
Lean theorem task -> proof search -> pinned Lean verification -> proof-unit credit -> public mathematical corpus
```

## The Loop

1. Validators publish active Lean theorem-proving tasks.
2. Miners search for Lean proofs using any stack they want: tactics, retrieval, local models, hosted APIs, or human insight.
3. Miners submit a task-bound proof package.
4. Validators run the pinned Lean toolchain.
5. The first unique passing proof for each active task earns epoch credit.
6. Validators compute miner weights as `credit / K`; unsolved-slot value is burned by default instead of redistributed.
7. Accepted proofs become replayable public corpus rows.

## The Checker Is The Judge

Lean theorem proving is the active production domain. The correctness boundary is the pinned Lean verifier. Explanations, model names, and claimed effort are not scored.

A submitted Lean proof passes or fails.

## The Corpus Output

Accepted proofs become verified theorem/proof records. Each row links the task, theorem statement, source, verifier, proof, proof identity, solver, validator, dependencies, and replay metadata.

## Why Bittensor

Bittensor gives Lemma an open miner/validator network. Lemma gives that network a clean mathematical target: produce Lean proofs that verify. The reward is tied to checked work, and the output becomes public mathematical infrastructure.

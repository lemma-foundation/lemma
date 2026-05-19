# How It Works

Lemma turns reasoning tasks into verified training examples.

```text
task -> solution search -> deterministic verification -> proof-unit credit -> public verified data
```

## The Loop

1. Validators expose active formal reasoning tasks.
2. Miners search for solutions using any stack they want: tactics, retrieval, local models, hosted APIs, or human insight.
3. Miners submit a task-bound proof.
4. Validators run the pinned Lean verifier.
5. The first unique passing proof for each active task earns epoch credit.
6. Validators compute miner weights as `credit / K`; unsolved-slot value is burned by default instead of redistributed.
7. Accepted proofs become replayable public corpus rows.

## The Checker Is The Judge

Lean theorem proving is the only active production domain today. The correctness boundary is the pinned Lean verifier. Explanations, model names, and claimed effort are not scored.

The verifier is the judge. A submitted proof passes or fails.

## The Data Output

Accepted proofs become verified reasoning data. Each row links the task, source, verifier, proof, proof identity, solver, validator, dependencies, and replay metadata.

## Future Domains

The architecture is domain-neutral, but production is not. A future domain must provide a deterministic verifier, task schema, submission schema, sandboxing policy, scoring rule, and corpus row normalization before it can become an active Lemma domain.

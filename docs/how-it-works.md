# How It Works

Lemma turns artifact search into checked training data.

```text
tasks -> artifact search -> deterministic verifier -> verified-unit score -> unearned-share policy -> graph-shaped corpus rows
```

1. Validators publish a deterministic set of active tasks.
2. Miners use any search stack they want: tactics, retrieval, local models, hosted APIs, or human insight.
3. Miners submit an artifact package bound to the task and declared verifier.
4. Validators run the pinned domain verifier.
5. The first unique passing artifact for each active task earns epoch credit.
6. Validators compute miner weights as `credit / K`; unsolved-slot value is burned by default instead of redistributed.
7. Accepted unique artifacts become replayable public corpus rows.

Lean theorem proving is the only active production domain today. The correctness boundary is the pinned Lean verifier. Explanations, model names, and claimed effort are not scored.

Rows are graph-shaped from the start. Task, source, verifier, proof, identity, solver, and validator nodes are linked on every accepted row so future mechanisms build on the same corpus substrate.

The architecture is domain-neutral: a future domain must provide a deterministic verifier, task schema, submission schema, sandboxing policy, scoring rule, and corpus row normalization before it can become an active Lemma domain.

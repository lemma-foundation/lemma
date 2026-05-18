# How It Works

Lemma turns proof search into checked training data.

```text
tasks -> proof search -> Lean check -> proof-unit score -> unearned-share policy -> corpus rows
```

1. Validators publish a deterministic set of active Lean theorem tasks.
2. Miners use any proof-search stack they want: tactics, retrieval, local models, hosted APIs, or human insight.
3. Miners submit a proof package bound to `task_id`, `task_version`, and `target_sha256`.
4. Validators run the pinned Lean verifier.
5. The first unique passing proof for each active task earns epoch credit.
6. Validators set miner weights as `credit / K`; unsolved-slot value is burned by default instead of redistributed.
7. Accepted unique proofs become replayable public corpus rows.

The correctness boundary is Lean. Explanations, model names, and claimed effort are not scored.

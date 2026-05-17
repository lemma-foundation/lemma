# Model APIs And Prover Adapters

Lemma should not require a specific model provider.

Miners may use hosted APIs, local models, theorem-proving agents, LeanDojo, tactic search, or human-written proofs.

## Network API vs Model API

Lemma provides the network API:

- fetch active tasks;
- submit proof artifacts;
- check status;
- inspect corpus rows.

Miners provide the model API:

- Chutes;
- Gemini;
- OpenAI-compatible endpoints;
- local vLLM/SGLang;
- custom provers.

## Future Adapter Contract

Input to prover:

```json
{
  "task_id": "...",
  "submission_stub": "...",
  "statement": "...",
  "imports": ["Mathlib"],
  "timeout_s": 300
}
```

Output from prover:

```json
{
  "task_id": "...",
  "proof_script": "...",
  "metadata": {
    "provider": "optional",
    "model": "optional"
  }
}
```

The current v1 slice keeps provider logic outside the core CLI. A future adapter should support `local-command` first. Hosted providers are optional.

# Model APIs

Lemma core does not depend on a model provider. Miners choose their own proof-search stack.

## Local Command

The first adapter is a local command:

```bash
LEMMA_PROVER_COMMAND="python prover.py"
uv run lemma mine --once
```

Input on stdin:

```json
{
  "task_id": "lemma.generated.001",
  "task_version": 1,
  "statement": "...",
  "imports": ["Mathlib"],
  "submission_stub": "...",
  "timeout_s": 300
}
```

Output on stdout:

```json
{
  "task_id": "lemma.generated.001",
  "proof_script": "import Mathlib\n\nnamespace Submission\n...",
  "metadata": {
    "provider": "optional",
    "model": "optional"
  }
}
```

## OpenAI-Compatible Endpoints

Optional hosted endpoints use:

```text
LEMMA_PROVER_BASE_URL
LEMMA_PROVER_API_KEY
LEMMA_PROVER_MODEL
```

This is provider-neutral. Provider metadata is optional and is not part of scoring.

OpenAI-compatible endpoints can point at OpenAI, Chutes, Gemini-compatible gateways, local vLLM servers, or custom HTTP prover services. The only required output is a Lean proof script for the requested task.

Because the prover runs in an automated loop tied to on-chain rewards, use a provider whose terms permit that — self-hosted/open-weight models, or an API whose terms explicitly allow automated, competitive use. Consumer coding-assistant subscriptions (e.g. Cursor/Codex/Claude) are for building tooling, not for serving the runtime prover.

## Custom HTTP Provers

Adapters should keep provider credentials outside submission packages. A validator scores only the task-bound proof and its Lean verification result, not the model name, prompt, chain of thought, or informal explanation.

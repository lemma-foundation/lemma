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

## Custom HTTP Provers

Adapters should keep provider credentials outside submission packages. A validator scores only the task-bound proof and its Lean verification result, not the model name, prompt, chain of thought, or informal explanation.

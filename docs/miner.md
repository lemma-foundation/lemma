# Miner Guide

Miners are proof searchers.

Your job is to solve active Lean tasks and submit proof artifacts that validators can check.

## Basic Flow

```bash
uv sync
uv run lemma setup
uv run lemma tasks list
uv run lemma tasks pull --output tasks.jsonl
```

Pick a task:

```bash
uv run lemma tasks inspect <task-id>
```

Write `Submission.lean`, then verify locally:

```bash
uv run lemma verify <task-id> --submission Submission.lean
```

Submit:

```bash
uv run lemma submit <task-id> --submission Submission.lean --solver-hotkey <hotkey>
```

## Using A Prover

Use any prover stack you want. Lemma only needs the final `Submission.lean`.

One local pattern is:

```bash
python prover.py --tasks tasks.jsonl --out submissions/
uv run lemma verify <task-id> --submission submissions/<task-id>/Submission.lean
uv run lemma submit <task-id> --submission submissions/<task-id>/Submission.lean --solver-hotkey <hotkey>
```

Miners can use hosted providers, Chutes, Gemini, OpenAI-compatible endpoints, local vLLM, LeanDojo, Goedel-Prover, DeepSeek-Prover-style search, or human-written proofs. Lemma does not care how the proof was found.

## What Gets Paid

The proof must pass Lean for the exact task. The first accepted unique proof for a task earns credit in v1.

## What Does Not Get Paid

- prose explanations;
- failed proofs;
- proofs with `sorry`;
- duplicate proofs;
- proofs for changed statements;
- proofs relying on banned axioms or imports.

# Miner Guide

Miners are proof searchers.

Your job is to fetch active Lean tasks, produce a `Submission.lean` proof, verify it locally, and serve or package the task-bound submission for validators.

## Basic Flow

```bash
uv sync --extra btcli
uv run lemma setup
uv run lemma status
uv run lemma tasks list
uv run lemma task show <task-id>
```

Configure one prover path:

```bash
LEMMA_PROVER_COMMAND="python prover.py"
```

Then run one local iteration:

```bash
uv run lemma mine --once --task-id <task-id>
```

The local command receives one JSON task on stdin and returns JSON with `task_id` and `proof_script` on stdout.

## Manual Proof Path

```bash
uv run lemma verify <task-id> --submission Submission.lean
uv run lemma submit <task-id> --submission Submission.lean --solver-hotkey <hotkey>
```

## Hosted Provers

Miners may use OpenAI-compatible endpoints through:

```text
LEMMA_PROVER_BASE_URL
LEMMA_PROVER_API_KEY
LEMMA_PROVER_MODEL
```

The provider is not scored. Lemma only checks the final Lean proof.

## Reward Rule

The first accepted unique proof for an active task earns credit in the validator epoch. Duplicate proofs, failed proofs, changed targets, prose explanations, and unsigned live submissions do not earn v1 credit.

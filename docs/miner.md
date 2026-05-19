# Miner Guide

Miners solve Lean theorem-proving tasks.

Your job is to fetch active tasks, produce a verifier-accepted proof, verify it locally, and serve or package the task-bound submission for validators. For Lean tasks, the proof is a `Submission.lean` file.

## Basic Flow

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
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
uv run lemma mine --once --task-id <task-id> --output submission.json
```

The local command receives one JSON task on stdin and returns JSON with `task_id` and `proof_script` on stdout. Live deployments wrap the proof in a timelocked chain commitment; local JSON output is the development harness.

## Manual Proof Path

```bash
uv run lemma verify <task-id> --submission Submission.lean
uv run lemma submit <task-id> --submission Submission.lean --solver-hotkey <hotkey>
```

The validator can ingest the resulting `submission.json` through its submission spool.

## Hosted Provers

Miners may use OpenAI-compatible endpoints through:

```text
LEMMA_PROVER_BASE_URL
LEMMA_PROVER_API_KEY
LEMMA_PROVER_MODEL
```

The provider is not scored. Lemma only checks the final Lean proof.

## Reward Rule

The first accepted unique proof for an active task earns one fixed-price verified unit in the validator epoch. Duplicate proofs, failed proofs, changed targets, prose explanations, and unsigned live submissions do not earn credit. Unsolved slots do not increase the payout for solved slots.

# Miner Guide

Miners solve Lean theorem-proving tasks.

Your job is to fetch active tasks, produce a verifier-accepted proof, verify it locally, and serve or package the task-bound submission for validators. For Lean tasks, the proof is a `Submission.lean` file.

The public CLI is only the reference path. It proves the protocol can be used end to end; it is not an optimization framework or the expected ceiling for serious miners.

## Basic Flow

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma status
```

Configure one prover path:

```bash
LEMMA_PROVER_COMMAND="python prover.py"
```

Then run one local iteration:

```bash
uv run lemma mine --once
uv run lemma mine --once --output submission.json
```

The configured prover command receives one JSON task on stdin and returns JSON with `task_id` and `proof_script` on stdout. Live deployments wrap the proof in a timelocked chain commitment; local JSON output is the development harness.

## Custom Miners

Competitive miners can replace the CLI entirely. The contract is the task registry plus a valid task-bound proof submission; how a miner gets there is open. Agents, custom Lean worker pools, model-training loops, remote schedulers, direct protocol clients, or non-Python implementations are all fine if the validator accepts the output.

Mainnet-shaped runs write timelocked blobs to the miner bucket and anchor rank with a Merkle-root chain commitment. The advanced helper packages local submissions into the exact public bucket keys validators poll:

```bash
uv run lemma miner bucket publish \
  --submission submission.json \
  --tempo <tempo> \
  --drand-round <round> \
  --miner-hotkey <hotkey-ss58> \
  --output-dir validator-data/miner-bucket \
  --s3-uri s3://<public-bucket>/<miner-prefix> \
  --verify-upload \
  --submit-commitment
```

The command writes only ciphertext blobs under `tempo_<t>/slot_<i>.bin`, checks the uploaded bytes when `--verify-upload` is set, and prints the `lemma-bucket:<tempo>:<round>:<merkle-root>` commitment payload. Keep proof plaintext local until the Drand reveal.

## Hosted Provers

Miners may use OpenAI-compatible endpoints through:

```text
LEMMA_PROVER_BASE_URL
LEMMA_PROVER_API_KEY
LEMMA_PROVER_MODEL
```

The provider is not scored. Lemma only checks the final Lean proof.

## Reward Rule

The rank-0 unique proof for an active task earns one verified unit in the validator epoch. On the bucket/commitment path, rank-0 means the earliest valid Merkle-root commit block. Duplicate proofs, failed proofs, changed targets, prose explanations, and unauthenticated live submissions do not earn credit. Unsolved slots do not increase the payout for solved slots.

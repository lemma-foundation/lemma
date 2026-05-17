# Lemma

Lemma is a Bittensor subnet for producing open, Lean-verified proof data for training mathematical AI.

AI can guess. Lean can check. Lemma pays for the checked data.

Miners produce Lean proofs for active theorem tasks. Validators check those proofs with the pinned Lean environment, score the first accepted unique proof for each task, set normal Bittensor miner weights, and publish accepted proofs as replayable corpus rows.

The corpus is the product. The market is the means.

## What Lemma Is

- a Bittensor subnet;
- a continuous source of Lean theorem/proof training data;
- a binary proof-checking system: pass or fail;
- a public corpus of verified rows that can be replayed later;
- a path toward stronger open mathematical provers.

## What Lemma Is Not

- not a Google DeepMind Formal Conjectures bounty subnet;
- not endorsed by Google DeepMind;
- not a smart-contract escrow product;
- not an owner-cut router;
- not a contract custody system;
- not a prose-judging subnet.

Lemma v1 uses normal Bittensor validator and miner emissions. Subnet owner emission routing is left alone.

## The Loop

```text
task -> proof search -> Lean verification -> validator score
     -> Bittensor weights -> public corpus -> stronger prover models
```

Miners can use local tactics, hosted models, retrieval, search, human-written proofs, or custom agents. Validators only score the final checked proof artifact.

## Quick Start: Miners

```bash
uv sync --extra btcli
uv run lemma setup
uv run lemma status
uv run lemma mine --once --prover-command "python prover.py"
```

For manual inspection:

```bash
uv run lemma tasks list
uv run lemma tasks inspect lemma.sample.true_intro
uv run lemma verify lemma.sample.true_intro --submission Submission.lean
uv run lemma submit lemma.sample.true_intro --submission Submission.lean --solver-hotkey <hotkey>
```

## Quick Start: Validators

```bash
uv sync --extra btcli
uv run lemma setup
uv run lemma worker --check
uv run lemma validate --once --no-set-weights
```

The validator path fetches active tasks, validates task-bound submissions, runs Lean, scores accepted proofs, and writes local corpus deltas.

## Corpus Row

```json
{
  "schema_version": 1,
  "task_id": "lemma.sample.true_intro",
  "task_version": 1,
  "target_sha256": "9b4b...",
  "proof_sha256": "14ae...",
  "source_stream": "human_curated",
  "source_license": "CC-BY-4.0",
  "solver_hotkey": "miner-hotkey",
  "validator_hotkey": "validator-hotkey",
  "rewarded": true,
  "verification": {
    "passed": true,
    "verifier_version": "lemma-lean-v1"
  }
}
```

Rows include the theorem statement, imports, toolchain, proof script, hashes, source metadata, validator attribution, and verification summary. Failed proofs are not corpus rows. Valid alternate proofs can be stored with `rewarded: false`.

## Benchmarks

Google DeepMind Formal Conjectures, lean-eval, miniF2F, PutnamBench, and the IMO Grand Challenge are frontier benchmarks for measuring mathematical AI. They are not the v1 payout path. If models trained on Lemma's corpus solve more of those problems, the subnet is working.

Lemma is independent and is not endorsed by Google DeepMind.

## Docs

- [Overview](docs/overview.md)
- [Corpus](docs/corpus.md)
- [Miner guide](docs/miner.md)
- [Validator guide](docs/validator.md)
- [Task supply](docs/task-supply.md)
- [Incentives](docs/incentives.md)
- [Security](docs/security.md)
- [Benchmarks](docs/benchmarks.md)
- [Model API](docs/model-api.md)
- [Architecture](docs/architecture.md)
- [Production](docs/production.md)
- [Testing](docs/testing.md)
- [FAQ](docs/faq.md)

## Development Checks

```bash
uv run ruff check lemma tests
uv run mypy lemma
uv run pytest tests -q
uv run python scripts/leak_check.py
```

## License

Code is Apache-2.0. Corpus rows default to CC-BY 4.0 unless source metadata says otherwise.

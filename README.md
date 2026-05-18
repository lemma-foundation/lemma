# Lemma

Lemma is an open AlphaProof-style proof-data engine for Lean: a Bittensor subnet where miners produce machine-checked proofs, validators verify them with the Lean kernel, and every accepted proof becomes public training data.

AI can guess. Lean can check. Lemma pays for the checked data.

Miners produce Lean proofs for active theorem tasks. Validators check those proofs with the pinned Lean environment, reward accepted proof units, burn unsolved-slot value by default, and publish accepted proofs as replayable corpus rows.

The corpus is the product. The market is the means.

## What Lemma Is

- a Bittensor subnet;
- a continuous source of Lean theorem/proof training data;
- a binary proof-checking system: pass or fail;
- a public corpus of verified rows that can be replayed later;
- a path toward stronger open mathematical provers.

## What Lemma Is Not

- not a Google DeepMind Formal Conjectures payout path;
- not endorsed by Google DeepMind;
- not a smart-contract escrow product;
- not an owner-cut router;
- not a contract custody system;
- not a prose-judging subnet.

Lemma v1 uses normal Bittensor validator and miner emissions. Subnet owner emission routing is left alone.

## The Loop

```text
formal task -> proof search -> Lean verification -> fixed-price proof unit
     -> unearned-share burn/recycle policy -> public corpus -> stronger prover models
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
uv run lemma task show lemma.sample.true_intro
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

The validator path fetches active tasks, validates task-bound submissions, runs Lean, scores accepted proofs, withholds unsolved-slot value from current solvers, and writes local corpus deltas.

## Try The Loop Locally

Use [examples/operator-smoke](examples/operator-smoke/README.md) to build a pinned registry, package one proof submission, run a validator pass, and export corpus artifacts.

## Corpus Row

```json
{
  "schema_version": 1,
  "task_id": "lemma.sample.true_intro",
  "task_version": 1,
  "target_sha256": "9b4b...",
  "proof_sha256": "14ae...",
  "proof_identity": "14ae...",
  "proof_identity_source": "proof_sha256_fallback",
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

## Scoring And Unearned Share

Each epoch has `K` active tasks. A miner's v1 score is:

```text
score = verified_unique_wins / K
```

Bittensor miner weights use the same denominator: `weight = credit / K`. The unearned share is not redistributed to current solvers. It is burned by default and can only be routed to future proof-production rails by an explicit policy.

## Benchmarks

Google DeepMind Formal Conjectures, lean-eval, miniF2F, PutnamBench, and the IMO Grand Challenge are frontier benchmarks for measuring mathematical AI. They are not the v1 payout path. If models trained on Lemma's corpus solve more of those problems, the subnet is working.

Lemma is independent and is not endorsed by Google DeepMind.

## Docs

- [What is Lemma?](docs/what-is-lemma.md)
- [Open AlphaProof-style engine](docs/open-alphaproof-engine.md)
- [Open AlphaProof execution plan](docs/exec-plan-open-alphaproof.md)
- [How it works](docs/how-it-works.md)
- [Corpus](docs/corpus.md)
- [Miner guide](docs/miner.md)
- [Validator guide](docs/validator.md)
- [Tasks](docs/tasks.md)
- [Operator registry flow](docs/operator-registry-flow.md)
- [Scoring](docs/scoring.md)
- [Security and gaming](docs/security-and-gaming.md)
- [Benchmarks](docs/benchmarks.md)
- [Formal Conjectures](docs/formal-conjectures.md)
- [Model APIs](docs/model-apis.md)
- [Architecture](docs/architecture.md)
- [CLI](docs/cli.md)
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

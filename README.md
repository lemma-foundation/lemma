# Lemma

Lemma is a Bittensor subnet for training open mathematical AI with Lean-verified proof data.

Miners solve theorem-proving tasks in Lean. Validators check every proof with the Lean kernel. Accepted proofs earn miner rewards through the normal Bittensor validator-weight mechanism and are appended to the public Lemma Corpus.

The goal is not to pay for one rare conjecture solve. The goal is to build the largest useful open corpus of verified Lean theorem/proof data, so stronger prover models can be trained and evaluated against frontier benchmarks such as Google DeepMind's Formal Conjectures, lean-eval, PutnamBench, miniF2F, and the IMO Grand Challenge.

AI can guess. Lean can check. Lemma pays for the checked data.

## Plain English

A lemma is a stepping stone to a larger proof. Lemma, the subnet, creates many of those stepping stones for mathematical AI.

Every epoch, Lemma publishes Lean theorem tasks. Miners try to prove them using any method: local tactics, AI models, retrieval, search, or human insight. Validators run Lean to check whether each proof is correct. Verified proofs are rewarded and added to an open dataset.

That dataset trains better provers. Better provers solve more tasks. Eventually, those provers should be capable of attacking harder open mathematical benchmarks.

## How The Subnet Works

```text
Lean proof task
    ↓
Miner proof search
    ↓
Submission.lean
    ↓
Lean verification
    ↓
Validator score
    ↓
Normal Bittensor miner rewards
    ↓
Public Lemma Corpus
    ↓
Better prover models
```

## What Miners Do

Miners are proof searchers. They fetch active tasks, produce Lean proofs, and submit proof artifacts. They can use any tooling: local models, hosted APIs, LeanDojo-style retrieval, tactic search, custom agents, or human-written proofs.

## What Validators Do

Validators are proof checkers. They run the pinned Lean environment, reject invalid submissions, deduplicate proofs, score accepted work, and publish corpus rows.

## What Lemma Publishes

Lemma publishes a public corpus of verified Lean theorem/proof data. Each row is designed to be replayable: statement, imports, toolchain, proof, proof hash, verifier result, solver attribution, and source metadata.

## Benchmarks And Frontier Problems

Google DeepMind's Formal Conjectures, lean-eval, miniF2F, PutnamBench, and the IMO Grand Challenge are frontier benchmarks for measuring mathematical AI. Lemma v1 does not use Formal Conjectures solves as the main reward stream. If models trained on Lemma's corpus solve more of those problems, the subnet is doing its job.

Lemma is independent and is not endorsed by Google DeepMind.

## Quick Start

```bash
uv sync
uv run lemma setup
uv run lemma status
uv run lemma tasks list
uv run lemma tasks inspect <task-id>
uv run lemma verify <task-id> --submission Submission.lean
```

## Docs

- [What is Lemma?](docs/what-is-lemma.md)
- [Incentive mechanism](docs/incentive-mechanism.md)
- [Miner guide](docs/miner.md)
- [Validator guide](docs/validator.md)
- [The Lemma Corpus](docs/corpus.md)
- [Task supply](docs/task-supply.md)
- [Model APIs and prover adapters](docs/model-api.md)
- [Benchmarks](docs/benchmarks.md)
- [Security and gaming](docs/security-and-gaming.md)
- [Architecture](docs/architecture.md)
- [FAQ](docs/faq.md)

## Development

```bash
uv run ruff check lemma tests
uv run mypy lemma
uv run pytest tests -q
```

## License

Apache-2.0.

# Lemma

**Lemma is a Verified Reasoning Network.**

Miners solve hard reasoning tasks. Validators check the answers with deterministic verifiers. Accepted solutions become open training data for stronger AI.

AI can guess. Verifiers can check. Lemma pays for checked reasoning.

## Why Lemma Exists

Most AI can produce answers that sound right. Lemma focuses on answers that can be mechanically checked.

In v1, Lemma uses Lean theorem proving. A Lean proof either passes the verifier or it does not. That binary signal turns reasoning into reusable training data.

## How It Works

1. Validators publish active formal reasoning tasks.
2. Miners search for solutions.
3. Miners submit task-bound proofs.
4. Validators run the pinned Lean verifier.
5. Accepted proofs earn proof-unit credit.
6. Accepted proofs become replayable public corpus rows.
7. The corpus trains stronger reasoning models.

Lemma runs as a network on Bittensor. Bittensor supplies the miner and validator mechanism; deterministic verification supplies the correctness signal.

## What Lemma Produces

The product is verified reasoning data: replayable records of tasks, proofs, verifier metadata, source and license metadata, attribution, dependencies, and verification results.

A proof that passes becomes training data. Failed proofs do not become corpus rows. Valid alternate proofs can be stored without duplicate reward when they add useful proof diversity.

## Why Lean First

Lean is the first production domain because it gives Lemma a mature deterministic checker for theorem-proving tasks. Math is the wedge. Verified reasoning data is the product.

Lean is the first production domain, not the final boundary. Future domains must be deterministic, replayable, licensed, and safe before they can enter production.

## What Lemma Is Not

- not a prose-judging network;
- not a generic code benchmark;
- not a smart-contract escrow product;
- not a Google DeepMind Formal Conjectures payout path;
- not endorsed by Google DeepMind;
- not production outside Lean yet.

Lemma uses normal Bittensor validator and miner emissions. Subnet owner emission routing is left alone.

Google DeepMind Formal Conjectures, lean-eval, miniF2F, PutnamBench, and the IMO Grand Challenge are research context and evaluation targets. They are not the v1 payout path.

## Quick Start: Miners

```bash
uv sync --extra btcli
uv run lemma setup
uv run lemma status
uv run lemma mine --once --prover-command "python prover.py" --output submission.json
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
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
```

The validator path fetches active tasks, validates task-bound submissions, runs Lean, scores accepted proofs, withholds unsolved-slot value from current solvers, and writes local corpus deltas.
The submission spool is a file inbox for miner submission JSON files; consumed files move to `processed/` after a successful validator pass.

## Try The Loop Locally

Use [examples/operator-smoke](examples/operator-smoke/README.md) to build a pinned registry, package one proof submission, run a validator pass, and export corpus artifacts.

## Corpus Export

Export the current Lean domain corpus:

```bash
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Corpus rows include the theorem statement, imports, toolchain, proof script, identity strength, source/license metadata, graph links, validator attribution, and verification summary. A corpus row is a replayable record of a verified solution.

## Scoring

Each epoch has `K` active paid task slots. A miner earns one credit for being first to submit a unique accepted proof for a slot. Unsolved slots remain unearned by default.

```text
score = verified_unique_wins / K
weight = credit / K
```

The unearned share is not redistributed to current solvers. It is burned by default and can only be routed to future proof-production rails by an explicit policy.

## Roadmap

Lean first. Then corpus productization. Then verifier adapters. Then experimental domains.

Roadmap examples include Verus, SAT/SMT, LP/SDP, and cryptanalysis only after they meet the same deterministic verifier, replay, licensing, and corpus safety standards. They are not live production mechanisms today.

## Docs

Getting Started:

- [What is Lemma?](docs/what-is-lemma.md)
- [How it works](docs/how-it-works.md)
- [FAQ](docs/faq.md)
- [CLI](docs/cli.md)

Operators:

- [Miner guide](docs/miner.md)
- [Validator guide](docs/validator.md)
- [Tasks](docs/tasks.md)
- [Production](docs/production.md)
- [Testing](docs/testing.md)
- [Operator registry flow](docs/operator-registry-flow.md)

Protocol:

- [Protocol invariants](docs/PROTOCOL_INVARIANTS.md)
- [Architecture](docs/architecture.md)
- [Scoring](docs/scoring.md)
- [Corpus](docs/corpus.md)
- [Proof identity](docs/proof-identity.md)
- [Useful verified row](docs/useful-verified-row.md)
- [License policy](docs/license-policy.md)
- [Dependency graph](docs/dependency-graph.md)
- [Security and gaming](docs/security-and-gaming.md)
- [Domain adapter spec](docs/domain-adapter-spec.md)
- [Lean domain](docs/domains/lean.md)

Roadmap And Research:

- [Roadmap](ROADMAP.md)
- [Benchmarks](docs/benchmarks.md)
- [Formal Conjectures](docs/formal-conjectures.md)
- [Open AlphaProof-style engine](docs/open-alphaproof-engine.md)
- [Open AlphaProof execution plan](docs/exec-plan-open-alphaproof.md)
- [Model APIs](docs/model-apis.md)
- [Affine integration](docs/integrations/affine.md)
- [Verus domain](docs/domains/verus.md)

## Development Checks

```bash
uv run ruff check lemma tests
uv run mypy lemma
uv run pytest tests -q
uv run python scripts/leak_check.py
```

## License

Code is Apache-2.0. Corpus rows default to CC-BY 4.0 unless source metadata says otherwise.

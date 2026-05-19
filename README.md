# Lemma

**Lemma is a permissionless incentive network for growing open, machine-verified mathematics.**

Miners submit Lean proofs. Validators verify them with a pinned Lean toolchain. Accepted theorems become part of an open, citation-structured corpus of formal mathematics.

## Why Lemma Exists

Mathlib showed that machine-verified mathematics can become shared public infrastructure. Lemma adds a market around formal proof production.

The goal is simple: reward correct Lean proofs and use them to expand the open mathematical record.

## How It Works

1. Validators publish an active pool of Lean theorem-proving tasks.
2. Miners search for Lean proofs.
3. Miners submit task-bound proof packages.
4. Validators run the pinned Lean verifier.
5. First accepted unique proofs earn credit.
6. Accepted theorems become replayable corpus entries.
7. Dependency and citation metadata turns the corpus into a graph of reusable mathematics.

Lemma runs as a network on Bittensor. Bittensor supplies the permissionless miner and validator market; Lemma supplies the deterministic mathematical verification target.

## What Lemma Produces

Lemma produces an open corpus of verified Lean theorem/proof records.

Each accepted entry records:

- theorem statement;
- proof source;
- imports and dependencies;
- verifier and toolchain metadata;
- source and license metadata;
- contributor attribution;
- verification result;
- corpus graph links.

Downstream users can train theorem provers and reasoning models on the corpus, but the public identity is formal mathematics: verified theorem/proof records first, model data second.

## Why Lean And Math

Lean gives Lemma a mature, deterministic verifier. A proof either passes in the pinned environment or it fails.

Mathematics is the right v1 domain because it is both clean and deep: clean enough for binary verification, deep enough to support years of useful work.

## Scope

Lemma v1 focuses on Lean formal mathematics. The network rewards verified Lean proof production and publishes accepted theorem/proof records as an open mathematical corpus.

Long-term verifier-domain research exists, but it is not part of the v1 public thesis. See [Background Research: Future Verifier Domains](docs/research/future-verifier-domains.md) for that context.

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

Export the current Lean mathematical corpus:

```bash
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Corpus rows include the theorem statement, imports, toolchain, proof script, identity strength, source/license metadata, graph links, validator attribution, and verification summary. A corpus row is a replayable record of a verified theorem/proof.

## Scoring

Each epoch has `K` active paid theorem slots. A miner earns one credit for being first to submit a unique accepted proof for a slot. Unsolved slots remain unearned by default.

```text
score = verified_unique_wins / K
weight = credit / K
```

The unearned share is not redistributed to current solvers. It is burned by default and can only be routed to future proof-production rails by an explicit policy.

## Roadmap

1. Stabilize the Lean verifier path.
2. Improve miner and validator reliability.
3. Expand task supply for useful Lean theorem proving.
4. Export high-quality corpus releases.
5. Improve dependency/citation graph tooling.
6. Support downstream theorem-prover training and evaluation.

Long-term verifier-domain research exists, but it is not part of the v1 public thesis.

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

Lean:

- [Lean domain](docs/domains/lean.md)

Background Research:

- [Roadmap](ROADMAP.md)
- [Future verifier domains](docs/research/future-verifier-domains.md)
- [Benchmarks](docs/research/benchmarks.md)
- [Open AlphaProof-style engine](docs/research/open-alphaproof-engine.md)
- [Open AlphaProof execution plan](docs/research/exec-plan-open-alphaproof.md)
- [Model APIs](docs/research/model-apis.md)
- [Affine integration](docs/research/integrations/affine.md)

## Development Checks

```bash
uv run ruff check lemma tests
uv run mypy lemma
uv run pytest tests -q
uv run python scripts/leak_check.py
```

## License

Code is Apache-2.0. Corpus rows default to CC-BY 4.0 unless source metadata says otherwise.

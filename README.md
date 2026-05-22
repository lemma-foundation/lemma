# Lemma

**Lemma is a permissionless incentive network for growing open, machine-verified mathematics.**

Miners submit Lean proofs. Validators verify them with a pinned Lean toolchain. Accepted proofs become replayable theorem/proof records in an open, citation-structured corpus of formal mathematics.

## Why Lemma Exists

Mathlib showed that machine-verified mathematics can become shared public infrastructure. Lemma adds an incentive layer for formal proof production.

The goal is simple: reward correct Lean proofs and use them to expand the open mathematical record.

## How It Works

1. Validators derive the same active pool of procedural Lean theorem-proving tasks.
2. Miners search for Lean proofs.
3. Miners submit task-bound proof packages.
4. Validators run the pinned Lean verifier.
5. First accepted unique proofs earn credit.
6. Accepted proofs become replayable corpus entries.
7. Dependency and citation metadata turns the corpus into a graph of reusable mathematics.

Lemma runs as a network on Bittensor. Bittensor supplies the permissionless miner and validator network; Lemma supplies the deterministic mathematical verification target.

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

Mathematics is the right production domain because it is both clean and deep: clean enough for binary verification, deep enough to support years of useful work.

## Scope

Lemma focuses on Lean formal mathematics. The network rewards verified Lean proof production and publishes verified theorem/proof records as an open mathematical corpus.

## Quick Start: Miners

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma status
uv run lemma mine --once --prover-command "python prover.py" --output submission.json
```

`lemma mine` is the reference client and smoke path, not the official mining strategy. Competitive miners can replace the CLI entirely as long as they produce valid task-bound proof submissions.

## Quick Start: Validators

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
```

The validator path fetches active tasks, validates task-bound submissions, runs Lean, scores accepted proofs, withholds unsolved-slot value from current solvers, and writes local corpus deltas.
The submission spool is a file inbox for miner submission JSON files; consumed files move to `processed/` after a successful validator pass.
`--bucket-reveals-jsonl` is the adapter for the mainnet-shaped path: a miner bucket reveal must match the miner's on-chain committed Merkle root before it can enter scoring. Add `--verify-chain-commitments` to read the miner's on-chain bucket commitment and `--verify-drand-reveals` to decrypt bucket ciphertexts and require the decrypted proof to match the revealed proof; production mode enables both checks for bucket reveals.
Live weight writes require both `LEMMA_ENABLE_SET_WEIGHTS=1` and `--set-weights`; smoke passes should stay on `--no-set-weights`.
When a live write is attempted, the validator appends a public-safe local receipt to `weight-submissions.jsonl` under `LEMMA_OPERATOR_DATA_DIR`, including the resolved UID vector and extrinsic hash when the Bittensor client returns one.
Production mode additionally requires procedural depth-2 paid supply rebuilt from a pinned public source pool, chain/drand epoch randomness, drand-keyed operator parameters from the chain-pinned operator bundle, public novelty-cache receipts, public import-graph slot-weight receipts, public burn-rate retargeting for `T(t)`, hotkey-authenticated miner submissions, commit/reveal fields on revealed submissions, network-disabled Lean verification, and strong Lean-derived proof identity for paid rewards. Registry files can be mirrored as caches; they are not the procedural problem authority.

## Try The Loop Locally

Use [examples/operator-smoke](examples/operator-smoke/README.md) to build a pinned registry, package one proof submission, run a validator pass, and export corpus artifacts.

For production-shaped supply, validators rebuild deterministic depth-2 procedural rows from public inputs before configuring the active pool. Each row carries the operator-bundle version, bundle hash, ordered operators, drand-keyed params, input/output statement hashes, public novelty-cache receipt, and public import-graph slot-weight receipt. The detailed operator path lives in [Operator Registry Flow](docs/operator-registry-flow.md). Curated Mathlib and mixed supply remain useful for local smoke and curriculum development. SN467 burn-in uses the paid mainnet supply path on the test chain.

## Corpus Export

The public smoke corpus lives at [lemma-foundation/lemma-corpus](https://github.com/lemma-foundation/lemma-corpus).

Corpus release and export tooling is documented in [Corpus](docs/corpus.md).

Corpus rows include the theorem statement, imports, toolchain, proof script, identity strength, source/license metadata, graph links, validator attribution, and verification summary. A corpus row is a replayable record of a verified theorem/proof.

## Scoring

Each epoch has `K` active paid theorem slots. A miner earns one credit for the rank-0 unique accepted proof for a slot. On the mainnet-shaped path, rank-0 is earliest Merkle-root commit block, with proof identity as the deterministic tie-break. Unsolved slots remain unearned by default.

```text
score = sum(winning_slot_weight) / sum(active_slot_weights)
weight = miner_score
```

The unearned share is not redistributed to current solvers. It is burned by default and can only be routed to future proof-production rails by an explicit policy.
In production, weak script identity can be stored as corpus metadata but cannot earn paid reward; rewarded rows need a strong Lean-derived proof-term hash or structural declaration fingerprint.

## Roadmap

1. Stabilize the Lean verifier path.
2. Improve miner and validator reliability.
3. Expand task supply for useful Lean theorem proving.
4. Export high-quality corpus releases.
5. Improve dependency/citation graph tooling.
6. Support downstream theorem-prover training and evaluation.

Long-term verifier-domain research exists, but it is not part of the public production thesis.

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
- [Mainnet readiness](docs/mainnet-readiness.md)
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

## Development Checks

```bash
uv run ruff check .
uv run mypy lemma
uv run bandit -q -r lemma scripts -ll
uv run pip-audit --ignore-vuln PYSEC-2025-49 --ignore-vuln PYSEC-2022-42969
uv run pytest tests -q
uv run python scripts/leak_check.py
```

## License

Code is Apache-2.0. Corpus rows default to CC-BY 4.0 unless source metadata says otherwise.

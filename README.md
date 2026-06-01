# Lemma

**Lemma is an open competition for formal proof.**

Agents compete to prove active Lean theorem tasks. A rewardable submission is a
task-bound proof that passes the pinned verifier and wins its slot.

Lemma runs on Bittensor. Bittensor supplies the open miner/validator network; Lemma supplies the deterministic mathematical target: produce Lean proofs that verify.

## Why Lemma Exists

Formal proof has a rare property for an open competition: Lean can check the outcome.

The goal is simple: turn proof search into verifier-checked network work.

## How It Works

1. Validators derive the same active pool of Lean theorem-proving tasks.
2. Miners search for proofs using any stack they want.
3. Submissions bind a proof to a specific active task.
4. Validators run the pinned Lean verifier.
5. The first eligible accepted proof earns credit; unsolved slots stay unearned.
6. Accepted results can be mirrored for replay.

## What Lemma Records

Accepted rows contain the data needed to rerun and inspect a result:

- theorem statement;
- proof source;
- imports and dependencies;
- verifier and toolchain metadata;
- source and license metadata;
- contributor / miner attribution;
- verification result;
- proof graph links.

The subnet owner publishes canonical snapshots from accepted rows. Validators can publish similar mirrors if they configure storage, but publishing is not required for validation.

## Quick Start: Miners

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma status
uv run lemma mine --once --prover-command "python prover.py" --output submission.json
```

The mining docs are intentionally sparse. `lemma mine` is the setup and smoke path, not the strategy. The goal is to build an agent or system that proves active tasks; use Cursor, Claude Code, Codex, Antigravity, or any other tool that helps you set up `lemma`, configure `btcli`, inspect tasks, and improve your prover. Miners with the best strategies win.

## Quick Start: Validators

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
```

The validator path fetches active tasks, checks task-bound submissions with Lean, scores accepted proofs, and writes local result records. Use `--no-set-weights` for smoke runs. Live validation, bucket reveals, commitments, and production settings are covered in [Validator Guide](docs/validator.md) and [Production](docs/production.md).

## Try The Loop Locally

Use [examples/operator-smoke](examples/operator-smoke/README.md) to build a pinned registry, package one proof submission, run a validator pass, and export Proof Atlas artifacts.

For production-shaped supply, validators rebuild active tasks from public inputs before configuring the active pool. The detailed path lives in [Operator Registry Flow](docs/operator-registry-flow.md).

## Proof Atlas

The public proof data lives at [lemma-foundation/lemma-proof-atlas](https://github.com/lemma-foundation/lemma-proof-atlas).

Release and export tooling is documented in [Proof Atlas](docs/proof-atlas.md).

## Scoring

Each chain tempo has `K` active paid theorem slots. Harder frontiers can run fewer slots through the public curriculum and cost caps while the subnet tempo stays fixed. A miner earns credit when their accepted proof is first for a slot under the current scoring rules. Unsolved slots remain unearned by default.

```text
score = sum(winning_slot_weight) / sum(active_slot_weights)
weight = miner_score
```

The unearned share is not redistributed to current solvers. It is burned by default and can only be routed to future proof-production rails by an explicit policy.
In production, weak script identity and structural print fingerprints can be stored as accepted-proof metadata but cannot earn paid reward; rewarded rows need a strong Lean-derived proof-term hash.

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
- [Proof Atlas](docs/proof-atlas.md)
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

Code is Apache-2.0. Accepted proof rows default to CC-BY 4.0 unless source metadata says otherwise.

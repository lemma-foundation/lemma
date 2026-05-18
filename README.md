# Lemma

Lemma is a Lean-only proof-data subnet: a permissionless market where miners produce Lean proofs accepted by a deterministic verifier, and validators publish the accepted proofs as open training-data rows.

AI can guess. Verifiers can check. Lemma pays for checked data.

Lean theorem proving is the production foundation. Lean demonstrates the mechanism because its verifier is deterministic, mature, and produces high-quality theorem-proof pairs.

Miners produce proofs for active Lean tasks. Validators check those proofs with a pinned Lean verifier, reward accepted proof units, burn unsolved-slot value by default, and publish accepted proofs as replayable graph rows.

Math is the wedge. Verified data is the product. The graph-shaped corpus is the substrate. The market is the means.

## What Lemma Is

- a Bittensor subnet;
- a continuous source of verifier-grounded training data;
- a binary artifact-checking system: pass or fail;
- a public graph of verified rows that can be replayed later;
- a path toward stronger open reasoning, theorem-proving, and code-verification models.

## What Lemma Is Not

- not a Google DeepMind Formal Conjectures payout path;
- not endorsed by Google DeepMind;
- not a generic code benchmark;
- not a production multi-domain verifier subnet yet;
- not a test-only programming subnet;
- not a smart-contract escrow product;
- not an owner-cut router;
- not a contract custody system;
- not a prose-judging subnet.

Lemma uses normal Bittensor validator and miner emissions. Subnet owner emission routing is left alone.

## The Loop

```text
formal task -> proof search -> Lean verification -> fixed-price proof unit
     -> unearned-share burn/recycle policy -> public corpus -> stronger prover models
```

For v1, the artifact is a Lean proof and the verifier is `lake build` in the pinned Lean environment. Miners can use local tactics, hosted models, retrieval, search, human-written proofs, or custom agents. Validators only score the final checked artifact.

## Mechanism Class

The row contract is graph-native so future deterministic verifier domains can attach to the same substrate. They are roadmap domains, not live production mechanisms:

- Lean: theorem-proof pairs
- Verus: Rust programs, specifications, and proofs
- SAT/SMT: formulas, assignments, solver traces, or certificates
- LP/SDP: optimization instances plus primal/dual certificates
- Cryptanalysis: instances plus verifiable witnesses

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

## Corpus Row

```json
{
  "schema_version": 1,
  "task_id": "lemma.sample.true_intro",
  "task_version": 1,
  "target_sha256": "9b4b...",
  "proof_sha256": "14ae...",
  "proof_identity": "14ae...",
  "proof_identity_source": "normalized_script_sha256",
  "proof_identity_strength": "weak",
  "full_reward_eligible": false,
  "source_stream": "human_curated",
  "source_license": "CC-BY-4.0",
  "quality": {
    "useful_verified_row": false,
    "license_state": "attribution_required"
  },
  "dependencies": {
    "mathlib_imports": ["Mathlib"],
    "dependency_depth": 0
  },
  "solver_hotkey": "miner-hotkey",
  "validator_hotkey": "validator-hotkey",
  "rewarded": true,
  "verification": {
    "passed": true,
    "verifier_version": "lemma-lean-v1"
  }
}
```

Rows include the theorem statement, imports, toolchain, proof script, identity strength, source/license metadata, graph links, validator attribution, and verification summary. Failed proofs are not corpus rows. Valid alternate proofs can be stored with `rewarded: false`.

Export the current Lean domain corpus:

```bash
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

## Scoring And Unearned Share

Each epoch has `K` active tasks. A miner's v1 score is:

```text
score = verified_unique_wins / K
```

Bittensor miner weights use the same denominator: `weight = credit / K`. The unearned share is not redistributed to current solvers. It is burned by default and can only be routed to future proof-production rails by an explicit policy.

## Benchmarks

Google DeepMind Formal Conjectures, lean-eval, miniF2F, PutnamBench, and the IMO Grand Challenge are frontier benchmarks for measuring mathematical AI. They are not the v1 payout path. If models trained on Lemma's corpus solve more of those problems, the subnet is working.

Lemma is independent and is not endorsed by Google DeepMind.

## For Model Miners

Lemma corpora are intended for model training. Reasoning/model subnets can train on Lemma's accepted artifacts to improve theorem proving, code reasoning, program synthesis, SAT/SMT, and formal verification performance.

Affine is the model competition layer. Lemma is the verifier-grounded data production layer. Affine-style miners can consume Lemma's public corpora; Lemma does not require a transactional Affine dependency.

## Docs

- [What is Lemma?](docs/what-is-lemma.md)
- [Roadmap](ROADMAP.md)
- [Protocol invariants](docs/PROTOCOL_INVARIANTS.md)
- [Dependency graph](docs/dependency-graph.md)
- [Proof identity](docs/proof-identity.md)
- [Useful verified row](docs/useful-verified-row.md)
- [License policy](docs/license-policy.md)
- [Domain adapter spec](docs/domain-adapter-spec.md)
- [Affine integration](docs/integrations/affine.md)
- [Lean domain](docs/domains/lean.md)
- [Verus domain](docs/domains/verus.md)
- [Open AlphaProof-style engine](docs/open-alphaproof-engine.md)
- [Open AlphaProof execution plan](docs/exec-plan-open-alphaproof.md)
- [How it works](docs/how-it-works.md)
- [Corpus](docs/corpus.md)
- [Miner guide](docs/miner.md)
- [Validator guide](docs/validator.md)
- [Tasks](docs/tasks.md)
- [Mathlib extraction](docs/mathlib-extraction.md)
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

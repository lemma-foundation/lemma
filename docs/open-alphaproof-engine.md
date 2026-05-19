# Open AlphaProof-Style Engine

Lemma is a Verified Reasoning Network that starts with Lean proofs. Miners produce machine-checked proofs. Validators verify them with the Lean kernel. Every accepted proof becomes public replayable training data.

The product is the corpus: source-attributed Lean theorem/proof rows with enough metadata to replay the check later. The market is the mechanism that produces those rows.

## Mantra

```text
Lean checks truth.
Miners produce proofs.
Validators score proofs.
Rewards scale with verified proof units.
Unsolved slots do not inflate solved-slot payouts.
Difficulty adapts from observed solve rate.
The corpus is the product.
The market is the means.
Benchmarks are downstream surfaces.
```

## Architecture

```text
supply streams -> task filters -> deterministic active pool
               -> mining -> Lean validation -> proof-unit scoring
               -> burn/recycle unearned share -> public corpus
               -> downstream benchmarks and prover training
```

Supply streams include Mathlib proof erasure, Mathlib perturbations, proof-state graph extraction, auto-formalization candidates, conjecture generation, and hard-target variant generation. Heavy generators run off-chain. Validators check deterministic task files: syntax, type validity, policy, novelty, triviality label, source/license metadata, and queue inclusion.

The active pool has two separate controls:

- `frontier_depth`: the protocol difficulty proxy, driven by EMA solve rate.
- `active_K`: the paid throughput target, driven by validator capacity, with solve rate only as a safety signal.

If solve rate is zero, the frontier does not step backward into already exposed tasks. The controller halts frontier advancement and draws replacements from hard-target variant/scaffold streams around stalled targets.

## Economics

Each round exposes `K` active paid slots. A miner earns one credit for the first accepted unique proof of an active task.

```text
score_m = credit_m / K
unearned_share = 1 - sum(score_m)
```

The unearned share is never redistributed to current solvers. The default policy is `burn`; `recycle` and `hold` are explicit policy rails for future proof-production funding. Recycle must mean future supply, infrastructure, training, or indexing work, not curator-priced challenge rewards.

If nobody solves anything:

```text
all miner shares = 0
unearned_share = 1.0
```

The old previous-weight fallback is not part of the target design.

## Proof Identity

The production target is Lean-derived canonical proof-term identity for paid duplicate detection. Script hashes are retained as metadata and as a clearly labelled development fallback until the Lean extractor is production-ready. Documentation and schemas must not imply script hash deduplication is exact structural proof identity.

## Benchmarks

Benchmarks are downstream surfaces. AlphaProof, Google DeepMind Formal Conjectures, miniF2F, PutnamBench, LeanDojo, LeanNavigator, and the IMO Grand Challenge are research context or evaluation targets only. Lemma is independent; no endorsement is implied.

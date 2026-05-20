# Execution Plan: Open AlphaProof-Style Engine

This plan keeps the Lean verification spine intact and changes the public contract around it.

## Phase 0: Source Of Truth

- Keep `docs/research/open-alphaproof-engine.md` as the direction-setting research document.
- Align README, architecture, task, scoring, corpus, miner, validator, benchmark, and site language.
- Remove stale payout, escrow, custody, owner-router, prose-judging, and benchmark-as-product framing.

## Phase 0.5: Economic Simulator

- Simulate 100-1000 tempos with synthetic miner populations.
- Verify miner shares follow deterministic active slot weights and never redistribute unsolved slots.
- Verify unsolved value appears as `unearned_share`, never as inflated current-solver reward.
- Track solve-rate, `active_K`, and `frontier_depth` trajectories before chain wiring.

## Phase 1: Scoring Economics

- Use deterministic active slot weights for miner weights.
- Compute `unearned_share = 1 - sum(miner_weights)`.
- Default `unearned_policy` to `burn`.
- Remove the previous-weight fallback; empty tempos have zero miner shares and `unearned_share = 1.0`.

## Phase 2: Queue And Curriculum

- Maintain a deterministic K-slot active pool.
- Advance solved slots.
- Park expired unsolved tasks.
- Halt frontier advancement and request hard-target variants when solve rate stalls.
- Keep `frontier_depth` as the difficulty proxy and `active_K` as throughput.

## Phase 3: Supply Streams

- Start each stream as deterministic typed fixtures.
- Keep heavy generation off the validator critical path.
- Require source and license provenance for every candidate.

## Phase 4: Proof Identity

- Preserve `proof_sha256` as script metadata.
- Surface `proof_term_hash` when available.
- Label script-hash fallback clearly until Lean proof-term canonicalization is production-ready.

## Phase 5: Chain Interfaces

- Add commitment, drand, weights, and burn/recycle interfaces with local fixtures.
- Do not claim production timelock reveal until live chain integration is tested.

## Acceptance

- All tests pass.
- Docs and schemas name the corpus as the product.
- Scoring tests cover `K=10` with 0, 2, 7, and 10 solved slots.
- The public site stays static and avoids endorsement or payout-path claims.
- Leak checks pass before any commit or push.

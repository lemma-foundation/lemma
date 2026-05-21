# Security And Gaming

An incentive network for formal mathematics is only useful if bad artifacts cannot enter the verified corpus.

Lean gives Lemma a binary correctness signal, but the network still needs clear anti-gaming boundaries.

## Submission Checks

Validators reject:

- `sorry` or `admit`;
- custom axioms and constants;
- unsafe code and native execution tricks;
- changed theorem statements;
- disallowed imports;
- macro, syntax, elaborator, and notation changes;
- oversized proof bodies;
- inactive task IDs;
- task-version or target-hash mismatches;
- unsigned live miner responses;
- missing hotkey-authenticated commit/reveal fields in production mode;
- miner bucket reveals whose `(slot_index, ciphertext_sha256)` Merkle root does not match the miner's on-chain committed root;
- miner bucket reveals whose decrypted drand payload does not match the revealed proof;
- paid production tasks that are not procedural depth-2.

## Verification

Verification runs in a pinned Lean/mathlib environment. Docker verification disables networking by default. Remote workers require bearer auth for non-loopback binds.

## Source-Pool Pinning

Production validators pin source-pool bytes with `LEMMA_TASK_SOURCE_POOL_SHA256_EXPECTED` and derive paid task targets from that source pool plus the epoch block hash. Local/dev registries can still be pinned for replay fixtures, but registry bytes do not choose paid production problems.

## Scoring Defenses

Proofs are deduplicated by Lean proof-term hash or Lean-derived structural fingerprint when available. Script fallback is labelled as `script_sha256` or `normalized_script_sha256` and is not treated as exact structural identity. Public proof release should wait until the scoring window closes. Baseline-solved tasks and held-out benchmark claims are kept out of paid activation.

First valid committed reveal wins each theorem slot. Re-submitting another miner's proof after reveal should not pay because rank is anchored to the miner's Merkle-root commit block, not local file arrival time. Validators must reproduce the active task set deterministically before scoring. Curated and mixed supply are useful for development work, but paid production tasks must use fresh procedural depth-2 rows generated from the current block-hash epoch seed to avoid a static future playlist.

## Privacy

Corpus rows never include local paths, hostnames, IPs, secrets, wallet files, verifier logs, or local agent state.

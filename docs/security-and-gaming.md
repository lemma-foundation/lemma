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

## Registry Pinning

Validators trust task registry bytes pinned by `LEMMA_TASK_REGISTRY_SHA256_EXPECTED`. Production mode also requires verified registry signatures. The signature covers the canonical registry payload without `signed_by` and `signature`; the SHA256 pin still covers the exact published file bytes.

## Scoring Defenses

Proofs are deduplicated by Lean proof-term hash or Lean-derived structural fingerprint when available. Script fallback is labelled as `script_sha256` or `normalized_script_sha256` and is not treated as exact structural identity. Public proof release should wait until the scoring window closes. Baseline-solved tasks and held-out benchmark claims are kept out of paid activation.

First valid committed reveal wins each theorem slot. Re-submitting another miner's proof after reveal should not pay because rank is anchored to the miner's Merkle-root commit block, not local file arrival time. Validators must reproduce the active task set deterministically before scoring. Curated and mixed supply are useful for development and testnet work, but paid mainnet tasks must be fresh procedural depth-2 rows to avoid pre-computation collapse.

## Privacy

Corpus rows never include local paths, hostnames, IPs, secrets, wallet files, verifier logs, or local agent state.

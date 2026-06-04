# Security And Gaming

An open proof competition is only useful if bad artifacts cannot enter the verified corpus.

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
- paid production tasks that are not registry-backed real missing-proof rows;
- paid production tasks already marked `known_solved`, `public_solution_known`, or `baseline_solved`.

## Verification

Verification runs in a pinned Lean/mathlib environment. Docker verification disables networking by default. Remote workers require bearer auth for non-loopback binds.

## Source Pinning

Production validators trust a SHA-pinned task registry, signature policy, and public source metadata. Paid rows must point to real missing-proof sources such as `sorrydb`, `formal_conjectures`, public Lean projects, or reviewed human-curated rows. The active window is still derived independently from the pinned registry, `K`, frontier depth, and chain/drand epoch randomness.

## Scoring Defenses

Proofs are deduplicated for paid production by the Lean proof-term hash. Lean structural fingerprints and script hashes are labelled below strong paid identity. Public proof release should wait until the scoring window closes. Baseline-solved tasks, public-known solutions, and held-out benchmark claims are kept out of paid activation.

First valid committed reveal wins each theorem slot. Re-submitting another miner's proof after reveal should not pay because rank is anchored to the miner's Merkle-root commit block, not local file arrival time. Validators must reproduce the active task set deterministically before scoring. Fixed fixtures are useful for local smoke tests, but SN467 burn-in and paid mainnet tasks must use registry-backed real tasks with public source references, target hashes, and production proof-identity gates.

## Privacy

Accepted proof rows never include local paths, hostnames, IPs, credentials, wallet files, verifier logs, or local agent state.

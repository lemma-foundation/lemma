# Security And Gaming

Lean gives Lemma a binary correctness signal, but the subnet still needs clear anti-gaming boundaries.

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
- unsigned live miner responses.

## Verification

Verification runs in a pinned Lean/mathlib environment. Docker verification disables networking by default. Remote workers require bearer auth for non-loopback binds.

## Registry Pinning

Validators trust task registry bytes pinned by `LEMMA_TASK_REGISTRY_SHA256_EXPECTED`. Registry `signed_by` and `signature` fields are stored as metadata unless an explicit verifier is wired in and tested. Signature metadata must not let a changed registry pass the SHA256 check.

## Scoring Defenses

Proofs are deduplicated by Lean proof-term hash when available. Script fallback is labelled as `script_sha256` or `normalized_script_sha256` and is not treated as exact structural identity. Public proof release should wait until the scoring window closes. Baseline-solved tasks and held-out benchmark claims are kept out of paid activation.

First valid commit wins each task slot. Re-submitting another miner's proof after reveal should not pay. Validators must reproduce the active task set deterministically before scoring.

## Privacy

Corpus rows never include local paths, hostnames, IPs, secrets, wallet files, verifier logs, or local agent state.

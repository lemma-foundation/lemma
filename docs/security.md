# Security

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

## Scoring Defenses

Proofs are deduplicated by proof-term hash when available, otherwise proof-script hash. Public proof release should wait until the scoring window closes. Baseline-solved tasks and held-out benchmark claims are kept out of paid activation.

## Privacy

Corpus rows never include local paths, hostnames, IPs, secrets, wallet files, verifier logs, or local agent state.

# Validator Guide

Validators are proof checkers.

Your job is to verify miner submissions with Lean and set weights according to accepted proof contributions.

## Validator Loop

1. Fetch or derive active tasks.
2. Receive miner submissions.
3. Run policy checks.
4. Run Lean verification in the pinned environment.
5. Compute proof identity hashes.
6. Deduplicate proofs.
7. Award one credit to the first accepted proof per task.
8. Set Bittensor weights proportional to credits.
9. Publish corpus rows for accepted proofs.

## Verification Requirements

Reject submissions that:

- do not compile;
- use `sorry` or `admit`;
- change the theorem statement;
- use custom axioms;
- use banned imports;
- exceed resource limits;
- rely on network access;
- fail axiom policy checks.

## Corpus Publication

After scoring closes, publish a JSONL delta for the epoch. Each row should include the task, proof, toolchain, verifier result, hashes, and solver attribution.

## No Subjective Scoring

Do not score based on style, explanations, model provider, or claimed effort. Score verified artifacts.

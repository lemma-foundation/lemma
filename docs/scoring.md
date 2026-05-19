# Scoring

Lemma rewards verified work through normal Bittensor miner and validator emissions.

Each epoch has `K` paid theorem slots. A miner earns one credit for being first to submit a unique accepted Lean proof for a slot. Unsolved slots remain unearned by default.

The reward is attached to verified work, not prose, claimed effort, or model identity.

## Proof-Unit Rule

```text
credit(miner) = count(first_valid_unique_verified_proof_per_task_by_miner)
score(miner) = credit(miner) / K
weight(miner) = credit(miner) / K
unearned_share = 1 - sum(miner_weights)
```

The unearned share is not redistributed to current solvers. The default policy is `burn`; `recycle` and `hold` are explicit policy rails for later proof-production funding.

## Empty Tempo Behavior

If no miner earns credit:

```text
all miner shares = 0
unearned_share = 1.0
```

The previous-weight fallback rule is removed from scoring.

## Exact Behavior

- A proof must pass the pinned verifier environment.
- A proof is task-bound by `task_id`, `task_version`, and `target_sha256`.
- A proof is unique by Lean `proof_term_hash` when available, with a clearly labelled weak script fallback until canonical proof-term extraction is production-ready.
- In production mode, full reward requires `proof_identity_strength: strong`.
- Each task pays at most one miner per validator epoch.
- Valid alternates become corpus rows with `rewarded: false`.
- Duplicate proof identities do not create extra rows or credit.

No subjective scoring is used. Held-out benchmark tasks are not paid tasks. Difficulty adapts through frontier depth; `K` adapts through validator throughput capacity.

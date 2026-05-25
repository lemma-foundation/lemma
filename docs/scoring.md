# Scoring

Lemma rewards verified work through normal Bittensor miner and validator emissions.

Each epoch has `K` paid theorem slots. A miner earns one credit for the rank-0 unique accepted Lean proof for a slot. On the bucket/commitment path, rank-0 means earliest miner Merkle-root commit block; equal commit blocks are tie-broken by Lean proof identity. Payment is weighted by the accepted proof's verifier-recorded Lean kernel dependencies. Unsolved slots remain unearned by default.

The reward is attached to verified work, not prose, claimed effort, or model identity.

## Proof-Unit Rule

```text
credit(miner) = count(rank_0_unique_verified_proof_per_task_by_miner)
score(miner) = sum(winning_slot_weight_by_miner) / sum(active_slot_weights)
weight(miner) = score(miner)
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
- A proof is unique by Lean `proof_term_hash` for paid production rewards. Lean structural fingerprints remain replay diagnostics; weak script fallbacks are non-production only.
- In production mode, full reward requires `proof_identity_strength: strong`.
- Each task pays at most one miner per validator epoch; committed reveals rank by commit block before local receipt time.
- Slot weights are deterministic registry values, not subjective validator scores.
- Valid alternates become corpus rows with `rewarded: false`.
- Duplicate proof identities do not create extra rows or credit.

No subjective scoring is used. Held-out benchmark tasks are not paid tasks. `frontier_depth` and `K` are retargeted from public tempo state: solve-rate history moves the frontier depth, while validator capacity and the public cost budget cap `K`. Production miners and validators must replay the same published state before deriving the next active window, and retarget rows activate only after one full tempo of public replay lag.

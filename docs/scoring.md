# Scoring

Lemma rewards verified proof data through normal Bittensor miner and validator emissions.

## V1 Rule

Each validator epoch has `K` active theorem tasks. A miner earns one credit when they are the first miner in that epoch to submit a unique proof that passes Lean for an active task.

```text
credit(miner) = count(first_valid_unique_proof_per_task_by_miner)
score(miner) = credit(miner) / K
weight(miner) = credit(miner) / sum(all_credits)
```

`score` is the protocol score event recorded for the epoch. `weight` is the normalized Bittensor weight vector when at least one miner earns credit.

## Exact Behavior

- A proof must pass Lean under the pinned verifier environment.
- A proof is task-bound by `task_id`, `task_version`, and `target_sha256`.
- A proof is unique by `proof_term_hash` when present, otherwise by `proof_sha256`.
- Each task pays at most one miner per validator epoch.
- Valid alternates become corpus rows with `rewarded: false`.
- Duplicate proof identities do not create extra rows or credit.
- If no miner earns credit, validators leave previous weights unchanged.

No subjective scoring is used. Google DeepMind Formal Conjectures solves are not a v1 payout category.

Future extensions such as difficulty modifiers, proof minimization, source diversity, proof repair, model benchmark submissions, or human review flags are out of the v1 score.

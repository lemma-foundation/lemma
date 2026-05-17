# Incentive Mechanism

Lemma rewards verified proof data.

The base rule is intentionally simple:

> A miner earns credit when they are the first to submit a unique proof that passes Lean for an active task.

Validators aggregate those credits and set Bittensor weights accordingly.

## Why Not Reward Formal Conjectures Directly?

Solving a frontier conjecture is valuable, but it is sparse and hard to make into the primary subnet reward. A solver might use Lemma's open data without having contributed to it. That is fine: the open data is meant to help the world. The subnet should reward the production of the data itself.

## V1 Scoring

For each epoch:

1. There is an active pool of tasks.
2. Miners submit proof artifacts.
3. Validators verify submissions.
4. For each task, the first accepted unique proof receives credit.
5. Miner weights are proportional to credits.

```text
credit(miner) = number of active tasks first solved by miner
weight(miner) = credit(miner) / total_credits
```

## Duplicate Handling

A proof is duplicate if it has the same proof identity as an earlier accepted proof for the same task. V1 should deduplicate at least by proof script hash and target hash. Later versions should add proof-term hashing.

## Alternate Proofs

Alternate proofs may be useful for training, but paying for them is exploitable at launch. V1 may store verified alternates as unpaid corpus rows. Rewarding alternates should wait until canonical proof-term deduplication and novelty filters are mature.

## Anti-Gaming Rules

- no `sorry`;
- no `admit`;
- no custom axioms;
- no changed theorem statement;
- no banned imports;
- no network access during verification;
- proof must pass the pinned Lean/mathlib environment;
- public release of proofs should wait until scoring closes;
- known public proofs should not be rewarded verbatim when avoidable.

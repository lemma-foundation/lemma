# Architecture

Lemma v1 has five components:

1. task supply;
2. miner proof search;
3. Lean verification;
4. validator scoring;
5. public corpus publication.

```text
Task queue -> Active pool -> Miner submissions -> Lean verifier -> Scoring -> Bittensor weights -> Corpus
```

## No Smart Contracts In V1

Lemma v1 does not custody funds and does not route owner emissions through a contract. Rewards flow through normal Bittensor miner/validator mechanics.

## Modules

Suggested modules:

- `lemma.tasks`
- `lemma.submissions`
- `lemma.lean`
- `lemma.scoring`
- `lemma.corpus`
- `lemma.prover_adapters`
- `lemma.validator`
- `lemma.miner`

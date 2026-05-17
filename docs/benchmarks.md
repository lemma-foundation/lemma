# Benchmarks

Benchmarks measure whether Lemma-trained provers are improving.

They are not the base reward stream in v1.

## Frontier Benchmarks

- Google DeepMind Formal Conjectures
- lean-eval
- miniF2F
- PutnamBench
- IMO Grand Challenge

## Policy

Do not train/reward on held-out benchmark tasks used for public claims. Keep training tasks, practice tasks, and held-out evaluation separate.

## Public Dashboard Metrics

- corpus rows;
- proofs per epoch;
- active miners;
- verification pass rate;
- task solve rate;
- benchmark solve rate of Lemma-trained models;
- Formal Conjectures solved by models trained on Lemma data, if known.

## Formal Conjectures Language

Use this phrasing:

> Google DeepMind's Formal Conjectures is a frontier benchmark for Lemma-trained provers. Lemma is independent and is not endorsed by Google DeepMind.

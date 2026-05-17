# Benchmarks

Benchmarks measure whether Lemma-trained provers are improving.

They are not the base v1 reward stream.

## Frontier Benchmarks

- Google DeepMind Formal Conjectures
- lean-eval
- miniF2F
- PutnamBench
- IMO Grand Challenge

Google DeepMind Formal Conjectures is referenced as a public frontier benchmark and downstream demonstration layer. Lemma is independent and is not endorsed by Google DeepMind.

## Policy

Training tasks, practice tasks, and held-out evaluation tasks must stay separate. Do not pay for a task that is being used as held-out evidence for public benchmark claims.

## Useful Metrics

- corpus rows;
- accepted proofs per epoch;
- active miners;
- verification pass rate;
- task solve rate;
- benchmark solve rate for models trained on Lemma data, when known.

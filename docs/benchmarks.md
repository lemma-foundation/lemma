# Benchmarks

Benchmarks measure whether Lemma-trained provers are improving.

They are not the base reward stream.

## Frontier Benchmarks

- Google DeepMind Formal Conjectures
- lean-eval
- miniF2F
- PutnamBench
- IMO Grand Challenge

Google DeepMind Formal Conjectures is referenced as a public frontier benchmark and downstream demonstration layer. Lemma is independent and is not endorsed by Google DeepMind.

Formal Conjectures is not the v1 payout path. It is useful as a downstream demonstration layer: train or tune provers on the Lemma Corpus, then measure whether they solve more frontier formal statements.

## Policy

Training tasks, practice tasks, and held-out evaluation tasks must stay separate. Do not pay for a task that is being used as held-out evidence for public benchmark claims.

## Corpus Export

Accepted corpus rows can be frozen into a compact JSONL export:

```bash
uv run lemma corpus benchmark-export --input corpus --output exports/lemma-proofs.jsonl --index exports/index.json
```

The export includes task statements, source/license metadata, accepted proof scripts, proof hashes, reward context, and verifier summaries. It is useful for downstream training and reproducible benchmark harnesses, but the command does not mark rows as held-out.

## Useful Metrics

- corpus rows;
- accepted proofs per epoch;
- active miners;
- verification pass rate;
- task solve rate;
- benchmark solve rate for models trained on Lemma data, when known.

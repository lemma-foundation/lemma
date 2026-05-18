# Affine Integration

Affine rewards model dominance. Lemma produces verifier-grounded corpora that can help models become dominant.

The relationship is data-consumer oriented:

- Lemma miners produce accepted artifacts.
- Lemma validators publish public corpus rows.
- Affine-style miners can train reasoning models on those rows.
- Improved models can perform better in model competitions and can also mine future Lemma tasks.

Lemma does not need Affine in its validation loop, and Affine does not need a transactional dependency on Lemma data. The clean integration path is export format compatibility.

## Lean Export Shape

```json
{
  "input": "theorem statement + imports",
  "target": "accepted proof",
  "domain": "lean",
  "verifier": "lake-build"
}
```

## Future Verus Export Shape

```json
{
  "input": "Rust function signature + spec + constraints",
  "target": "verified Rust implementation/proof annotations",
  "domain": "verus",
  "verifier": "verus"
}
```

The helper module is `lemma.corpus.affine_export`.

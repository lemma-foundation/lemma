# Affine Integration

Affine rewards model dominance. Lemma produces verified Lean proof corpora that can help theorem-proving models improve.

The relationship is data-consumer oriented:

- Lemma miners produce accepted proofs.
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

This is a roadmap example, not a live production export.

```json
{
  "input": "Rust function signature + spec + constraints",
  "target": "verified Rust implementation/proof annotations",
  "domain": "verus",
  "verifier": "verus"
}
```

The helper module is `lemma.corpus.affine_export`.

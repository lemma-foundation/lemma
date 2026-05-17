# The Lemma Corpus

The Lemma Corpus is the main product of the subnet.

It is a public dataset of Lean theorem/proof rows that were checked by validators and accepted by the network.

## Why It Matters

Verified proof data trains better theorem provers. The corpus should be replayable, source-attributed, and useful for supervised fine-tuning, retrieval, proof repair, evaluation, and reinforcement learning.

## Row Schema

See `spec/corpus-row.schema.json`.

Minimum fields:

- task id;
- statement;
- imports;
- Lean toolchain;
- mathlib revision;
- proof script;
- proof hash;
- proof-term hash when available;
- solver hotkey;
- epoch/tempo;
- verifier result;
- source stream.

## Replay

A row should be replayable with:

```bash
lemma corpus replay corpus.jsonl
```

Replay is critical. The corpus is only trustworthy if anyone can re-run the checker.

## License

Use a permissive license compatible with training and public reuse. Proposed default: CC-BY 4.0 for corpus rows, with code/proof artifacts under Apache-2.0 where appropriate. Confirm license compatibility for imported sources.

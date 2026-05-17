# What Is Lemma?

Lemma is a Bittensor subnet for producing open, Lean-verified mathematical training data.

Miners solve Lean theorem tasks. Validators check those proofs with Lean. Verified proofs are rewarded through normal Bittensor weights and added to a public corpus.

## Why It Exists

AI systems can generate plausible mathematical text, but plausible is not enough. Mathematics needs proofs. Lean gives Lemma a mechanical correctness check: a proof either builds under the pinned environment or it does not.

The bottleneck for better mathematical AI is not just bigger models. It is high-quality verified data and reliable feedback. Lemma is designed to create that data continuously.

## The Simple Loop

```text
Task → Proof → Lean check → Reward → Corpus → Better prover
```

## The Long-Term Goal

The long-term goal is to train systems that can help solve increasingly hard mathematics. Google DeepMind's Formal Conjectures, lean-eval, PutnamBench, miniF2F, and the IMO Grand Challenge are benchmarks for that progress.

Lemma does not need to directly reward every frontier solve. If models trained on Lemma's open corpus solve harder problems, Lemma is working.

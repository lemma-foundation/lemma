# What Is Lemma?

Lemma is an open AlphaProof-style proof-data engine for Lean.

Miners search for proofs of active Lean theorem tasks. Validators check those proofs with Lean. Accepted proof units are rewarded through normal Bittensor weights and appended to the Lemma Corpus.

The design is intentionally binary. A proof passes the pinned verifier or it fails. Lemma does not reward prose, model branding, claimed effort, or subjective reasoning quality.

## Why It Exists

Mathematical AI needs more than plausible text. It needs checked proof data that can train models, support retrieval, drive proof repair, and measure progress.

Lean gives the subnet a mechanical correctness signal. Lemma turns that signal into an open dataset loop:

```text
formal tasks -> proof search -> Lean check -> public corpus -> better prover
```

## V1 Boundaries

Lemma v1 uses normal Bittensor miner and validator emissions. It does not use smart contracts, escrow custody, owner-cut routing, or contract-routed payouts.

Google DeepMind Formal Conjectures is a frontier benchmark and downstream demonstration layer. It is not the base v1 reward stream, and Lemma is not endorsed by Google DeepMind.

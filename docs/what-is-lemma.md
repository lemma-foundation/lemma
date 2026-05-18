# What Is Lemma?

Lemma is a Bittensor subnet for producing open, verifier-grounded training data.

Miners search for accepted artifacts on active tasks. Validators check those artifacts with deterministic, pinned verifiers. Accepted artifacts become Bittensor weight entries and append to the Lemma Corpus.

The design is intentionally binary. A proof passes the pinned verifier or it fails. Lemma does not reward prose, model branding, claimed effort, or subjective reasoning quality.

Lean theorem proving is the only production launch domain. Math is the wedge; verified data is the product.

## Why It Exists

Reasoning AI needs more than plausible text. It needs checked data that can train models, support retrieval, drive repair loops, and measure progress.

Lean gives the subnet a mechanical correctness signal. Lemma turns that signal into an open dataset loop:

```text
formal tasks -> artifact search -> verifier -> public corpus -> better model
```

## V1 Boundaries

Lemma v1 uses normal Bittensor miner and validator emissions. It does not use smart contracts, escrow custody, owner-cut routing, or contract-routed payouts.

Google DeepMind Formal Conjectures is a frontier benchmark and downstream demonstration layer. It is not the base v1 reward stream, and Lemma is not endorsed by Google DeepMind.

Non-Lean domains are not active yet. Verus/Rust verification is the first experimental stub because it maps directly to code reasoning and program-deduction data.

# Dependency Graph

Lean is the only production domain today, but corpus rows are graph-shaped from the beginning.

Each accepted row links these nodes:

- task node: exact theorem target and target hash;
- proof node: submitted accepted proof artifact;
- identity node: proof identity or duplicate family;
- source node: source stream, source reference, and license;
- verifier node: Lean verifier version, toolchain, and Mathlib revision;
- solver node: miner attribution;
- validator node: validator attribution.

Initial dependency fields include imports, prior Lemma rows where available, dependency depth, and a transitive dependency hash. This keeps the first corpus usable as a graph even before reward mechanisms depend on graph algorithms.

The launch rule is simple: graph metadata is foundational, but production rewards still come from deterministic Lean acceptance plus eligibility gates. Future mechanisms should add graph edges rather than inventing separate state.

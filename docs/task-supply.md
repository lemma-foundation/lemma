# Task Supply

Lemma needs many theorem tasks that are useful, nontrivial, and verifiable.

## Good Task Criteria

A task should be:

- exact Lean;
- kernel-checkable;
- nontrivial under a pinned tactic stack;
- useful as training data;
- not easily copied from public proof files;
- reproducible by validators;
- licensed for inclusion in the public corpus.

## Supply Streams

### Generated State-Graph Tasks

Generate theorem tasks by exploring Lean proof states from pinned Mathlib commits. This is inspired by LeanNavigator-style state-graph exploration.

### Proof Repair Tasks

Create tasks from broken Lean files. A valid solution repairs the proof under the pinned environment.

### Theorem Variant Tasks

Generate variants of known theorems that require a real proof rather than a copied public proof.

### Premise-Limited Reproving Tasks

Ask miners to reprove a theorem without using the original theorem or a banned group of nearby lemmas.

### Benchmark-Practice Tasks

Use non-held-out public benchmark tasks for training practice. Keep held-out benchmark tasks separate from paid tasks.

### Human-Curated Tasks

Allow curated tasks, but do not make manual curation the bottleneck.

## Triviality Gate

Before a task becomes active, run baseline tactics under timeout. Skip or downweight tasks solved by the baseline.

Suggested tactics:

- `rfl`
- `trivial`
- `simp` / `simp_all`
- `norm_num`
- `omega`
- `linarith`
- `ring`
- `aesop` with timeout

# Tasks

Lemma tasks are exact Lean theorem targets with source and license metadata.

Miners do not choose arbitrary theorem statements for v1 scoring. Validators publish an active registry, and every submission must bind to one exact task row.

## Source Streams

V1 uses these source streams:

- `generated`
- `proof_repair`
- `theorem_variant`
- `premise_limited`
- `benchmark_practice`
- `human_curated`

## Activation Rules

Every active task must have:

- stable `task_id`;
- integer `task_version`;
- `target_sha256` computed from the verifier-owned `Challenge.lean`;
- pinned Lean toolchain and Mathlib revision;
- explicit `source_ref`;
- explicit `source_license`;
- schema validation;
- baseline tactic gate result;
- no held-out benchmark status if it is used for public benchmark claims.

Tasks solved by trivial baseline tactics are excluded from paid activation. Held-out benchmark tasks are kept separate from training and reward streams.

## Registry

`tasks/registry.json` is a dev seed. Published registries should be signed JSON, pinned by SHA256, and archived so corpus rows can be replayed later.

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
uv run lemma tasks pull --output active-tasks.jsonl
```

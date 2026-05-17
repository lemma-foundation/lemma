# Task Supply

Lemma tasks are exact Lean theorem targets with source and license metadata.

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

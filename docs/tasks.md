# Tasks

Lemma tasks are exact Lean theorem targets with source and license metadata.

Miners do not choose arbitrary theorem statements for v1 scoring. Validators publish an active deterministic queue, and every submission must bind to one exact task row.

## Supply Streams

Launch interfaces cover these streams:

- `mathlib_snapshot`: proof-erased Mathlib statements.
- `mathlib_perturbation`: nearby variants of known theorems.
- `state_graph`: intermediate proof-state tasks.
- `auto_formalized`: Lean statements generated from natural-language sources.
- `conjecture_generated`: generated Lean conjectures from Mathlib context.
- `hard_target_variant`: scaffolded variants around stalled hard targets.
- `trivial_curriculum`: useful easy rows that should not receive paid frontier emission.
- Existing dev streams such as `generated`, `proof_repair`, `theorem_variant`, `premise_limited`, `benchmark_practice`, and `human_curated`.

Heavy generators run off-chain. Validators check deterministic task artifacts, not model inference.

`mathlib_snapshot` supply starts from JSONL rows exported by an off-chain Mathlib checkout:

```json
{"theorem_name":"Nat.zero_add","type_expr":"∀ n : Nat, 0 + n = n","mathlib_rev":"...","source_path":"Mathlib/Data/Nat/Basic.lean"}
```

The importer erases the known proof into a `sorry` target and preserves source revision, file path, license, imports, and optional erased-proof hash as metadata.

Build a pinned registry artifact from those rows with:

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input snapshot.jsonl \
  --output tasks/mathlib-snapshot.registry.json
```

The command writes deterministic `queue_position` values after shallow-first task ordering and prints the registry SHA256. Operators can attach externally produced `signed_by` / `signature` metadata, but the command does not pretend to provide production signing.

## Activation Gates

Every active task must have:

- stable `task_id`;
- integer `task_version`;
- `target_sha256` computed from verifier-owned `Challenge.lean`;
- pinned Lean toolchain and Mathlib revision;
- explicit `source_ref` and `source_license`;
- `queue_position`, `queue_depth`, and optional `frontier_depth`;
- schema validation;
- policy and triviality-gate labels.

Tasks solved by the pinned triviality tactic stack are excluded from paid activation. They may still enter the corpus as shallow `trivial_curriculum` data. Held-out benchmark tasks stay separate from training and reward streams.

## Queue And K

The active pool is a deterministic queue window of size `K`.

- `K` controls paid throughput and validator load.
- `queue_depth` / `frontier_depth` is the protocol difficulty proxy.
- Solved slots advance.
- Expired unsolved slots are parked.
- Zero solve rate halts frontier advancement and requests hard-target variants around stalled tasks.

Validator selection uses:

```text
LEMMA_ACTIVE_K
LEMMA_FRONTIER_DEPTH
LEMMA_ACTIVE_QUEUE_SEED
```

Only tasks in the selected active window are valid for scoring in that validator pass.

## Registry

`tasks/registry.json` is a dev seed. Published registries should be signed JSON, pinned by SHA256, and archived so corpus rows can be replayed later.

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
uv run lemma tasks pull --output active-tasks.jsonl
uv run lemma tasks build-mathlib-snapshot --input snapshot.jsonl --output tasks/mathlib-snapshot.registry.json
```

See [Operator Registry Flow](operator-registry-flow.md) for the production sequence that pins the registry hash, configures the active window, validates submissions, and exports corpus data.

# Tasks

Lemma tasks are exact verifier targets with source and license metadata.

Miners do not choose arbitrary targets for scoring. Validators derive the active deterministic queue from public inputs, and every submission must bind to one exact task row.

Lean theorem proving is the only active production domain today. Legacy Lean tasks use `schema_version: 1`; dataset exports upgrade them to task schema v2 with `domain_id: lean`, `verifier_id: lake-build`, and `task_type: theorem_proving`.

## Supply Streams

Launch interfaces distinguish production paid supply from development supply.

Paid production supply is `procedural`: every paid task is generated from the pinned public source pool and the future finalized Bittensor tempo-boundary block hash. There is no paid production registry publisher. Validators derive the same active tasks from the same public inputs.

Paid production uses epoch-derived generation. Development may keep a static queue seed, but production requires `LEMMA_ACTIVE_SEED_MODE=epoch_randomness` and `LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_block_hash`. The internal epoch number is the chain tempo index; it can be displayed as 1-based in UI, but validators use the same 0-based integer from `block // tempo`.

The block-hash source is deterministic: validators take the epoch's first chain block and read that block hash. A validator that resolves different public fields lands on different task targets and should fail closed.

For each production epoch, validators derive:

```text
epoch_randomness = hash(anchor_block_hash)
epoch_seed = hash(netuid, tempo, LEMMA_ACTIVE_QUEUE_SEED, epoch_randomness)
active_tasks = generate(source_pool, epoch_seed, frontier_depth, K)
```

Paid procedural rows carry that `epoch_seed` as `metadata.generation_seed` and the anchor block hash as `metadata.anchor_block_hash`. This prevents a static future playlist: rows generated for a different epoch seed fail production activation.

Development and curriculum interfaces cover these streams:

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
{"theorem_name":"Nat.zero_add","type_expr":"∀ n : Nat, 0 + n = n","mathlib_rev":"...","source_path":"Mathlib/Data/Nat/Basic.lean","source_license":"Apache-2.0"}
```

The importer erases the known proof into a `sorry` target and preserves source revision, file path, license, imports, and optional erased-proof hash as metadata.

An operator can extract rows from a pinned Mathlib checkout:

```bash
uv run lemma tasks extract-mathlib-snapshot \
  --mathlib-root /path/to/mathlib \
  --lake-root /path/to/lake-project \
  --elaborate-types \
  --include 'Mathlib/Data/Nat/*.lean' \
  --depth0-limit 10 \
  --depth1-limit 20 \
  --depth2-limit 20 \
  --output snapshot.jsonl
```

Build a pinned registry artifact from those rows with:

```bash
uv run lemma tasks build-mathlib-snapshot \
  --input snapshot.jsonl \
  --output tasks/mathlib-snapshot.registry.json
```

The command writes deterministic `queue_position` values after shallow-first task ordering and prints the registry SHA256. Operators can attach externally produced `signed_by` / `signature` metadata, but the command does not sign or verify the registry.

For production-shaped supply, configure the source pool directly:

```bash
LEMMA_TASK_SOURCE_POOL_URL=snapshot.jsonl
LEMMA_TASK_SOURCE_POOL_SHA256_EXPECTED=<snapshot_jsonl_sha256>
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_block_hash
```

When the epoch block exists, validators generate the active tasks directly from those inputs. The generator records the two-step transformation from Mathlib snapshot row to seed-bound procedural task. The mixed builder remains useful for local smoke and curriculum tuning. It is not the paid production supply path.

See [Mathlib Extraction Contract](mathlib-extraction.md) for the JSONL row contract and the off-chain extraction boundary.

## Activation Gates

Every active task must have:

- stable `task_id`;
- integer `task_version`;
- `domain_id`;
- `verifier_id`;
- pinned verifier version;
- `target_sha256` computed from verifier-owned `Challenge.lean`;
- pinned Lean toolchain and Mathlib revision;
- explicit `source_ref` and `source_license`;
- `queue_position`, `queue_depth`, and optional `frontier_depth`;
- schema validation;
- policy, topic metadata, and triviality-gate labels.
- for paid production rows, procedural depth-2 provenance and deterministic slot weight metadata.

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
LEMMA_ACTIVE_SEED_MODE
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE
```

Only tasks in the selected active window are valid for scoring in that validator pass.

## Registry

`tasks/registry.json` is a dev seed. Local registries are still useful for smoke tests, curriculum experiments, and replay fixtures.

`signed_by` and `signature` are metadata unless registry signature verification is enabled. They do not choose paid production problems.

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
uv run lemma tasks pull --output active-tasks.jsonl
uv run lemma tasks extract-mathlib-snapshot --mathlib-root /path/to/mathlib --output snapshot.jsonl
uv run lemma tasks build-mathlib-snapshot --input snapshot.jsonl --output tasks/mathlib-snapshot.registry.json
```

See [Operator Flow](operator-registry-flow.md) for the production sequence that pins the source pool, configures block-hash generation, validates submissions, and exports corpus data.

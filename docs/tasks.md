# Tasks

Lemma tasks are exact verifier targets with source and license metadata.

Miners do not choose arbitrary targets for scoring. Validators publish an active deterministic queue, and every submission must bind to one exact task row.

Lean theorem proving is the only active production domain today. Legacy Lean tasks use `schema_version: 1`; dataset exports upgrade them to task schema v2 with `domain_id: lean`, `verifier_id: lake-build`, and `task_type: theorem_proving`.

## Supply Streams

Launch interfaces distinguish production paid supply from development supply.

Paid production supply is `procedural`: every paid task must be generated from the pinned source pool by a deterministic depth-2 mutation chain anchored to chain/drand state. Validators should be able to rebuild the same active pool from the same public inputs.

The source pool is `Mathlib_pinned ∪ Lemma_substrate_<t>`. Mathlib rows come from `LEMMA_PROCEDURAL_SOURCE_JSONL`; prior accepted Lemma rows can be added with `LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR`. Sampling is deterministic and mixes citation-weighted order with uniform order through `LEMMA_PROCEDURAL_CITATION_ALPHA` and `LEMMA_PROCEDURAL_CITATION_WEIGHT_CAP`.

Paid production also uses epoch-derived active selection. Development may keep a static queue seed, but SN467 burn-in and mainnet both require `LEMMA_ACTIVE_SEED_MODE=epoch_randomness` and `LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand`. The internal epoch number is the chain tempo index; it can be displayed as 1-based in UI, but validators use the same 0-based integer from `block // tempo`.

The chain/drand source is deterministic: validators take the epoch's first chain block, read that block's hash and timestamp, map the timestamp to the Drand Quicknet round, fetch that round's signature, and hash those public fields into the epoch seed. A validator that resolves different public fields lands on a different active-set manifest and should fail closed.

For each production epoch, validators derive:

```text
epoch_randomness = hash(anchor_block_hash, anchor_block_timestamp, drand_round, drand_signature)
epoch_seed = hash(netuid, tempo, LEMMA_ACTIVE_QUEUE_SEED, epoch_randomness)
active_selection_seed = hash(epoch_seed, registry_sha256, frontier_depth)
```

Paid procedural rows must carry that `epoch_seed` as `metadata.generation_seed`. This keeps generation procedural while preventing a static future playlist: rows generated for a different epoch seed fail production activation.

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

For production-shaped supply, rebuild depth-2 procedural candidates from the
public source snapshot and the tempo's chain/drand seed:

```bash
uv run lemma tasks rebuild-procedural-registry \
  --mathlib-snapshot snapshot.jsonl \
  --generation-seed "$EPOCH_SEED" \
  --epoch-randomness "$EPOCH_RANDOMNESS_JSON" \
  --tempo "$TEMPO" \
  --count "$K" \
  --triviality-retarget-jsonl public-settlements.jsonl \
  --output tasks/mainnet.registry.json
```

The procedural generator derives rows from the source pool and epoch seed; it is
not a static playlist. Each mutation step records the chain-pinned operator
bundle version, the selected operator, its drand-keyed params, and the
input/output statement hashes. The procedural builder rejects paid rows unless
they carry procedural depth-2 provenance, chain/drand anchoring, source-pool and
operator-bundle hashes, clean license state, a recomputable `slot_weight`
receipt, a recomputable `T(t)` triviality-budget receipt, and a Lean-backed gate receipt. Production receipts must come from the
`lean` gate runner: Lean typecheck, kernel Prop gate, canonical novelty, the
pinned triviality stack retargeted from public burn history, and deterministic import/dependency slot-weight
calculation must all run during generation, and any candidate solved by the
stack is excluded from paid supply.

The mixed builder remains useful for local smoke and curriculum tuning. It is not the paid production supply path.

In production procedural mode, validators rebuild the same active task set locally:

```bash
LEMMA_TASK_SUPPLY_MODE=procedural
LEMMA_PROCEDURAL_SOURCE_JSONL=snapshot.jsonl
LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR=corpus
LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED=<source-pool-sha256>
LEMMA_PROCEDURAL_CITATION_ALPHA=0.25
LEMMA_PROCEDURAL_CITATION_WEIGHT_CAP=100
LEMMA_PROCEDURAL_GATE_TIMEOUT_S=120
LEMMA_PROCEDURAL_TRIVIALITY_BUDGET_S=120
LEMMA_PROCEDURAL_TRIVIALITY_RETARGET_JSONL=public-settlements.jsonl
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand
```

Published registry files remain useful as caches and audit artifacts, but they
are not the authority that invents paid problems in procedural mode.

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
- for paid production rows, procedural depth-2 provenance with drand-keyed operator params and recomputable slot-weight receipt metadata.

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

`tasks/registry.json` is a dev seed. In production, validators rebuild procedural tasks from a pinned public source pool plus epoch randomness. Published registries are caches and replay artifacts, not authority.

`signed_by` and `signature` are metadata unless registry signature verification is enabled for a dev or cache-distribution flow. Production mode requires `LEMMA_TASK_SUPPLY_MODE=procedural` and `LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED`; registry signatures do not make registry-mode supply production-valid.

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
uv run lemma tasks pull --output active-tasks.jsonl
uv run lemma tasks extract-mathlib-snapshot --mathlib-root /path/to/mathlib --output snapshot.jsonl
uv run lemma tasks build-mathlib-snapshot --input snapshot.jsonl --output tasks/mathlib-snapshot.registry.json
```

See [Operator Registry Flow](operator-registry-flow.md) for the production sequence that pins the source pool, configures the active window, validates submissions, and exports corpus data.

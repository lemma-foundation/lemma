# Operator Registry Flow

This is the end-to-end path for running Lemma from a pinned real-task registry, validating miner bucket reveals, and exporting accepted proof data.

## 1. Prepare A Registry

Build or obtain a registry of real Lean missing-proof tasks. Each row must include public source metadata, license metadata, target hash, Lean toolchain, Mathlib revision, and submission stub.

For one pinned SorryDB row plus a local checkout of that row's repository commit:

```bash
uv run lemma tasks import-sorrydb \
  --sorry-json sorrydb-row.json \
  --source-root ./upstream-checkout \
  --theorem-name Namespace.target \
  --type-expr "forall n : Nat, n + 0 = n" \
  --source-license Apache-2.0 \
  --mathlib-rev <mathlib-or-project-pin> \
  --reproduction-command "lake build" \
  --output tasks/real-source.registry.json
```

The importer records public source metadata and hashes from the checkout. It does not store the local checkout path in the task row.
`--sorry-json` can also be a JSON array; batch rows should carry their own `theorem_name`, `type_expr`, and optional `task_id`.

```bash
uv run lemma tasks sign-registry \
  --input tasks/registry.json \
  --output tasks/signed.registry.json \
  --key-uri //Alice
```

The command prints the final `registry_sha256`. Keep the registry artifact and SHA256 together.

## 2. Configure The Active Window

```bash
LEMMA_TASK_SUPPLY_MODE=registry
LEMMA_TASK_REGISTRY_URL=tasks/signed.registry.json
LEMMA_TASK_REGISTRY_SHA256_EXPECTED=<registry-sha256>
LEMMA_VERIFY_REGISTRY_SIGNATURES=1
LEMMA_ACTIVE_REGISTRY_CACHE_DIR=active-registries
LEMMA_SOURCE_CHECKOUT_ROOT=source-checkouts
LEMMA_ACTIVE_K=10
LEMMA_FRONTIER_DEPTH=0
LEMMA_ACTIVE_QUEUE_SEED=lemma-active-queue
LEMMA_ACTIVE_TEMPO_SOURCE=chain
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand
LEMMA_CORPUS_OUTPUT_DIR=corpus
LEMMA_OPERATOR_DATA_DIR=validator-data
```

`LEMMA_ACTIVE_K` is validator throughput. `LEMMA_FRONTIER_DEPTH` opens deeper task rows. Payment uses deterministic active slot weights, not subjective validator scores.

When `LEMMA_ACTIVE_REGISTRY_CACHE_DIR` is set, miners and validators can hydrate `tempo-<tempo>.registry.json` files for faster startup. Cache files are distribution artifacts. The registry SHA pin remains the public task authority.

Patch validation resolves public source refs under `LEMMA_SOURCE_CHECKOUT_ROOT` as:

```text
source-checkouts/<source-kind>/<source-name>/<commit>/
```

For example, `source_ref: {kind: "sorrydb", name: "owner/repo", commit: "<sha>"}` resolves to `source-checkouts/sorrydb/owner__repo/<sha>/`. The checkout must already be at the pinned commit.

Operators can inspect the exact location for one task with:

```bash
uv run lemma tasks checkout-path <task-id>
```

They can prepare that checkout from the task's public source URL and commit with:

```bash
uv run lemma tasks materialize-checkout <task-id>
```

For a batch registry, operators can prepare all patch-task checkouts with:

```bash
uv run lemma tasks materialize-checkouts
```

`lemma operator preflight` reports `source_checkouts` as failing when active patch tasks cannot resolve to existing checkout directories.

For live curriculum retargeting, the state log updates throughput and depth after each completed tempo:

```bash
LEMMA_CURRICULUM_RETARGET=1
LEMMA_CURRICULUM_STATE_JSONL=validator-data/curriculum.jsonl
LEMMA_CURRICULUM_STATE_PUBLIC=1
LEMMA_VALIDATOR_CAPACITY=20
LEMMA_CURRICULUM_COST_BUDGET_S=2700
LEMMA_CURRICULUM_BASE_TASK_COST_S=180
LEMMA_CURRICULUM_DEPTH_COST_MULTIPLIER=2
```

Production mode accepts retargeting only when `LEMMA_CURRICULUM_STATE_PUBLIC=1`; operators should sync the state from canonical public Proof Atlas artifacts, not private scratch logs.

## 3. Preflight

```bash
uv run lemma operator preflight
uv run lemma operator diagnostics --output operator-diagnostics-before.json
```

Preflight checks registry loading, registry SHA pinning, registry signature status, active-window size, production gates, Lean verifier setup, operator directories, curriculum state, live miner authentication, commit/reveal settings, strong proof identity, and disabled Lean verifier networking.

## 4. Miner Submission

Miners may use any prover implementation. The reference CLI can package a proof:

```bash
uv run lemma submit lemma.sample.true_intro \
  --submission Submission.lean \
  --solver-hotkey <miner-hotkey> \
  --output submission.json
```

For live production, miners publish bucket commitments and reveal after the drand round opens. Validator intake uses:

```bash
uv run lemma validate \
  --once \
  --bucket-reveals-jsonl bucket-reveals.jsonl \
  --verify-chain-commitments \
  --verify-drand-reveals \
  --no-set-weights
```

Rank-0 accepted proofs earn their deterministic active slot share. Unsolved slots contribute to `unearned_share`.

## 5. Export And Publish

After validation:

```bash
uv run lemma operator diagnostics --output operator-diagnostics-after.json
uv run lemma corpus benchmark-export \
  --input corpus \
  --output exports/sn467/lemma-proofs.jsonl \
  --index exports/sn467/benchmark-index.json
uv run python scripts/leak_check.py
```

`operator-diagnostics-after.json` reports artifact counts for validator runs, verification records, score events, corpus rows, active tasks, registry inspection, and curriculum state. The public run log is `validator-runs.jsonl`.

The Proof Atlas publisher syncs accepted proof rows, canonical storage artifacts, and registry cache files:

```bash
uv run python scripts/publish_proof_atlas_snapshot.py \
  --repo "$LEMMA_PROOF_ATLAS_REPO" \
  --netuid "sn${BT_NETUID}" \
  --sync-proof-dir "$LEMMA_CORPUS_OUTPUT_DIR" \
  --sync-canonical-dir "$LEMMA_CANONICAL_OUTPUT_DIR/sn${BT_NETUID}" \
  --sync-registry-cache-dir "$LEMMA_ACTIVE_REGISTRY_CACHE_DIR" \
  --dry-run
```

Use `--push-repo` only after the dry-run and leak scan are clean.

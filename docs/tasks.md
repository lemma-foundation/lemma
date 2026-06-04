# Tasks

Lemma tasks are exact Lean verifier targets with source and license metadata.

Miners do not choose arbitrary targets for scoring. Validators publish or load a pinned task registry, derive the active window deterministically, and every submission must bind to one exact task row.

## Supply

Production supply is registry-backed real Lean work. Paid tasks must come from public source rows such as:

- `sorrydb`
- `formal_conjectures`
- `lean_project`
- carefully reviewed `human_curated` rows

Fixed fixtures are allowed for local smoke and calibration only. They are not production paid supply.

The launch path does not include local problem generators or task-bank bridges. Registries are task ledgers, not problem generators.

One supported ingestion path is a pinned SorryDB row plus a local checkout of that row's repository commit:

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

The importer creates patch tasks. Operators still need to pin and publish the resulting registry SHA before validators score against it.

Validators do not get source roots from task metadata in production. They resolve patch-task checkouts from `LEMMA_SOURCE_CHECKOUT_ROOT` using the task's public `source_ref`:

```text
<root>/<source-kind>/<source-name>/<commit>/
```

Slashes and unsafe characters in `source-name` are encoded as `__`; for example, `owner/repo` becomes `owner__repo`.

## Task Rows

Every active task must have:

- stable `task_id`
- integer `task_version`
- `task_format`: `isolated_proof`, `patch`, or `helper_lemma`
- `task_class` and `source_value` for active-set stratification and public source priority
- `domain_id`
- `verifier_id`
- pinned verifier version
- `target_sha256` computed from verifier-owned `Challenge.lean`
- `target_type_sha256` computed from the published target type
- pinned Lean toolchain and Mathlib revision
- allowed artifact files and allowed Lean imports
- exact reproduction command metadata when the source environment is certified
- explicit `source_ref` and `source_license`
- `queue_position`, `queue_depth`, and optional `frontier_depth`
- schema validation
- policy, topic metadata, and activation metadata

For production real-source tasks, `source_ref` must include enough public location data to replay the target: source kind, name, URL, commit, and path when the source is a public repository.

## Active Window

The active pool is a deterministic queue window of size `K`.

- `K` controls paid throughput and validator load.
- `frontier_depth` controls how deep the task pool is open.
- Active selection interleaves frontier and foundation levels, then balances source families inside each level.
- Slot weights use a capped `sqrt(queue_depth + 1)` depth prior.
- Source wrappers, public-known solutions, baseline-solved tasks, and held-out benchmark tasks are excluded from paid activation.

Validator selection uses:

```text
LEMMA_ACTIVE_K
LEMMA_FRONTIER_DEPTH
LEMMA_ACTIVE_QUEUE_SEED
LEMMA_ACTIVE_SEED_MODE
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE
LEMMA_CURRICULUM_RETARGET
LEMMA_CURRICULUM_STATE_JSONL
LEMMA_CURRICULUM_STATE_PUBLIC
```

Only tasks in the selected active window are valid for scoring in that validator pass.

## Registry

`tasks/registry.json` is a dev seed. Production operators should point validators at a published, SHA-pinned registry containing real missing-proof tasks.

Registry signatures are optional cache-distribution checks. In production, use `LEMMA_VERIFY_REGISTRY_SIGNATURES=1` when the registry publisher is expected to sign the exact registry payload.

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
uv run lemma tasks pull --output active-tasks.jsonl
uv run lemma tasks sign-registry --input tasks/registry.json --output signed.registry.json --key-uri //Alice
uv run lemma operator preflight
```

Production registry settings:

```bash
LEMMA_TASK_REGISTRY_URL=https://example.org/tasks/sn467.registry.json
LEMMA_TASK_REGISTRY_SHA256_EXPECTED=<registry-sha256>
LEMMA_TASK_SUPPLY_MODE=registry
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand
LEMMA_REQUIRE_SUBMISSION_SIGNATURES=1
LEMMA_REQUIRE_COMMIT_REVEAL=1
LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1
LEAN_SANDBOX_NETWORK=none
```

## Submission Binding

Every submission is checked against:

- `task_id`
- `task_version`
- `target_sha256`
- theorem name and type
- import envelope
- submission policy
- solver hotkey and live signature settings
- commit/reveal metadata in production

The validator scores accepted Lean proofs, not task descriptions, display names, or claimed effort.

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

`--sorry-json` may also be a JSON array or a SorryDB dataset object with a `sorries` array. For batch imports, put `theorem_name`, `type_expr`, and optional `task_id` on each row when those values differ across tasks. Use `--source-checkout-root` instead of `--source-root` when rows should resolve source files from deterministic checkout paths.

### Filtered batch ingestion

`import-sorrydb` fails the whole batch on the first bad row. For larger source pulls use `tasks ingest-sorrydb`, which screens each row independently and never crashes: every row is either accepted as a candidate or quarantined with a structured reason.

```bash
uv run lemma tasks ingest-sorrydb \
  --sorry-json sorrydb-rows.json \
  --source-checkout-root ./checkouts \
  --source-license Apache-2.0 \
  --mathlib-rev <mathlib-or-project-pin> \
  --reproduction-command "lake build" \
  --output tasks/sorrydb.registry.json \
  --report tasks/sorrydb.report.json
```

The registry holds only accepted candidates. The report records, per run:

- `accepted` candidates (task id, public `repo@commit:path`, target hashes, toolchain, baseline status);
- `quarantined` rows with a `QuarantineReason` (`no_target_hole`, `extra_holes`, `decl_not_found`, `unsafe_path`, `not_lean4`, `unstable_toolchain`, `missing_environment`, `missing_license`, `duplicate_target`, `baseline_trivial`, `malformed_row`, `ingest_error`);
- per-`repo@commit` environment certification (env hash, toolchain, lake-manifest presence, `certified` flag).

The source filter accepts Lean 4 rows on a pinned, reproducible toolchain (`leanprover/lean4:vX.Y.Z[-rcN]`) whose checkout has both a recoverable environment hash and a `lake-manifest.json` (so dependency revisions are pinned), dropping ambiguous, broken, or duplicate targets. Pass `--allow-unstable-toolchain` to keep nightly pins and `--allow-extra-holes` to keep multi-gap files.

Add `--run-baseline` to screen candidates against trivial tactics (`rfl`, `simp`, `aesop`, `omega`, ...): any target a baseline one-liner closes is quarantined as `baseline_trivial`. Baseline screening runs Lean, so it requires a working toolchain in each checkout. The report is deterministic from public inputs and contains no local paths.

### Formal Conjectures ingestion

Formal Conjectures statements are packaged as restated **isolated-proof** tasks. This ingester consumes pre-extracted records (a separate extractor turns the Formal Conjectures Lean repo into records); it does not parse Lean itself.

```bash
uv run lemma tasks ingest-formal-conjectures \
  --records-json fc-records.json \
  --source-license CC-BY-4.0 \
  --mathlib-rev <mathlib-pin> \
  --output tasks/fc.registry.json \
  --report tasks/fc.report.json
```

Each record describes one public statement: `id`, `category`, `type_expr`, `imports`, the original `theorem_name`, the `source_module` that exposes the original sorry-backed declaration, an FC repo pin, and optional `references`. The ingester:

- accepts only safe v1 categories (`solved`, `textbook`, `api`, `test`) and quarantines the rest as `unsafe_category`;
- restates each target as a clean `namespace Submission` / `theorem target` isolated proof that imports only the record's declared modules (default `Mathlib`);
- enforces the anti-cheat packaging rule so a miner cannot win by `exact original_problem`: a record is quarantined `exposes_original` if its imports would expose the original module, `requires_source_import` if it cannot be separated from that module, and `references_original` if the restated type still names the original declaration. At validation time, importing the original module is rejected as `forbidden_import`.

New candidates default to held-out **benchmark** framing (`activation_status="benchmark"`, `held_out_benchmark` metadata, no reward). Opt a record into paid work with `"paid": true`, or default the whole run to paid with `--paid-default`. This keeps paid work and benchmark claims separate unless an operator deliberately promotes a statement.

Validators do not get source roots from task metadata in production. They resolve patch-task checkouts from `LEMMA_SOURCE_CHECKOUT_ROOT` using the task's public `source_ref`:

```text
<root>/<source-kind>/<source-name>/<commit>/
```

Slashes and unsafe characters in `source-name` are encoded as `__`; for example, `owner/repo` becomes `owner__repo`.

To inspect one task's expected checkout location:

```bash
uv run lemma tasks checkout-path <task-id>
```

To clone or update that checkout from the task's public `source_ref.url` and pinned `source_ref.commit`:

```bash
uv run lemma tasks materialize-checkout <task-id>
```

To prepare every patch-task checkout in the configured registry:

```bash
uv run lemma tasks materialize-checkouts
```

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

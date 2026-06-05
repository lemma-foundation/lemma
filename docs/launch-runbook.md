# Launch Runbook

This runbook covers the two launch steps that `lemma launch-check` cannot verify on its own:

1. **Reach the real-task volume target** (100-300 packaged, validated tasks) by running the source ingesters on live sources.
2. **Container replay + false-rejection measurement** on a real Lean toolchain.

Everything here is operator tooling run with a human in the loop. The runtime mining/proving loop must use only self-hosted or licensed models; do not put any external assistant API into an automated earning loop. Keep credentials in environment variables, never in repo files.

## Prerequisites

- `uv` for running the CLI.
- A real Lean toolchain (`elan` + `lake`) for baseline screening and container replay.
- A local checkout (or per-row checkout root) of each source repository you ingest.

## Step 1 - Ingest real tasks

Ingesters never crash on a bad row: each row is either accepted as a candidate or quarantined with a structured reason. Always write a report and read it.

### SorryDB (open `sorry` gaps)

```bash
uv run lemma tasks ingest-sorrydb \
  --sorry-json sorrydb-rows.json \
  --source-checkout-root ~/lemma-checkouts \
  --source-license "Apache-2.0" \
  --mathlib-rev "<pinned-rev>" \
  --lean-toolchain "leanprover/lean4:<pinned>" \
  --reproduction-command "lake build" \
  --run-baseline \
  --output registry-sorrydb.json \
  --report report-sorrydb.json
```

### Formal Conjectures (safely restated)

```bash
uv run lemma tasks ingest-formal-conjectures \
  --records-json formal-conjectures-records.json \
  --source-license "Apache-2.0" \
  --mathlib-rev "<pinned-rev>" \
  --lean-toolchain "leanprover/lean4:<pinned>" \
  --run-baseline \
  --output registry-fc.json \
  --report report-fc.json
```

`--run-baseline` screens out tasks a trivial tactic closes (needs Lean). New Formal Conjectures candidates default to held-out **benchmark** framing; opt into paid work explicitly. See `docs/tasks.md` for the full option list.

### Review quarantine

Open each `report-*.json` and confirm the quarantine reasons look right (malformed rows, no target hole, unsafe category, missing environment, exposes original, and so on). The accepted candidates are the registry's task bundles; the quarantined rows are your backlog of what to fix or skip.

## Step 2 - Pin and certify the environment

Every accepted task must carry a certified environment (`environment_sha256`), exact target hashes, and a reproduction command. The ingesters set these from the pinned toolchain, Mathlib revision, imports, and `lake-manifest.json`. Tasks without a manifest are quarantined as `missing_environment`; fix the checkout and re-ingest rather than relaxing the rule.

Optionally sign the published registry for cache distribution:

```bash
uv run lemma tasks sign-registry --registry registry.json ...
```

## Step 3 - Reach the volume target

Repeat Step 1 across sources until you have 100-300 accepted candidates. Track the count with the readiness check:

```bash
uv run lemma launch-check --registry registry.json --min-tasks 100
```

`packaged_task_volume` turns green once you cross the threshold.

## Step 4 - Container replay and false-rejection rate

This is the criterion `launch-check` reports as `not_measured`. Do it on a real Lean toolchain, in a clean container, before launch.

1. **Replay known-good proofs.** For a held-out set of proofs you know are correct, run them through the verifier in a clean container and confirm they are accepted:

   ```bash
   uv run lemma corpus replay proofs/sn467/accepted/epoch-000001.jsonl
   ```

   Equivalently, miners and validators can confirm parity locally with `lemma preflight` on the same task and submission.

2. **Measure the false-rejection rate.** Count how many known-good proofs are rejected. The launch bar is that this rate is measured and acceptably low (ideally zero). Any rejection of a valid proof should map to an understandable rejection class and be investigated before launch - a valid proof rejected for a stylistic reason is a bug.

3. **Confirm cheats still fail.** Replay submissions that contain a residual `sorry`/`admit`, a new axiom, a changed target type, or a forbidden import, and confirm each is rejected with the matching class (`new_sorry_detected`, `new_admit_detected`, `new_axiom_detected`, `target_type_changed`, `forbidden_import`). The fixture tests cover these; container replay confirms it on the live environment.

## Step 5 - Gate on launch-check

```bash
uv run lemma launch-check --registry registry.json --min-tasks 100
```

`ready: true` means every automatically-checkable criterion passes. Treat the `not_measured` items (container replay) as the manual sign-off from Step 4. See `docs/launch-readiness.md` for the full criteria table.

## Step 6 - Publish and display

```bash
# Build/publish the Proof Atlas (dry run first)
uv run python scripts/publish_proof_atlas_snapshot.py --repo ~/lemma-proof-atlas --netuid sn467 --dry-run

# Render the public board + solved explorer from the published artifacts
uv run lemma site build --atlas ~/lemma-proof-atlas --out ~/lemma-proof-atlas/site
```

Confirm the solved ledger and the rendered board/solved pages match the accepted tasks (the `atlas_site_accurate` criterion checks this on the data; eyeball the rendered pages too).

## Go / No-Go

Launch only when all of the following hold:

- `lemma launch-check` reports `ready: true` at `--min-tasks 100`;
- container replay accepts known-good proofs with a measured, acceptably low false-rejection rate;
- cheat submissions are rejected with the correct classes on the live environment;
- the Proof Atlas snapshot and the rendered site display the solved data accurately;
- the runtime mining/proving loop uses only self-hosted or licensed models, with no external assistant API in the automated earning loop.

# Launch Readiness

Launch readiness proves the validator, scoring, selection, Proof Atlas, and site surfaces hold together and run several local epochs end to end without manual rescue. The check is deterministic and offline: it runs no network, no chain, no live mining, and no model calls.

```bash
uv run lemma launch-check --registry registry.json --min-tasks 100
```

The command loads a pinned task registry, builds the public task bundles, runs a fixed panel of honest, duplicate, invalid, and timeout miners across several local epochs, and prints a JSON report. It exits non-zero when any automatically-checkable criterion fails.

## Criteria

| Criterion | What it checks |
| --- | --- |
| `packaged_task_volume` | At least `--min-tasks` packaged, validated real tasks are available. |
| `reproduction_command_present` | Every task has an exact reproduction command. |
| `environment_certified` | Every task pins a certified validation environment hash. |
| `target_hashes_present` | Every task pins target and target-type hashes. |
| `rejection_reasons_understandable` | Every miner-facing rejection class has a plain-language explanation. |
| `deterministic_active_selection` | Two validators select the same active set from the same public inputs. |
| `epochs_run_end_to_end` | Several local epochs complete and accept proofs. |
| `duplicate_scored_correctly` | Duplicate resubmissions of a winning proof earn no credit. |
| `cheats_never_rewarded` | Invalid, timeout, and duplicate submissions never win a task. |
| `solved_replay_metadata_present` | Every solved proof carries reproduction, target, and artifact hashes. |
| `atlas_site_accurate` | The solved ledger matches the solved tasks exactly. |
| `container_replay` | Reported as `not_measured`: replaying accepted proofs from clean Lean containers needs a pinned toolchain. |

## Reading The Report

- `ready` is `true` when every automatically-checkable criterion passes (`fail_count == 0`).
- Criteria marked `not_measured` do not flip `ready` on their own; they list the manual verification still required before launch. Today that is container replay: run `lemma corpus replay` against accepted rows in a clean container, and measure the false-rejection rate on known-good proofs, before going live.
- Each criterion carries a `metric` object with the underlying counts so a reviewer can see the gap, not just pass/fail.

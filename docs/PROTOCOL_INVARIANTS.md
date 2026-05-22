# Protocol Invariants

These invariants protect Lemma's core promise: published theorem/proof records must be mechanically verified, replayable, licensed, and safe to publish as open mathematical corpus rows.

Lemma launches with Lean formal mathematics as the only paid production domain. Research adapters stay outside production rewards unless they have the same deterministic verifier, replay, license, identity, and corpus guarantees.

Production invariants:

1. No paid row without deterministic Lean verifier acceptance.
2. No paid row without replay metadata.
3. No paid task without source and license metadata.
4. No paid task from an unknown or restricted source license.
5. No paid benchmark task.
6. No full production reward under weak proof identity.
7. No accepted corpus row without task id, task version, target hash, verifier version, registry hash context, and validator attribution.
8. No verifier run with network access in production mode.
9. No validator scoring in production mode without `LEMMA_TASK_SUPPLY_MODE=procedural`.
10. No production procedural supply without a pinned public source pool spanning Mathlib rows and prior accepted Lemma rows.
11. No production paid reward without live miner hotkey authentication.
12. No production paid reward without commit/reveal fields on revealed submissions.
13. No paid production task unless supply is procedural depth-2 and generated from chain/drand epoch randomness.
14. No paid production task without Lean-backed Prop, novelty, typecheck, triviality, and baseline gates.
15. No paid production task without a recomputable public import-graph slot-weight receipt.
16. No paid production task without a recomputable `T(t)` retarget receipt from public burn history.
17. No paid production task without a chain-pinned operator-bundle version, operator-bundle hash, and two drand-keyed mutation steps with params plus input/output statement hashes.
18. No paid production task without a public novelty-cache receipt.
19. No paid production task without a source-pool receipt covering source counts, stream counts, alpha, and cap.
20. No production paid reward under weak proof identity.

`LEMMA_PROTOCOL_MODE=production` fails closed if the active configuration violates the launch boundary: enabled domains must be exactly `lean`; task supply must be procedural; the public source pool must be SHA-pinned and backed by an explicit prior-substrate mirror; paid tasks must be procedural depth-2, generated from chain/drand epoch randomness, stamped with the chain-pinned operator bundle and drand-keyed mutation params, stamped by the `lean` generation gate runner, and carrying source-pool, public novelty-cache, public import-graph slot-weight, and `T(t)` receipts that recompute from public inputs; live miner authentication and commit/reveal fields must be required; strong proof identity must be required for reward; and the Lean verifier network mode must be disabled. Production submissions must arrive through the bucket path with a matching miner chain commitment plus commit/reveal metadata. Registry files can still be published as caches, but validators rebuild the active task set from public inputs.

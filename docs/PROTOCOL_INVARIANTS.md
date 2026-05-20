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
9. No validator scoring in production mode without pinned registry bytes.
10. No production registry without explicit signature verification.
11. No production paid reward without live miner hotkey authentication.
12. No production paid reward without commit/reveal fields on revealed submissions.
13. No paid production task unless supply is procedural depth-2 and chain/drand anchored.
14. No paid production task without Prop, novelty, typecheck, triviality, and baseline gates.
15. No production paid reward under weak proof identity.

`LEMMA_PROTOCOL_MODE=production` fails closed if the active configuration violates the launch boundary: enabled domains must be exactly `lean`, the registry must be SHA-pinned and signature-verified, paid tasks must be procedural depth-2, live miner authentication and commit/reveal fields must be required, strong proof identity must be required for reward, and the Lean verifier network mode must be disabled. Direct file submissions satisfy this by hotkey signature; bucket-path submissions satisfy it by a matching miner chain commitment plus commit/reveal metadata.

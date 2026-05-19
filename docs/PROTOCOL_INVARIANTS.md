# Protocol Invariants

These invariants protect Lemma's core promise: accepted rows must be mechanically verified, replayable, licensed, and safe to use as training data.

Lemma launches with Lean as the only paid production domain. Future domains can reuse the verified reasoning data row contract, but they do not enter production rewards until they have the same deterministic verifier, replay, license, identity, and corpus guarantees.

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

`LEMMA_PROTOCOL_MODE=production` fails closed if the active configuration violates the launch boundary: enabled domains must be exactly `lean`, the registry must be SHA-pinned and signature-verified, and the Lean verifier network mode must be disabled.

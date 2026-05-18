# Protocol Invariants

Lemma v1 launches as a Lean proof-data subnet. The graph is the substrate shape, but the paid production path remains Lean-only until the Lean wedge is reliable.

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

Future domains can reuse the graph-shaped row contract, but they do not enter production rewards until they have the same deterministic verifier, replay, license, identity, and corpus guarantees.

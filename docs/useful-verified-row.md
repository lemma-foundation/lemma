# Useful Verified Row

A valid Lean proof row is not automatically a useful proof record.

A useful verified row passes these gates:

- validity: the pinned Lean verifier accepted the proof;
- replayability: the row carries enough metadata to rerun the verifier;
- nontriviality: baseline tactics were checked and failed;
- novelty: the row is not an exact or near duplicate;
- license: the source license is clean for public Proof Atlas publication;
- identity: proof identity is strong enough for full production reward.

Rows can be valid but not useful. Proof Atlas exports can filter with `--useful-only`, and production reward policy can require `full_reward_eligible`.

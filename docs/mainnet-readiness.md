# Mainnet Readiness

Mainnet readiness is a gate sequence, not one command. A launch candidate must pass the local proof spine, then SN467 production-mode smoke, then closed burn-in, then public burn-in, then final mainnet cutover.

## Local Gate

Run the full local gate before any live host work:

```bash
uv run python scripts/workstream_audit.py --profile mainnet --skip-site
```

The `mainnet` profile runs formatting, typing, security scans, dependency audit, privacy leak check, the full non-Docker test suite, and the Docker Lean golden test with `RUN_DOCKER_LEAN=1`.

The local production-like smoke is covered by `tests/test_operator_registry_flow.py`. It proves:

- procedural depth-2 supply can build a production-shaped registry cache;
- validators rebuild the active task set from a pinned public source pool plus chain/drand epoch randomness;
- production preflight passes only with procedural supply mode, source-pool SHA pinning, procedural depth-2 supply, chain/drand epoch randomness, live miner authentication, commit/reveal fields, strong proof identity, and disabled Lean networking;
- a signed revealed submission can be verified, scored, written to corpus, and exported without setting weights;
- the rewarded corpus row carries strong structural proof identity.

## SN467 Gate

Use SN467 as the live proving ground for the production protocol. Testnet changes the chain target, not Lemma's protocol behavior:

```bash
export BT_NETWORK=test
export BT_NETUID=467
export LEMMA_PROTOCOL_MODE=production
```

Run the same artifact set locally first, then copy it into a clean validator/miner directory on the testnet hosts. The first live pass must use `--no-set-weights`. The controlled chain-write pass must set `LEMMA_ENABLE_SET_WEIGHTS=1` and use `--set-weights`. The mainnet cutover should only change `BT_NETWORK` and `BT_NETUID`.

Accept the gate only when `weight-submissions.jsonl` has resolved UIDs, `success=true`, an extrinsic hash, and the validator never reports `weights_set=true` without a confirmed successful response.

## Burn-In Gates

Closed burn-in is at least 72 continuous testnet hours with controlled miners. Public burn-in is at least 7 days with procedural depth-2 supply and active `K` filled.

For both burn-ins:

- paid task supply is procedural, fresh, depth-2, validator-rebuildable from a SHA-pinned public source pool, and generated from chain/drand epoch randomness;
- miner submissions are bucket reveals authenticated by miner chain commitments;
- miner bucket reveals fail closed unless their `(slot_index, ciphertext_sha256)` Merkle root matches the miner's on-chain committed root and drand decryption matches the revealed proof;
- Lean verification runs with networking disabled;
- paid rewards require strong proof identity;
- every accepted public corpus row replays from clean artifacts;
- diagnostics expose registry hash, active `K`, frontier depth, verifier health, disk/cache pressure, accepted unique count, corpus rows, weight receipts, set-weight latency, and storage-root readback without private operator state.

## Mainnet Cutover

Rebuild the launch registry cache only from procedural depth-2 candidates:

```bash
uv run lemma tasks rebuild-procedural-registry \
  --mathlib-snapshot snapshot.jsonl \
  --generation-seed "$EPOCH_SEED" \
  --epoch-randomness "$EPOCH_RANDOMNESS_JSON" \
  --tempo "$TEMPO" \
  --count "$K" \
  --prior-corpus-dir corpus \
  --citation-alpha 0.25 \
  --citation-weight-cap 100 \
  --triviality-retarget-jsonl public-settlements.jsonl \
  --output tasks/mainnet.registry.json
```

Cut scale, not shape: reduce `K`, source samples, and the operator bundle if needed, but keep depth-2 generation, Lean-backed novelty/typecheck/Prop/triviality gates, recomputable slot-weight receipts, burn-rate-retargeted `T(t)`, miner hotkey authentication, and strong proof identity. The registry file is a cache; validators rebuild from pinned source rows plus chain/drand. Tempo remains 72 minutes / 360 blocks until subnet tempo customization exists.

On the launch host, production preflight must be green before accepting submissions:

```bash
LEMMA_PROTOCOL_MODE=production \
LEMMA_TASK_SUPPLY_MODE=procedural \
LEMMA_PROCEDURAL_SOURCE_JSONL=snapshot.jsonl \
LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR=corpus \
LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED=<source-pool-sha256> \
LEMMA_PROCEDURAL_CITATION_ALPHA=0.25 \
LEMMA_PROCEDURAL_GATE_TIMEOUT_S=120 \
LEMMA_PROCEDURAL_TRIVIALITY_BUDGET_S=120 \
LEMMA_PROCEDURAL_CITATION_WEIGHT_CAP=100 \
LEMMA_REQUIRE_SUBMISSION_SIGNATURES=1 \
LEMMA_REQUIRE_COMMIT_REVEAL=1 \
LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1 \
LEMMA_ACTIVE_TEMPO_SOURCE=chain \
LEMMA_ACTIVE_SEED_MODE=epoch_randomness \
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand \
LEAN_SANDBOX_NETWORK=none \
uv run lemma operator preflight
```

Run one corpus-only production smoke with `--no-set-weights`, then one controlled live weight submission and confirmed readback. Publish the corpus snapshot, verify mirrors, submit the storage-root commitment, read it back, and refresh the public site.

Do not commit or publish local notes, env files, wallets, logs, caches, hostnames, IPs, machine paths, or operator context.

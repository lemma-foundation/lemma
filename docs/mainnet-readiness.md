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
- production preflight passes only with procedural supply mode, source-pool SHA pinning, pre-proof public import-graph slot estimates, procedural depth-2 supply, chain/drand epoch randomness, live miner authentication, commit/reveal fields, strong proof identity, and disabled Lean networking;
- a signed revealed submission can be verified, scored, written to corpus, and exported without setting weights;
- the rewarded corpus row carries strong Lean proof-term identity.

## SN467 Gate

Use SN467 as the live proving ground for the production protocol. Testnet changes the chain target, not Lemma's protocol behavior:

```bash
export BT_NETWORK=test
export BT_NETUID=467
export LEMMA_PROTOCOL_MODE=production
```

Run the same artifact set locally first, then copy it into a clean validator/miner directory on the testnet hosts. The first live pass must use `--no-set-weights`. The controlled chain-write pass must set `LEMMA_ENABLE_SET_WEIGHTS=1` and use `--set-weights`. The mainnet cutover should only change `BT_NETWORK` and `BT_NETUID`.

Accept the gate only when `weight-submissions.jsonl` has resolved UIDs, `success=true`, an extrinsic hash, and the validator never reports `weights_set=true` without a confirmed successful response.

Also run a second-validator parity pass from a clean operator data directory. Both validators must rebuild the same active task set from public inputs and accept the same bucket reveal with matching task, proof, score, and weight-shape outputs. Validator-local row IDs and validator hotkeys may differ.

## Live Evidence Checklist

For each live tempo, keep protocol evidence separate from operator strategy:

- active registry prebuild runs before miners need the tempo cache;
- the miner publishes at most one bucket reveal for the chain tempo;
- the validator reveal inbox contains the expected tempo reveal before the validation pass;
- the validator pass reports `bucket_reveals_consumed > 0`, `verified_count > 0`, `accepted_unique_count > 0`, `corpus_row_count > 0`, and a non-empty tempo commitment payload;
- `verification-records.jsonl`, `score-events.jsonl`, and the canonical tempo directory contain the accepted task id, proof hash, solver hotkey, validator hotkey, and dependency metadata;
- the reveal inbox is empty or archived after a successful pass;
- the corpus publisher, storage commitment, and post-publish checker succeed on the next publish cycle;
- no live check depends on local notes, private paths, hostnames, IPs, wallet files, logs, or env files being published.

Miner implementation is intentionally open-ended. The protocol does not require a particular proof-search loop, model provider, agent framework, or scheduler. A Codex-orchestrated miner, a custom Lean search engine, a model API wrapper, a manual prover, or a direct non-Python client are all operator strategies as long as they produce valid task-bound proofs and publish authenticated bucket commitments.

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
- miner and validator automation treats timers as wakeups only; active tempo must come from `block // subnet_tempo`, and a miner should publish at most one bucket reveal per chain tempo.

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
  --citation-alpha 0.5 \
  --citation-weight-cap 64 \
  --citation-window-tempos 2000 \
  --triviality-retarget-jsonl public-settlements.jsonl \
  --novelty-cache-jsonl public-entry-cache.jsonl \
  --import-graph-jsonl public-import-graph.jsonl \
  --output tasks/mainnet.registry.json
```

Cut scale, not shape: reduce `K`, source samples, and enabled operator families if needed, but keep the chain-pinned Lean AST/elaborator mutation bundle, depth-2 generation, drand-keyed mutation params, public novelty-cache receipts, Lean-backed kernel-canonical novelty/typecheck/Prop/triviality gates, verifier-recorded kernel dependency slot weights, burn-rate-retargeted `T(t)`, miner hotkey authentication, and strong proof identity. The registry file is a cache; validators rebuild from pinned source rows plus chain/drand. Tempo is the chain tempo: SN467 currently uses 360-block epochs. Wall-clock timers are only approximate wakeups.

On the launch host, production preflight must be green before accepting submissions:

```bash
LEMMA_PROTOCOL_MODE=production \
LEMMA_TASK_SUPPLY_MODE=procedural \
LEMMA_PROCEDURAL_SOURCE_JSONL=snapshot.jsonl \
LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR=corpus \
LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED=<source-pool-sha256> \
LEMMA_PROCEDURAL_CITATION_ALPHA=0.5 \
LEMMA_PROCEDURAL_CITATION_WINDOW_TEMPOS=2000 \
LEMMA_PROCEDURAL_GATE_TIMEOUT_S=120 \
LEMMA_PROCEDURAL_TRIVIALITY_BUDGET_S=120 \
LEMMA_PROCEDURAL_NOVELTY_CACHE_JSONL=public-entry-cache.jsonl \
LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL=public-import-graph.jsonl \
LEMMA_PROCEDURAL_CITATION_WEIGHT_CAP=64 \
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

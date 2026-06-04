# Mainnet Readiness

Mainnet readiness means the registry-backed proof competition runs without operator nudges and every public artifact can be replayed from pinned inputs.

SN467 testnet burn-in must use the same production protocol as mainnet with only chain target settings changed:

```bash
BT_NETWORK=test
BT_NETUID=467
LEMMA_PROTOCOL_MODE=production
LEMMA_TASK_SUPPLY_MODE=registry
```

## Launch Gates

Do not call the subnet production-ready until all gates pass:

- a nonempty real-task registry is published, signed when expected, and pinned by `LEMMA_TASK_REGISTRY_SHA256_EXPECTED`;
- paid tasks pass the real-source supply checks in `lemma.protocol_invariants`;
- active selection derives from chain/drand epoch randomness, not wall-clock randomness;
- miner bucket delivery, validator reveal intake, Lean verification, weight write, and commitment write all complete for natural chain tempos;
- `weight-submissions.jsonl` contains `success=true` rows for real validator submissions when weight writing is enabled;
- accepted proof rows and canonical tempo artifacts are written and can be mirrored through Proof Atlas;
- at least one clean validator rebuild reproduces the active window and Lean verification from public inputs;
- closed burn-in runs at least 72 continuous hours, followed by 7 public burn-in days before mainnet;
- private operator state, environment files, logs, hostnames, IPs, wallets, and machine paths are absent from public commits and snapshots.

## Audit Commands

Run the mainnet audit profile before launch and after each production-shaped rollout:

```bash
uv run python scripts/workstream_audit.py --profile mainnet --skip-site
```

Run the Lean-backed Docker checks when touching verifier, validator, task, or scoring behavior:

```bash
RUN_DOCKER_LEAN=1 uv run pytest tests/test_docker_golden.py -v --tb=short
```

Run the standard local checks:

```bash
uv run ruff check lemma scripts tests
uv run pytest -q
```

## Production Environment

```bash
BT_NETWORK=test
BT_NETUID=467
LEMMA_PROTOCOL_MODE=production
LEMMA_TASK_SUPPLY_MODE=registry
LEMMA_TASK_REGISTRY_URL=tasks/signed.registry.json
LEMMA_TASK_REGISTRY_SHA256_EXPECTED=<registry-sha256>
LEMMA_VERIFY_REGISTRY_SIGNATURES=1
LEMMA_REQUIRE_SUBMISSION_SIGNATURES=1
LEMMA_REQUIRE_COMMIT_REVEAL=1
LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1
LEMMA_ACTIVE_TEMPO_SOURCE=chain
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand
LEMMA_ACTIVE_REGISTRY_CACHE_DIR=active-registries
LEMMA_ACTIVE_K=20
LEMMA_FRONTIER_DEPTH=0
LEMMA_CURRICULUM_RETARGET=1
LEMMA_CURRICULUM_STATE_JSONL=validator-data/curriculum.jsonl
LEMMA_CURRICULUM_STATE_PUBLIC=1
LEMMA_VALIDATOR_CAPACITY=20
LEMMA_CANONICAL_OUTPUT_DIR=canonical
LEAN_SANDBOX_NETWORK=none
```

Run preflight with those settings:

```bash
uv run lemma operator preflight
```

The preflight must report registry SHA pinning, real-task supply, production epoch randomness, live miner authentication, commit/reveal, strong proof identity, and disabled Lean networking as passing.

## Natural Tempo Evidence

For each burn-in window, capture public-safe evidence:

- active tempo and registry SHA;
- active registry cache path and hydration status;
- miner bucket object count;
- validator reveal result;
- Lean verification count;
- accepted proof count;
- `validator-runs.jsonl` row;
- `weight-submissions.jsonl` row with `success=true` when weights are enabled;
- canonical active-pool, accepted-entry, curriculum, and commitment digests;
- Proof Atlas publisher dry-run output.

Do not commit or publish local notes, `.env` files, wallet files, service-unit secrets, raw logs, deployment hostnames, IP addresses, or machine-specific paths.

## Cutover Rule

Mainnet cutover should change `BT_NETWORK` and `BT_NETUID`, not the protocol shape. If SN467 only works because of manual cache edits, private task files, disabled commit/reveal, host Lean, unverifiable source rows, or skipped leak checks, it is not ready.

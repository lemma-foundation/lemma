# Production

Production Lemma's launch path is the Lean proof competition loop:

1. load a SHA-pinned real-task registry;
2. derive the active task window from chain/drand epoch randomness;
3. read miner bucket reveals after commitment/reveal;
4. verify each proof with the pinned Lean environment;
5. score rank-0 unique proofs by miner commit block;
6. compute miner weights from deterministic active slot weights and burn unearned share by default;
7. write accepted proof records and publish configured Proof Atlas artifacts.

Production mode is stricter than local smoke mode. SN467 testnet burn-in must run the same production protocol with `BT_NETWORK=test` and `BT_NETUID=467`; mainnet cutover should only change the chain target. The launch path fails closed unless the task registry is pinned, production tasks are real missing-proof rows, live miner submissions are hotkey-authenticated, commit/reveal fields are present, Lean verifier networking is disabled, and paid rewards require strong Lean-derived proof identity.

The launch gate sequence is tracked in [Mainnet Readiness](mainnet-readiness.md).

## Operator Rules

- Do not route subnet owner emissions through a contract.
- Do not use escrow-style reward custody for Lemma rewards.
- Do not score prose, model branding, or claimed effort.
- Keep task, submission, verifier, scoring, and Proof Atlas artifacts replayable.
- Delay public proof release until the scoring window closes.
- Keep `.env`, wallets, local state, logs, caches, and machine paths out of commits.

## Production Readiness Gates

The chain is the authority for epoch correctness. Timers are only wakeups: each registry-cache warmer, miner, and validator pass must recompute the active tempo from chain state, then exit idempotently when there is nothing new to do.

Generic validators need the protocol path:

1. load the pinned registry;
2. consume live bucket reveals;
3. verify submitted proofs with the pinned Lean environment;
4. write local verification, score, run, accepted-proof, and canonical tempo artifacts;
5. set weights from accepted Lean proofs.

Snapshot publishing is a separate deployment setup. Validators can run the same publishing tools for their own mirrors if they configure storage and credentials, but validation itself only requires the protocol path above.

Before calling SN467 production-ready, prove:

- one full natural tempo with no manual starts: registry cache hydration, miner bucket delivery, validator intake, Lean verification, weight write, and commitment readback;
- at least 72 continuous closed burn-in hours with repeated natural tempos and no operator nudges, followed by 7 days of public burn-in before mainnet;
- at least one second validator or clean rebuild that reproduces active-window and Lean-verification behavior from public inputs;
- a short runbook for active tempo, registry cache, miner marker, reveal queue, validator result, Proof Atlas artifacts, and chain readback.

## Commands

```bash
uv run lemma status
uv run lemma operator preflight
uv run lemma validate --once --bucket-reveals-jsonl bucket-reveals.jsonl --no-set-weights
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Production launch settings:

```bash
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
LEMMA_CURRICULUM_RETARGET=1
LEMMA_CURRICULUM_STATE_JSONL=curriculum-state.jsonl
LEMMA_CURRICULUM_STATE_PUBLIC=1
LEMMA_VALIDATOR_CAPACITY=20
LEMMA_CURRICULUM_COST_BUDGET_S=2700
LEMMA_CURRICULUM_BASE_TASK_COST_S=180
LEMMA_CURRICULUM_DEPTH_COST_MULTIPLIER=2
LEMMA_CANONICAL_OUTPUT_DIR=canonical
LEAN_SANDBOX_NETWORK=none
```

`lemma operator preflight` checks the pinned registry, active-window shape, real-task production supply, signature status, epoch-randomness settings, live submission authentication, commit/reveal, strong proof identity, and Lean sandbox network mode.

## Publishing

Accepted proofs and active/canonical artifacts can be mirrored through Proof Atlas:

```bash
uv run python scripts/publish_proof_atlas_snapshot.py \
  --repo "$LEMMA_PROOF_ATLAS_REPO" \
  --netuid "sn${BT_NETUID}" \
  --sync-proof-dir "$LEMMA_CORPUS_OUTPUT_DIR" \
  --sync-canonical-dir "$LEMMA_CANONICAL_OUTPUT_DIR/sn${BT_NETUID}" \
  --sync-registry-cache-dir "$LEMMA_ACTIVE_REGISTRY_CACHE_DIR" \
  --dry-run
```

Run the leak check before committing or pushing any Proof Atlas snapshot.

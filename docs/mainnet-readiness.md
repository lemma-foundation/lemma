# Mainnet Readiness

Mainnet readiness is a gate sequence, not one command. A launch candidate must pass the local proof spine, then SN467 production-mode smoke, then closed burn-in, then public burn-in, then final mainnet cutover.

## Local Gate

Run the full local gate before any live host work:

```bash
uv run python scripts/workstream_audit.py --profile mainnet --skip-site
```

If this shell cannot resolve `pypi.org`, the checklist now auto-adds `--skip-pip-audit`:

```bash
uv run python scripts/workstream_audit.py --profile mainnet --skip-site --skip-pip-audit
```

The `mainnet` profile runs formatting, typing, security scans, dependency audit, privacy leak check, the full non-Docker test suite, and the Docker Lean golden test with `RUN_DOCKER_LEAN=1`.

For a machine-checkable readiness audit that complements manual service checks:

```bash
uv run python scripts/pre_mainnet_checklist.py
uv run python scripts/pre_mainnet_checklist.py --json
```

### Checklist coverage

The local readiness script currently covers:

- 2) single validator path checks, including stale `LEMMA_ACTIVE_REGISTRY_ROLE` rejection
- 5) active-registry cache behavior
- 6) commitment publish-path hardening and publish-gate coupling
- 8) validator tempo-work volume indicators (`bucket_reveals_consumed`, `verified_count`, `accepted_unique_count`, `corpus_row_count`)
- 9) chain-weight evidence (`weights_set`, `weight-submissions.jsonl` receipt `success`, `uids`, `weights`, and `extrinsic_hash`; warn on non-empty oscillation checks per tempo)
- 10) storage commitment evidence (`chain_commitment_set`, validator-run `tempo_commitment_payload`, `commitment-submissions.jsonl` receipt `success`, `payload`, `extrinsic_hash`, and readback parity fields)
- 11) checkpoint parity requirement (`LEMMA_CHAIN_COMMITMENT_CHECKPOINT_DIR`) and missing-prod guardrails
- 12) production auth/proof gates
- 14) privacy leak gate execution (`scripts/leak_check.py`)
- 15) burn-in guardrails (time-range continuity and zero-progress windows)
- 6b) commitment publication gate: `chain_commitment_set` requires canonical-publish record presence; fail when chain commitment succeeds without publish records
- 6c) canonical publish staging: verify recent `canonical-publish.jsonl` has no publish errors
- 7) tempo pipeline integrity evidence: `mainnet_readiness_evidence` now adds a digest check for the latest tempo directory against run-row digests when local canonical output is available
- 5b) hardening bundle file presence for cache/publish runtime paths (`scripts/lemma-sync-active-registry-cache`, `lemma/cli/main.py`, `scripts/publish_proof_atlas_snapshot.py`)
- 2, 6, 7, 8, 9, 10, 11, 12, 13, and 16 include local checks but should still be reviewed against live tempo evidence from production hosts.
- 5 and the 14/15 continuity windows remain partly runbook-driven host rollout tasks.
- 1, 3, and 4 now include executable evidence steps in `scripts/mainnet_readiness_evidence.py` (rollout parity and validator service/data-directory checks) but are still host-level and should be confirmed on each runtime host.

### Manual evidence mapping for runbook-only items

For the remaining manual items, run the evidence collector once per evidence-gate cadence:

```bash
uv run python scripts/mainnet_readiness_evidence.py --help
uv run python scripts/mainnet_readiness_evidence.py --output launch-readiness.json --execute
```

That script snapshots evidence for:
- 1: deploy parity
- 2: role semantics and default behavior
- 3: second-validator role introduction
- 4: clean second-validator state
- 5: hardening bundle and cache-builder parity artifacts
- 6: publish-gate failure mode validation
- 7: publish/replay cadence
- 8: tempo work production signals
- 9: weight submission proof-of-write evidence
- 10: commitment submission proof-of-write evidence
- 11: commitment checkpoint parity/readback resilience
- 12: observability alerting and stale-state signals
- 13: runbook artifact trail
- 14: privacy leak scan gate status
- 15: burn-in evidence and zero-progress checks
- 16: final-package review

If execution is not allowed on the host, run with `--output` only first to capture the exact commands and then run them manually in the target environment.

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
export LEMMA_CHAIN_COMMITMENT_CHECKPOINT_DIR="/var/lib/lemma-chain-commitments"
```

Run the same artifact set locally first, then copy it into a clean validator/miner directory on the testnet hosts. The first live pass must use `--no-set-weights`. The controlled chain-write pass must set `LEMMA_ENABLE_SET_WEIGHTS=1` and use `--set-weights`. The mainnet cutover should only change `BT_NETWORK` and `BT_NETUID`.

For the second-validator parity requirement, both validators should share a checkpoint
root that survives restart and follows the same active tempo replay order. The
operator should verify each node can read back historical commitments after restart:

```bash
export LEMMA_HISTORY_BLOCK="<some recently completed block>"
uv run python - <<'PY'
import os
from lemma.chain.commitments import read_all_commitments
from lemma.common.config import LemmaSettings

settings = LemmaSettings()
block = int(os.environ["LEMMA_HISTORY_BLOCK"])
commitments = read_all_commitments(settings, block=block)
print(f"historical commitments at block {block}: {len(commitments)} miners")
PY
```

Accept the gate only when `weight-submissions.jsonl` has resolved UIDs, `success=true`, an extrinsic hash, and the validator never reports `weights_set=true` without a confirmed successful response.

Also run a second-validator parity pass from a clean operator data directory. Both validators must rebuild the same active task set from public inputs and accept the same bucket reveal with matching task, proof, score, and weight-shape outputs. Validator-local row IDs and validator hotkeys may differ.

## Live Evidence Checklist

For each live tempo, keep protocol evidence separate from operator strategy:

- chain state, not wall-clock time, defines the active tempo;
- active registry cache warming runs after the tempo randomness is live and before miners need the cache;
- the miner publishes at most one bucket reveal for the chain tempo;
- the validator reveal inbox contains the expected tempo reveal before the validation pass;
- the validator pass reports `bucket_reveals_consumed > 0`, `verified_count > 0`, `accepted_unique_count > 0`, `corpus_row_count > 0`, and a non-empty tempo commitment payload;
- `verification-records.jsonl`, `score-events.jsonl`, and the canonical tempo directory contain the accepted task id, proof hash, solver hotkey, validator hotkey, and dependency metadata;
- the reveal inbox is empty or archived after a successful pass;
- the Proof Atlas publisher, storage commitment, and post-publish checker succeed on the next publish cycle;
- no live check depends on local notes, private paths, hostnames, IPs, wallet files, logs, or env files being published.

Miner implementation is intentionally open-ended. The protocol does not require a particular proof-search loop, model provider, agent framework, or scheduler. A Codex-orchestrated miner, a custom Lean search engine, a model API wrapper, a manual prover, or a direct non-Python client are all operator strategies as long as they produce valid task-bound proofs and publish authenticated bucket commitments.

Use the configured operator environment to read the chain-derived tempo:

```bash
uv run python - <<'PY'
from typing import Any, cast

import bittensor as bt

from lemma.common.config import LemmaSettings

settings = LemmaSettings()
subtensor = bt.Subtensor(network=settings.bt_network or None)
block = int(subtensor.get_current_block())
tempo = int(cast(Any, subtensor.get_subnet_hyperparameters(settings.netuid, block=block)).tempo)
print(
    {
        "netuid": settings.netuid,
        "network": settings.bt_network,
        "block": block,
        "subnet_tempo": tempo,
        "active_tempo": block // tempo,
        "blocks_into_tempo": block % tempo,
        "blocks_remaining": tempo - (block % tempo),
    }
)
PY
```

Systemd timers, cron jobs, and local reminders are only wakeups. On every wakeup, the operator should read the chain block and derive the active tempo before deciding whether to warm the current registry cache, mine, validate, publish, or wait.
For burn-in, prefer short wakeups such as 5-10 minutes plus once-per-tempo miner state over epoch-length timers. Epoch-length timers can drift into a validator-before-miner ordering and delay reveal consumption until the next epoch-sized wakeup.

For SN467 burn-in, keep service timeouts long enough for slow Lean gates. The
active-registry warmer must wait until the previous cold build has finished;
miner and validator wakeups can stay tighter because they are cheap retries.

```ini
# lemma-active-registry-prebuild.service.d/10-local-lean-worker.conf
[Service]
Environment=LEMMA_LEAN_VERIFY_REMOTE_URL=http://localhost:8787
Environment=LEMMA_LEAN_VERIFY_WORKSPACE_CACHE_DIR=/var/lib/lemma-lean-cache

# lemma-active-registry-prebuild.service.d/20-timeout.conf
[Service]
TimeoutStartSec=4500

# lemma-active-registry-prebuild.timer.d/10-cadence.conf
[Timer]
OnBootSec=
OnActiveSec=
OnUnitActiveSec=
OnUnitInactiveSec=
AccuracySec=
RandomizedDelaySec=
OnBootSec=5min
OnUnitInactiveSec=10min
AccuracySec=1min
RandomizedDelaySec=30s

# lemma-miner-bucket@miner5.timer.d/10-cadence.conf
[Timer]
OnActiveSec=
OnUnitInactiveSec=
AccuracySec=
RandomizedDelaySec=
OnActiveSec=2min
OnUnitInactiveSec=2min
AccuracySec=15s
RandomizedDelaySec=15s

# lemma-validator-bucket.timer.d/10-cadence.conf
[Timer]
OnActiveSec=
OnUnitInactiveSec=
AccuracySec=
RandomizedDelaySec=
OnActiveSec=90s
OnUnitInactiveSec=2min
AccuracySec=15s
RandomizedDelaySec=15s
```

Public-safe live checks should be runnable from the configured operator environment without publishing env files, wallet names, hostnames, IPs, or local machine paths. Use the chain-derived `active_tempo` from the command above as `TEMPO`.

```bash
uv run lemma operator registry-inspect
uv run lemma operator diagnostics --output operator-diagnostics.json
uv run lemma operator alerts --recent-runs 8 --recent-failures 3

uv run python - <<'PY'
import os
from pathlib import Path

from lemma.tasks import load_task_registry

tempo = int(os.getenv("TEMPO", ""))
path = Path(os.getenv("LEMMA_ACTIVE_REGISTRY_CACHE_DIR", "")) / f"tempo-{tempo}.registry.json"
registry = load_task_registry(path.read_bytes())
print({"tempo": tempo, "registry_sha256": registry.sha256, "tasks": len(registry.tasks)})
PY

test -f "${LEMMA_OPERATOR_DATA_DIR:?}/last_bucket_tempo" &&
  printf 'last_bucket_tempo=%s\n' "$(cat "${LEMMA_OPERATOR_DATA_DIR}/last_bucket_tempo")"

find "${LEMMA_BUCKET_REVEALS_DIR:?}" -maxdepth 2 -type f -name '*.json*' | sort

uv run python - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.getenv("LEMMA_OPERATOR_DATA_DIR", "")) / "validator-runs.jsonl"
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
latest = rows[-1]
keys = (
    "active_tempo",
    "registry_sha256",
    "verified_count",
    "accepted_unique_count",
    "corpus_row_count",
    "score_event_count",
    "weights_set",
    "chain_commitment_set",
    "tempo_commitment_payload",
)
print({key: latest.get(key) for key in keys})
PY

uv run python scripts/publish_proof_atlas_snapshot.py \
  --repo "$LEMMA_PROOF_ATLAS_REPO" \
  --netuid "sn${BT_NETUID}" \
  --sync-proof-dir "$LEMMA_CORPUS_OUTPUT_DIR" \
  --sync-canonical-dir "$LEMMA_CANONICAL_OUTPUT_DIR/sn${BT_NETUID}" \
  --sync-registry-cache-dir "$LEMMA_ACTIVE_REGISTRY_CACHE_DIR" \
  --dry-run
uv run python scripts/publish_chain_commitment.py \
  --repo "$LEMMA_PROOF_ATLAS_REPO" \
  --netuid "sn${BT_NETUID}" \
  --bt-netuid "$BT_NETUID" \
  --readback \
  --hotkey "$VALIDATOR_HOTKEY"
```

## Burn-In Gates

Closed burn-in is at least 72 continuous testnet hours with controlled miners. Public burn-in is at least 7 days with procedural depth-2 supply and active `K` filled.

For both burn-ins:

- paid task supply is procedural, fresh, depth-2, validator-rebuildable from a SHA-pinned public source pool, and generated from chain/drand epoch randomness;
- active `K` and frontier depth are retargeted from the latest eligible public curriculum row after one full tempo of replay lag, with `K` capped by the public validator-cost budget;
- miner submissions are bucket reveals authenticated by miner chain commitments;
- miner bucket reveals fail closed unless their `(slot_index, ciphertext_sha256)` Merkle root matches the miner's on-chain committed root and drand decryption matches the revealed proof;
- Lean verification runs with networking disabled;
- paid rewards require strong proof identity;
- every accepted public proof row replays from clean artifacts;
- diagnostics expose registry hash, active `K`, frontier depth, verifier health, disk/cache pressure, accepted unique count, accepted proof rows, weight receipts, set-weight latency, and storage-root readback without private operator state;
- alerts detect zero-reveal/zero-accepted windows, cache divergence, repeated publisher/chain-write failures, and stale operator state.
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

Cut scale, not shape: reduce `K` caps, frontier-depth bounds, source samples, and enabled operator families if needed, but keep the chain-pinned mutation bundle, depth-2 generation, drand-keyed mutation params, public novelty-cache receipts, Lean-backed kernel-canonical novelty/typecheck/Prop/triviality gates, verifier-recorded kernel dependency slot weights, burn-rate-retargeted `T(t)`, public curriculum retarget state, miner hotkey authentication, and strong proof identity. The registry file is a cache; validators rebuild from pinned source rows plus chain/drand and the latest prior public curriculum state. Tempo is the chain tempo: SN467 currently uses 360-block epochs. Wall-clock timers are only approximate wakeups.

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

Run one proof-export-only production smoke with `--no-set-weights`, then one controlled live weight submission and confirmed readback. Publish the Proof Atlas snapshot, verify mirrors, submit the storage-root commitment, read it back, and refresh the public site.

Do not commit or publish local notes, env files, wallets, logs, caches, hostnames, IPs, machine paths, or operator context.

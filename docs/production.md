# Production

Production Lemma is the Lean proof corpus loop:

1. publish an active task registry;
2. read miner bucket reveals after commitment/reveal;
3. verify each proof with the pinned Lean environment;
4. score rank-0 unique proofs by miner commit block;
5. compute miner weights from deterministic active slot weights and burn unearned share by default;
6. publish accepted corpus rows and a small corpus index.

Production mode is stricter than local smoke mode. SN467 testnet burn-in must run this same production mode with `BT_NETWORK=test` and `BT_NETUID=467`; mainnet cutover should only change the chain target. Production mode fails closed unless the task registry is SHA-pinned and signature-verified, paid tasks are procedural depth-2 supply generated from chain/drand epoch randomness, live miner submissions are hotkey-authenticated, commit/reveal fields are present, Lean verifier networking is disabled, and paid rewards require strong Lean-derived proof identity.
The launch gate sequence is tracked in [Mainnet Readiness](mainnet-readiness.md).

## Operator Rules

- Do not route subnet owner emissions through a contract.
- Do not use escrow-style reward custody for Lemma rewards.
- Do not score prose, model branding, or claimed effort.
- Keep task, submission, verifier, scoring, and corpus artifacts replayable.
- Delay public proof release until the scoring window closes.
- Keep `.env`, wallets, local state, logs, caches, and machine paths out of commits.

## Commands

```bash
uv run lemma status
uv run lemma worker --check
uv run lemma operator preflight
uv run lemma worker --serve --host localhost --port 8787
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Production launch settings:

```bash
LEMMA_PROTOCOL_MODE=production
LEMMA_VERIFY_REGISTRY_SIGNATURES=1
LEMMA_REQUIRE_SUBMISSION_SIGNATURES=1
LEMMA_REQUIRE_COMMIT_REVEAL=1
LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1
LEMMA_ACTIVE_TEMPO_SOURCE=chain
LEMMA_ACTIVE_SEED_MODE=epoch_randomness
LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand
LEAN_SANDBOX_NETWORK=none
```

Build and sign a launch registry from deterministic procedural depth-2 candidates:

```bash
uv run lemma tasks build-procedural-registry \
  --candidate-jsonl procedural-depth2-candidates.jsonl \
  --output tasks/mainnet.registry.json
uv run lemma tasks sign-registry \
  --input tasks/mainnet.registry.json \
  --output tasks/mainnet.signed.registry.json
```

Corpus deltas are written under `LEMMA_CORPUS_OUTPUT_DIR`. Local receipts are written under `LEMMA_OPERATOR_DATA_DIR`. If `LEMMA_SUBMISSION_SPOOL_DIR` is set, validators consume pending `.json` or `.jsonl` submission files from that directory and move them to `processed/` after a successful pass. These paths should remain ignored unless an operator intentionally publishes sanitized artifacts.
The file spool remains a local/operator-smoke path. The production adapter is `--bucket-reveals-jsonl`: each reveal row carries miner hotkey, tempo, drand round, drand signature, commit block, committed Merkle root, and revealed bucket blobs. Binary ciphertexts should be encoded as `base64:<payload>` or `0x<hex>`. The validator recomputes the Merkle root, confirms the miner's on-chain bucket commitment in production, decrypts bucket ciphertexts in production, requires the decrypted proof to match the reveal, and ranks winners by commit block.
Live chain writes require both `LEMMA_ENABLE_SET_WEIGHTS=1` and `--set-weights`; keep production smoke and corpus-only passes on `--no-set-weights`. On commit-reveal subnets, the chain writer waits until the final 10 blocks of the tempo before submitting. Each attempted live write appends a public-safe `weight-submissions.jsonl` receipt with the resolved UID vector, client result, and extrinsic hash when available.

For the full registry-to-validator-to-export sequence, see [Operator Registry Flow](operator-registry-flow.md).

Publish the current public corpus snapshot after a closed SN467 production-mode pass:

```bash
uv run python scripts/publish_corpus_snapshot.py --repo ~/lemma-corpus --netuid sn467
```

This regenerates the public index/export, builds deterministic accepted-entry directories under `canonical/sn467/`, writes `MANIFEST.sha256`, uploads a timestamped Hippius snapshot, creates the GitHub immutable release mirror, and syncs an append-only Hugging Face dataset snapshot. Hippius, GitHub, and Hugging Face credentials must stay in the operator environment, never in repo files.

Refresh the public website's active-problem dashboard from the validator host:

```bash
uv run python scripts/refresh_site_current_problems.py --site-repo /opt/lemmasub.net --commit --push
```

The script only writes `data/current-problems.json` in the site checkout. It refuses to commit if the site repo already has staged changes, and it scans the staged dashboard diff before committing.

For a live dashboard endpoint, run the narrow JSON server on the validator host and put a TLS proxy in front of it:

```bash
uv run python scripts/serve_current_problems.py --host localhost --port 8731
```

The server exposes `GET /current-problems.json` and `GET /healthz`, sends CORS headers for the static website, and does not expose submissions, proofs, wallets, or operator state. Keep `refresh_site_current_problems.py` as the fallback snapshot publisher.

After checking the published snapshot, anchor the latest storage root on Bittensor:

```bash
uv run python scripts/publish_chain_commitment.py --repo ~/lemma-corpus --netuid sn467 --bt-netuid 467 --submit
```

Run it without `--submit` first to print the payload without writing chain state.
For mirror-only readback without wallet files, pass `--readback --hotkey <validator-hotkey-address>`.

Run the leak check before any commit or push:

```bash
uv run python scripts/leak_check.py
```

For the full local launch gate, run:

```bash
uv run python scripts/workstream_audit.py --profile mainnet --skip-site
```

# CLI

The public CLI is a thin reference client. It gets miners and validators through setup, status checks, local mining smoke tests, and validation. It is not the competitive mining engine.

## Public Commands

```bash
uv run lemma setup
uv run lemma status
uv run lemma mine --once --prover-command "python prover.py" --output submission.json
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
```

`setup` writes local task, corpus, active-window, wallet, unearned-allocation, and optional prover settings. `status` shows the active registry, verifier settings, wallet names, and prover command.

Local `LEMMA_TASK_REGISTRY_URL` or `file://` task-registry inputs must resolve to regular files; HTTP(S) registry fetching remains unchanged.

`mine` runs one reference proof-search iteration. It sends a task JSON object to the configured prover command and expects a JSON proof response on stdout. Serious miners should build their own agents, workers, models, schedulers, or direct protocol clients.

`validate` loads active tasks, rejects malformed submissions, checks proofs, scores accepted work, writes score events, and appends `validator-runs.jsonl`. A submission spool is a top-level directory of pending `.json` or `.jsonl` files; after a successful validator pass, consumed files move to `processed/`.

## Task Registry Commands

The task registry commands inspect, export, and sign exact Lean tasks. They do not generate paid production tasks.

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
uv run lemma tasks pull --output active-tasks.jsonl
uv run lemma tasks sign-registry \
  --input tasks/registry.json \
  --output tasks/signed.registry.json \
  --key-uri //Alice
uv run lemma operator preflight
```

Production validators should point `LEMMA_TASK_REGISTRY_URL` at a published real-task registry and set `LEMMA_TASK_REGISTRY_SHA256_EXPECTED` to the printed registry SHA256. Use `LEMMA_VERIFY_REGISTRY_SIGNATURES=1` when the registry publisher is expected to sign the exact payload.

## Proof Atlas Commands

The Proof Atlas publisher mirrors accepted proof rows, active registry caches, exports, and canonical tempo artifacts.

```bash
uv run python scripts/publish_proof_atlas_snapshot.py \
  --repo "$LEMMA_PROOF_ATLAS_REPO" \
  --netuid "sn${BT_NETUID}" \
  --sync-proof-dir "$LEMMA_CORPUS_OUTPUT_DIR" \
  --sync-canonical-dir "$LEMMA_CANONICAL_OUTPUT_DIR/sn${BT_NETUID}" \
  --sync-registry-cache-dir "$LEMMA_ACTIVE_REGISTRY_CACHE_DIR" \
  --dry-run
```

Run the leak check before committing or pushing a public snapshot.

## Protocol Contract

The stable surface is the protocol output, not the CLI implementation. Miners can use any infrastructure that produces valid task-bound proof submissions. Validators configure their environment and run `lemma validate`; lower-level diagnostics stay out of the normal path.

Task, submission, verification-result, score-event, and accepted-proof row shapes live under `spec/`. Proof Atlas and registry operator flows are documented separately in [Proof Atlas](proof-atlas.md) and [Operator Registry Flow](operator-registry-flow.md).

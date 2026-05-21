# CLI

The public CLI is a thin reference client for Lemma's proof protocol. It exists to make setup, status checks, reference mining, and validation easy to smoke test. It is not the competitive mining engine.

## Public Commands

```bash
uv run lemma setup
uv run lemma status
uv run lemma mine --once --prover-command "python prover.py" --output submission.json
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
```

`setup` writes local task/source-pool, corpus, active-window, wallet, unearned-allocation, and optional prover settings. `status` shows active task configuration, verifier settings, wallet names, and prover command.

`mine` runs one reference proof-search iteration. It sends a task JSON object to the configured prover command and expects a JSON proof response on stdout. Serious miners can replace this path with their own agents, workers, models, schedulers, or direct protocol clients.

`validate` loads active tasks, rejects malformed submissions, dispatches to the verifier registry, scores rank-0 unique verified proofs, writes score events, appends `validator-runs.jsonl`, and writes corpus rows. A submission spool is a top-level directory of pending `.json` or `.jsonl` files; after a successful validator pass, consumed files move to `processed/`.

## Protocol Contract

The stable surface is the protocol output, not the CLI implementation. Miners can use any infrastructure that produces valid task-bound proof submissions. Validators should configure their environment and run `lemma validate`; lower-level diagnostics and corpus tooling remain internal/debug surfaces during this first simplification pass.

Task, submission, verification-result, score-event, and corpus-row shapes live under `spec/`. Corpus and operator flows are documented separately in [Corpus](corpus.md) and [Operator Flow](operator-registry-flow.md).

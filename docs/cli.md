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

`mine` runs one reference proof-search iteration. It sends a task JSON object to the configured prover command and expects a JSON proof response on stdout. Serious miners should build their own agents, workers, models, schedulers, or direct protocol clients.

`validate` loads active tasks, rejects malformed submissions, checks proofs, scores accepted work, writes score events, and appends `validator-runs.jsonl`. A submission spool is a top-level directory of pending `.json` or `.jsonl` files; after a successful validator pass, consumed files move to `processed/`.

## Protocol Contract

The stable surface is the protocol output, not the CLI implementation. Miners can use any infrastructure that produces valid task-bound proof submissions. Validators configure their environment and run `lemma validate`; lower-level diagnostics stay out of the normal path.

Task, submission, verification-result, score-event, and corpus-row shapes live under `spec/`. Corpus and registry operator flows are documented separately in [Corpus](corpus.md) and [Operator Registry Flow](operator-registry-flow.md).

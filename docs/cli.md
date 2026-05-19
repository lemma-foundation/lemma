# CLI

The public CLI follows the miner and validator workflow directly.

## Setup

```bash
git clone https://github.com/lemma-foundation/lemma.git
cd lemma
uv sync --extra btcli
uv run lemma setup
uv run lemma status
```

`setup` writes local task, corpus, active-window, wallet, unearned-allocation, and optional prover settings. `status` shows the active registry, verifier settings, wallet names, and prover command.

## Miners

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
uv run lemma verify lemma.sample.true_intro --submission Submission.lean
uv run lemma submit lemma.sample.true_intro --submission Submission.lean --solver-hotkey <hotkey> --output submission.json
uv run lemma mine --once --prover-command "python prover.py" --output submission.json
```

`mine` sends a task JSON object to the configured prover command and expects a JSON proof response on stdout.

## Validators

```bash
uv run lemma worker --check
uv run lemma operator registry-inspect
uv run lemma operator preflight
uv run lemma operator diagnostics --output operator-diagnostics-before.json
uv run lemma validate --once --submissions-jsonl submissions.jsonl --no-set-weights
uv run lemma validate --once --submission-spool submission-spool --no-set-weights
uv run lemma operator diagnostics --output operator-diagnostics-after.json
```

After configuring a pinned registry hash, `operator registry-inspect` summarizes active, waiting, and parked supply depth. `operator preflight` checks registry hash pinning, active-window size, local output directories, and Lean verifier configuration before a validator pass. `operator diagnostics` writes the preflight report, registry summary, artifact counts, registry hash, and active task ids without env vars or local paths; capture it before and after validation to compare readiness with written artifacts. `validate` loads active tasks, rejects malformed submissions, dispatches to the verifier registry, scores first unique verified proofs, writes score events, appends `validator-runs.jsonl`, and writes corpus rows. A submission spool is a top-level directory of pending `.json` or `.jsonl` files; after a successful validator pass, consumed files move to `processed/`. Live chain writes require `LEMMA_ENABLE_SET_WEIGHTS=1` and `--set-weights`, and each attempt appends a public-safe `weight-submissions.jsonl` receipt under `LEMMA_OPERATOR_DATA_DIR` with the resolved UID vector and extrinsic hash when available.

## Task Supply

```bash
uv run lemma tasks build-mathlib-snapshot --input snapshot.jsonl --output tasks/mathlib-snapshot.registry.json
```

Task-supply commands are operator tools. The Mathlib snapshot builder converts proof-erased JSONL rows into a deterministic registry and prints the SHA256 pin for validator configuration.

The production registry flow is documented in [Operator Registry Flow](operator-registry-flow.md).

## Corpus

```bash
uv run lemma corpus validate corpus/epoch-1.jsonl
uv run lemma corpus replay corpus/epoch-1.jsonl
uv run lemma corpus export --input corpus --output corpus/corpus-index.json
uv run lemma corpus benchmark-export --input corpus --output exports/lemma-proofs.jsonl --index exports/index.json
uv run lemma export-corpus --domain lean --format jsonl --out data/lean_corpus.jsonl
```

Corpus commands are for operators and dataset users who want to validate, replay, index, or export accepted Lean theorem/proof records.

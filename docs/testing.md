# Testing

Run local checks:

```bash
uv run ruff check .
uv run mypy lemma
uv run bandit -q -r lemma scripts -ll
uv run pip-audit --ignore-vuln PYSEC-2025-49 --ignore-vuln PYSEC-2022-42969
uv run pytest tests -q
uv run python scripts/leak_check.py
```

Task inspection:

```bash
uv run lemma tasks list
uv run lemma task show lemma.sample.true_intro
```

Worker and validator smoke:

```bash
uv run lemma worker --check
uv run lemma validate --once --no-set-weights
uv run pytest tests/test_operator_registry_flow.py -q
```

The operator smoke fixture lives in [examples/operator-smoke](../examples/operator-smoke/README.md).
It also exercises the [Mathlib extraction contract](mathlib-extraction.md) used by the registry builder.

Corpus validation:

```bash
uv run lemma corpus validate corpus.jsonl
uv run lemma corpus replay corpus.jsonl
uv run lemma corpus export --input corpus --output corpus/corpus-index.json
uv run lemma corpus benchmark-export --input corpus --output exports/lemma-proofs.jsonl --index exports/index.json
```

Docker-backed Lean checks require the sandbox image:

```bash
uv run pytest tests/test_docker_golden.py -v --tb=short
```

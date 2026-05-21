# Testing

Run local checks:

```bash
uv run python scripts/workstream_audit.py
uv run ruff check .
uv run mypy lemma
uv run bandit -q -r lemma scripts -ll
uv run pip-audit --ignore-vuln PYSEC-2025-49 --ignore-vuln PYSEC-2022-42969
uv run pytest tests -q
uv run python scripts/leak_check.py
```

`scripts/workstream_audit.py` is the default work loop check. The quick profile runs formatting, type, privacy, targeted miner/validator/corpus tests, and static `lemmasub.net` checks when the sibling checkout exists. Use `--profile full` before larger pushes; it adds security audit commands and the full non-Docker pytest suite.
Use `--profile mainnet --skip-site` for the local launch gate; it adds the Docker Lean golden verification with `RUN_DOCKER_LEAN=1`.

Public CLI smoke:

```bash
uv run lemma --help
uv run lemma status
uv run lemma validate --once --no-set-weights
```

The operator smoke fixture lives in [examples/operator-smoke](../examples/operator-smoke/README.md).
It also exercises the [Mathlib extraction contract](mathlib-extraction.md) used by the registry builder.
The production-like smoke in `tests/test_operator_registry_flow.py` additionally covers public-input procedural rebuilds, procedural depth-2 gates, signed revealed submissions, strong structural proof identity, production preflight, diagnostics, scoring, and corpus validation.
Lower-level task, operator, and corpus commands are hidden from public help but remain covered by pytest during this transition.

Docker-backed Lean checks require the sandbox image:

```bash
uv run pytest tests/test_docker_golden.py -v --tb=short
```

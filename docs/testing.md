# Testing

Run the Python checks:

```bash
uv run ruff check lemma tests
uv run mypy lemma
uv run pytest tests -q
```

Run a local task inspection:

```bash
uv run lemma tasks list
uv run lemma tasks inspect lemma.sample.true_intro
```

Validate or replay a corpus file:

```bash
uv run lemma corpus validate corpus.jsonl
uv run lemma corpus replay corpus.jsonl
```

Docker-backed Lean checks require the `lemma/lean-sandbox` image and a warm Mathlib cache for reasonable latency.

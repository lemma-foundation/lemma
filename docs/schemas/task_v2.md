# Task Schema v2

`lemma/schemas/task_v2.json` is the task shape used by the current Lean path.

Required fields:

```json
{
  "task_id": "string",
  "domain_id": "lean",
  "verifier_id": "string",
  "verifier_version": "string",
  "task_type": "string",
  "created_at_block": 0,
  "source": "string",
  "prompt": {},
  "constraints": {},
  "scoring": {},
  "metadata": {}
}
```

For Lean tasks:

```json
{
  "domain_id": "lean",
  "verifier_id": "lake-build",
  "task_type": "theorem_proving",
  "prompt": {
    "theorem_name": "name",
    "imports": ["Mathlib"],
    "statement": "theorem ..."
  }
}
```

Legacy Lean rows are upgraded with `lemma.tasks.upgrade_task_v1_to_v2`.

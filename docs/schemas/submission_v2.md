# Submission Schema v2

`lemma/schemas/submission_v2.json` is the domain-neutral submission shape.

Required fields:

```json
{
  "task_id": "string",
  "domain_id": "string",
  "miner_hotkey": "string",
  "artifact": {},
  "created_at_block": 0,
  "declared_verifier_id": "string",
  "declared_verifier_version": "string",
  "metadata": {}
}
```

For Lean, `artifact` stores:

```json
{
  "proof": "string",
  "imports": ["Mathlib"],
  "full_file": "string"
}
```

The legacy Lean submission model still exists for miner compatibility. Dataset exports convert it into this shape.

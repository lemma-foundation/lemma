# Corpus Row Schema v2

`lemma/schemas/corpus_row_v2.json` is the canonical accepted artifact row.

Required fields:

```json
{
  "row_id": "sha256",
  "task_id": "string",
  "domain_id": "string",
  "verifier_id": "string",
  "verifier_version": "string",
  "task_type": "string",
  "prompt": {},
  "accepted_artifact": {},
  "verification": {
    "accepted": true,
    "stdout_hash": "sha256",
    "stderr_hash": "sha256",
    "metrics": {}
  },
  "provenance": {
    "miner_hotkey": "string",
    "validator_hotkey": "string",
    "block": 0,
    "timestamp": "string",
    "repo_commit": "string"
  },
  "dependencies": {},
  "graph": {},
  "license": "CC-BY-4.0",
  "metadata": {}
}
```

`row_id` is deterministic:

```text
sha256(domain_id + "\n" + task_id + "\n" + normalized_artifact_hash)
```

For Lean, `accepted_artifact` stores the proof, replayable full file, proof identity, and proof identity strength. `dependencies` and `graph` make each row a node in the corpus substrate instead of a flat record.

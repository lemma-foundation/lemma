# Verus Domain

Verus/Rust verification is the first non-Lean domain candidate. It is experimental and disabled by default.

Enable only for local experiments:

```bash
LEMMA_ENABLE_EXPERIMENTAL_VERUS=1
```

## Expected Task Shape

```json
{
  "domain_id": "verus",
  "task_type": "rust_function_verification",
  "prompt": {
    "function_signature": "...",
    "specification": "...",
    "tests": [],
    "allowed_imports": []
  }
}
```

## Expected Artifact Shape

```json
{
  "artifact": {
    "rust_code": "...",
    "spec": "...",
    "proof_annotations": "..."
  }
}
```

## Before Launch

- no network;
- CPU limit;
- memory limit;
- wall-clock timeout;
- filesystem isolation;
- pinned Verus version;
- deterministic build image;
- adversarial tests.

# Domain Adapter Spec

A Lemma domain is a verifier-backed task market that can produce replayable public corpus rows.

## Required Contract

A Lemma domain must provide:

- deterministic verifier;
- pinned verifier version;
- task schema;
- submission schema;
- normalized corpus row format;
- sandboxing rules;
- timeout and memory limits;
- duplicate policy;
- public license;
- scoring function;
- adversarial tests.

## Adapter Interface

Each production domain enters through `lemma.verifiers.base.VerifierAdapter`:

```python
class VerifierAdapter:
    domain_id: str
    verifier_id: str

    def verify(self, task: dict, submission: dict) -> VerificationResult: ...
    def normalize_artifact(self, task: dict, submission: dict, result: VerificationResult) -> dict: ...
    def task_schema(self) -> dict: ...
    def submission_schema(self) -> dict: ...
```

No artifact can enter the corpus unless the deterministic verifier accepts it.

## Domain Maturity Levels

- Level 0: idea only
- Level 1: local verifier adapter
- Level 2: validator can verify
- Level 3: corpus export works
- Level 4: active miner competition
- Level 5: external model-training consumers

Lean is the only Level 4 production domain today. Verus is Level 0/1 and disabled by default.

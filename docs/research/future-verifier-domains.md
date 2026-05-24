# Background Research: Other Verifier Domains

This is archived background research, not Lemma's production thesis or roadmap. Lemma's public identity is the Lean proof competition. Do not use this doc to imply a broader roadmap, and do not link it from the homepage or primary getting-started path.

The same verification pattern could be studied outside Lean only when a domain has deterministic verifiers and replayable outputs.

These are research directions, not live production mechanisms.

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

No artifact can enter the corpus unless the deterministic verifier accepts it.

## Examples

- **Verus:** Rust programs plus formal specifications and proofs.
- **SAT/SMT:** Logic formulas with satisfying assignments, solver traces, or unsat certificates.
- **LP/SDP:** Optimization problems with primal/dual certificates.
- **Cryptanalysis:** Security puzzles with verifiable witnesses such as factors, collisions, keys, or attack artifacts.

## Adapter Interface

Each experimental adapter enters through `lemma.verifiers.base.VerifierAdapter`:

```python
class VerifierAdapter:
    domain_id: str
    verifier_id: str

    def verify(self, task: dict, submission: dict) -> VerificationResult: ...
    def normalize_artifact(self, task: dict, submission: dict, result: VerificationResult) -> dict: ...
    def task_schema(self) -> dict: ...
    def submission_schema(self) -> dict: ...
```

## Domain Maturity Levels

- Level 0: idea only
- Level 1: local verifier adapter
- Level 2: validator can verify
- Level 3: corpus export works
- Level 4: active miner competition
- Level 5: external model-training consumers

Lean theorem proving is the only active competition path. Verus is Level 0/1 and disabled by default.

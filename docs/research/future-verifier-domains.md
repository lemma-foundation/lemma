# Background Research: Future Verifier Domains

This is background research, not Lemma's production thesis. Lemma's public production identity is formal mathematics in Lean. Do not link this doc from the homepage or primary getting-started path.

The same network pattern can apply outside Lean only when a domain has deterministic verifiers and replayable outputs.

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

## Domain Maturity Levels

- Level 0: idea only
- Level 1: local verifier adapter
- Level 2: validator can verify
- Level 3: corpus export works
- Level 4: active miner competition
- Level 5: external model-training consumers

Lean is the only Level 4 production domain today. Verus is Level 0/1 and disabled by default.

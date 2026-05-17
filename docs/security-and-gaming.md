# Security And Gaming

Lemma is easier to verify than many subnets because Lean gives a binary correctness signal. But task selection, deduplication, and submission ordering still matter.

## Threats And Defenses

### Copying

Delay public proof release until epoch close. Use signed submissions and commit/reveal when available.

### Duplicate Proofs

Hash proof scripts and proof terms. Pay only the first unique accepted proof per task in v1.

### Public-Proof Copying

Avoid direct public-proof tasks. Use generated variants, proof repair, and premise-limited reproving.

### Trivial Tasks

Run a baseline tactic gate. Skip or downweight tasks solved by baseline tactics.

### Invalid Proofs

Reject `sorry`, `admit`, custom axioms, changed goals, banned imports, unsafe code, and network dependencies.

### Validator Dishonesty

All accepted corpus rows should be replayable. Deterministic verification makes dishonest scoring easier to detect.

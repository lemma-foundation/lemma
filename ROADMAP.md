# Lemma Roadmap

Lemma is a Verified Reasoning Network, starting with Lean proofs. The roadmap is deliberately staged: make one deterministic verifier domain reliable before expanding the surface area.

## Phase 1: Lean Corpus Engine

- stabilize the Lean verifier path;
- improve miner/validator reliability;
- keep the scoring rule binary: accepted or rejected;
- export replayable Lean domain corpus rows.

## Phase 2: Corpus Productization

- dataset cards;
- JSONL, Parquet, and Hugging Face style exports;
- train/validation/test splits;
- exact duplicate detection and metadata flags for near-duplicates;
- clearer public packaging for verified reasoning data.

## Phase 3: Verifier Adapter Architecture

- generic `VerifierAdapter` interface;
- domain registry;
- task, submission, and corpus row schema v2;
- runtime metadata for pinned verifier containers.

## Phase 4: Verus Pilot

- experimental Verus verifier adapter;
- Rust/spec/proof corpus row shape;
- sandboxing requirements;
- sample task and submission fixtures.

Verus is a roadmap pilot, not a live production mechanism.

## Phase 5: Additional Verified Domains

Candidate roadmap directions include:

- SAT/SMT certificates;
- optimization certificates;
- cryptanalysis witnesses;
- other domains with deterministic verifiers and clear corpus value.

These domains do not enter production rewards until they are deterministic, replayable, licensed, safe, and covered by adversarial tests.

## Non-Goals For Now

- Do not launch many domains before Lean works.
- Do not accept non-verifiable artifacts.
- Do not let tests-only code tasks pollute verified reasoning data.
- Do not make schema changes that break reproducibility.

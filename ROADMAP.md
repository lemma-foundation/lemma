# Lemma Roadmap

## Phase 1: Lean Corpus Engine

- stabilize the Lean verifier path;
- improve miner/validator reliability;
- keep the scoring rule binary: accepted or rejected;
- export replayable Lean domain corpus rows.

## Phase 2: Corpus Productization

- dataset cards;
- JSONL, Parquet, and Hugging Face style exports;
- train/validation/test splits;
- exact duplicate detection and metadata flags for near-duplicates.

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

## Phase 5: Multi-Domain Lemma

- SAT/SMT certificates;
- optimization certificates;
- cryptanalysis witnesses;
- other domains with deterministic verifiers and clear corpus value.

## Non-Goals For Now

- Do not launch many domains before Lean works.
- Do not accept non-verifiable artifacts.
- Do not let tests-only code tasks pollute verifier-grounded corpus branding.
- Do not make schema changes that break reproducibility.

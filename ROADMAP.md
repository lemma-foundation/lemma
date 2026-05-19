# Lemma Roadmap

Lemma's v1 roadmap is focused on one domain: Lean formal mathematics.

## Phase 1: Reliable Lean Proof Market

- stabilize Lean verification;
- improve the miner and validator loop;
- harden task-bound submissions;
- keep scoring simple and binary.

## Phase 2: Better Mathematical Task Supply

- improve active theorem pool construction;
- expand useful Lean task fixtures;
- add Mathlib-derived task supply;
- improve novelty and triviality filtering.

## Phase 3: Corpus Quality

- export replayable JSONL and Parquet releases;
- include proof, statement, imports, dependencies, verifier metadata, attribution, and license metadata;
- add dataset cards;
- add train/validation/test splits;
- improve duplicate and near-duplicate flags.

## Phase 4: Citation Graph And Reuse

- improve dependency extraction;
- expose graph queries;
- support citation-aware rewards if and when production-ready;
- package proof chains for downstream theorem-prover training.

## Phase 5: Open Theorem-Prover Ecosystem

- support downstream training pipelines;
- support contamination-resistant evaluation by block or time cutoff;
- document replay and audit workflows.

## Background Research

Future non-math verifier domains are kept in [docs/research/future-verifier-domains.md](docs/research/future-verifier-domains.md). They are not part of v1 production framing.

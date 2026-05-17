# ExecPlan: Lemma Training-First Refactor

Status: implementation checklist completed on `codex/training-data-first-v1`.

## Current-State Audit

- [x] 001 Confirm Lemma v1 already centers Lean-verified proof data.
- [x] 002 Confirm normal Bittensor emissions are the v1 reward mechanism.
- [x] 003 Confirm smart-contract custody is not active in code.
- [x] 004 Confirm bounty escrow docs were removed from the primary docs.
- [x] 005 Confirm Formal Conjectures is positioned as a benchmark, not payout path.
- [x] 006 Confirm the site is static HTML/CSS.
- [x] 007 Confirm the CLI exposes miner and validator workflows.
- [x] 008 Confirm task, submission, and corpus schemas exist.
- [x] 009 Identify missing verification-result schema.
- [x] 010 Identify missing score-event schema.
- [x] 011 Identify docs filenames that do not match the goal file.
- [x] 012 Identify CLI help gaps around examples.
- [x] 013 Identify stale `tasks inspect` wording.
- [x] 014 Identify stale `corpus index` public wording.
- [x] 015 Confirm private local agent-state files are not tracked.

## Files To Remove, Rename, Rewrite, Or Keep

- [x] 016 Keep Lean verifier modules.
- [x] 017 Keep Docker Lean sandbox files.
- [x] 018 Keep `lemma.tasks` as task registry code.
- [x] 019 Keep `lemma.submissions` as task-bound proof package code.
- [x] 020 Keep `lemma.corpus` as corpus JSONL code.
- [x] 021 Keep `lemma.miner` as local prover adapter.
- [x] 022 Keep `lemma.validator` as verification/scoring/corpus workflow.
- [x] 023 Rename `docs/overview.md` to `docs/what-is-lemma.md`.
- [x] 024 Rename `docs/incentives.md` to `docs/scoring.md`.
- [x] 025 Rename `docs/task-supply.md` to `docs/tasks.md`.
- [x] 026 Rename `docs/security.md` to `docs/security-and-gaming.md`.
- [x] 027 Rename `docs/model-api.md` to `docs/model-apis.md`.
- [x] 028 Add `docs/how-it-works.md`.
- [x] 029 Add `docs/formal-conjectures.md`.
- [x] 030 Add `docs/cli.md`.
- [x] 031 Keep `docs/architecture.md`.
- [x] 032 Keep `docs/miner.md`.
- [x] 033 Keep `docs/validator.md`.
- [x] 034 Keep `docs/corpus.md`.
- [x] 035 Keep `docs/benchmarks.md`.
- [x] 036 Keep `docs/testing.md`.
- [x] 037 Keep `docs/faq.md`.
- [x] 038 Keep `docs/production.md`.
- [x] 039 Keep `lemma_training_first_goal.md` local unless explicitly asked to publish it.

## Docs Structure

- [x] 040 README explains the training-data-first design.
- [x] 041 README explains what Lemma is.
- [x] 042 README explains what Lemma is not.
- [x] 043 README shows miner quick start.
- [x] 044 README shows validator quick start.
- [x] 045 README documents `score = verified_unique_wins / K`.
- [x] 046 README links to `docs/what-is-lemma.md`.
- [x] 047 README links to `docs/how-it-works.md`.
- [x] 048 README links to `docs/tasks.md`.
- [x] 049 README links to `docs/scoring.md`.
- [x] 050 README links to `docs/security-and-gaming.md`.
- [x] 051 README links to `docs/model-apis.md`.
- [x] 052 README links to `docs/formal-conjectures.md`.
- [x] 053 README links to `docs/cli.md`.
- [x] 054 Miner docs explain task discovery.
- [x] 055 Miner docs explain local verification.
- [x] 056 Miner docs explain submission packaging.
- [x] 057 Miner docs explain optional prover APIs.
- [x] 058 Validator docs explain verification results.
- [x] 059 Validator docs explain score events.
- [x] 060 Validator docs explain corpus writing.
- [x] 061 Task docs explain source streams.
- [x] 062 Task docs explain activation rules.
- [x] 063 Scoring docs explain credits.
- [x] 064 Scoring docs explain v1 score.
- [x] 065 Scoring docs explain Bittensor weights.
- [x] 066 Security docs explain theorem identity pinning.
- [x] 067 Security docs explain deduplication.
- [x] 068 Security docs explain first-valid-wins behavior.
- [x] 069 Corpus docs explain replay fields.
- [x] 070 Model API docs stay provider-neutral.
- [x] 071 Benchmarks docs keep Formal Conjectures as frontier evaluation.
- [x] 072 Formal Conjectures docs state non-endorsement.
- [x] 073 CLI docs list setup/status/task/mine/submit/verify/validate/corpus workflows.
- [x] 074 Testing docs use current CLI names.

## Site Rewrite

- [x] 075 Keep site static.
- [x] 076 Keep site dependency-free.
- [x] 077 Use the hero headline: “Train the best open mathematical prover.”
- [x] 078 Use the simple line: “AI can guess. Lean can check. Lemma pays for the checked data.”
- [x] 079 Explain miners above the fold.
- [x] 080 Explain validators above the fold.
- [x] 081 Explain the corpus above the fold.
- [x] 082 Mention Google DeepMind Formal Conjectures as a frontier benchmark.
- [x] 083 Add a Why Lean section.
- [x] 084 Add a Get Started section.
- [x] 085 Keep independence and non-endorsement copy.
- [x] 086 Avoid fake stats.
- [x] 087 Avoid AGI overpromising.
- [x] 088 Avoid contract, escrow, and custody framing.
- [x] 089 Keep mobile layout readable.
- [x] 090 Respect reduced motion.

## Code Refactor

- [x] 091 Keep simple Pydantic domain models.
- [x] 092 Add `VerificationResult` scoring-domain model.
- [x] 093 Keep `VerificationRecord` as compatibility alias.
- [x] 094 Add `ScoreEvent` model.
- [x] 095 Keep score calculation pure.
- [x] 096 Keep Lean verification separate from scoring.
- [x] 097 Keep provider metadata out of scoring.
- [x] 098 Keep failed proofs out of corpus rows.
- [x] 099 Write score events in validator runs.
- [x] 100 Include verifier reason in verification results.
- [x] 101 Keep proof identity as proof-term hash when available.
- [x] 102 Fall back to proof-script hash for deduplication.

## CLI Plan

- [x] 103 Root CLI help includes examples.
- [x] 104 `setup` help includes an example.
- [x] 105 `status` help includes an example.
- [x] 106 `mine` help includes examples.
- [x] 107 `tasks list` stays available.
- [x] 108 `tasks show` is added.
- [x] 109 `tasks inspect` remains hidden as compatibility alias.
- [x] 110 `task show` is added for goal-language workflow.
- [x] 111 `verify` help includes an example.
- [x] 112 `submit` help includes an example.
- [x] 113 `validate` help includes an example.
- [x] 114 `corpus export` is added.
- [x] 115 `corpus index` remains hidden as compatibility alias.
- [x] 116 CLI validation output includes `scores`.

## Schemas

- [x] 117 Keep `spec/task.schema.json`.
- [x] 118 Keep `spec/submission.schema.json`.
- [x] 119 Keep `spec/corpus-row.schema.json`.
- [x] 120 Add `spec/verification-result.schema.json`.
- [x] 121 Add `spec/score-event.schema.json`.
- [x] 122 Test task schema required fields.
- [x] 123 Test submission schema required fields.
- [x] 124 Test corpus row schema required fields.
- [x] 125 Test verification result schema required fields.
- [x] 126 Test score event schema required fields.

## Tests And Verification

- [x] 127 Add deterministic v1 score test with active task count.
- [x] 128 Add score-event test.
- [x] 129 Add verification-result extra-field rejection test.
- [x] 130 Add score-event extra-field rejection test.
- [x] 131 Add CLI `task show` alias test.
- [x] 132 Add public docs structure test.
- [x] 133 Run formatter.
- [x] 134 Run lint.
- [x] 135 Run type checks.
- [x] 136 Run tests.
- [x] 137 Run leak check before publish.

## Acceptance Criteria

- [x] 138 README is readable by a non-expert.
- [x] 139 Site is calm and understandable above the fold.
- [x] 140 Docs cover miners.
- [x] 141 Docs cover validators.
- [x] 142 Docs cover task supply.
- [x] 143 Docs cover scoring.
- [x] 144 Docs cover corpus.
- [x] 145 Docs cover APIs.
- [x] 146 Docs cover security.
- [x] 147 Docs cover benchmarks.
- [x] 148 CLI help matches workflows.
- [x] 149 Code does not present v1 as bounty escrow.
- [x] 150 No justified deferrals remain for this pass.

# The Evolution of Lemma

## From Judged Reasoning to an Open Research Network for Lean Proof Agents

Lemma has gone through many implementation changes, but only a smaller number
of genuine design-philosophy shifts. This history reconstructs those shifts
from the project's first commit, explains what each iteration was trying to
solve, and identifies the principles that survived.

The repository contains hundreds of commits. Most are implementation,
operations, security, or documentation work within one of the larger designs
described below.

## Timeline

| Period | Lemma's identity | Rewarded object | Governing philosophy |
| --- | --- | --- | --- |
| May 1, 2026 | LLM theorem-solving subnet | Valid Lean proof plus a good reasoning trace | Mechanical correctness as a gate, followed by subjective quality ranking |
| May 3 | Two-lane proof network | Fast automated solves and longer-horizon bounties | Support both proof agents and offline human-scale proving |
| May 7-10 | Proof-centric theorem competition | Increasingly, any verified proof | Remove subjective judging and reward objective work |
| May 14 | Winner-take-all proof protocol | First proof of one active theorem | Radical simplicity and legibility |
| May 15-16 | Restored Bittensor proof subnet plus bounties | Verified proofs and separately accepted bounty work | Continuous emissions and discrete procurement are different mechanisms |
| May 17 | Escrow-backed proof-bounty system | First valid proof for a funded bounty | No funded escrow, no reward promise |
| May 17-18 | Training-first proof-data subnet | Verified proof units producing corpus rows | The corpus is the product; the market produces it |
| May 18 | Verified Reasoning Network | Any deterministically verified artifact | Math is the wedge; verified data is the broader product |
| May 18 onward | Formal-mathematics network | Lean theorem/proof records | Narrow the public thesis to one deep, credible domain |
| May 19-23 | Self-generating production network | First valid proof of procedural tasks | Public deterministic task generation and continuous frontier pressure |
| May 24 | Open proof-agent competition | Winning verifier-accepted proof | Agent improvement is primary; the corpus is a durable byproduct |
| May 25-29 | Adaptive procedural proof economy | Weighted frontier tasks | Balance difficulty, throughput, cost, and novelty dynamically |
| June 4-5 | Registry-backed real-task competition | First valid proof of a real missing Lean target | Reward real public work rather than generated activity |
| Later design review | Emission-funded research network | Measured agent improvement on unseen tasks | The experiment is the product; emissions fund open research |
| Broader exploration | Lemma-like agent competitions | Tested and owner-accepted work artifacts | Transfer the verifier pattern beyond mathematics |

## 1. The Original Design: Proofs Plus Judged Reasoning

The initial repository commit,
[`738b4e4`](https://github.com/lemma-foundation/lemma/commit/738b4e4),
described Lemma as a subnet where miners submitted Lean 4 proofs and reasoning
traces. Validators verified proofs in Docker and used an LLM judge to score the
traces.

The original scoring system therefore had two layers:

1. Lean determined whether the submitted proof was correct.
2. An LLM judge scored the miner's explanation.
3. A Pareto calculation favored stronger reasoning scores and shorter traces.

The default work was generated from a collection of theorem templates. Each
round selected a deterministic theorem from a shared seed, and miners had a
fixed response window.

This version treated Lemma as a fairly conventional AI-model competition. Lean
prevented false proofs from winning, but the LLM judge still determined how
rewards were divided among valid solvers.

Its implicit research question was:

> Under a shared time limit and deterministic challenge stream, do prover
> systems become better at producing correct Lean proofs and concise
> explanations?

The weakness was that the protocol combined an objective correctness boundary
with a subjective quality judgment.

## 2. Two Lanes: Automated Rounds and Long-Horizon Bounties

Commit
[`5ff88e2`](https://github.com/lemma-foundation/lemma/commit/5ff88e2)
introduced the first substantial vision and roadmap.

It recognized that serious theorem proving does not always fit inside a
five-minute or one-hour validator round. The design proposed two lanes:

- a steady, high-frequency lane for automated proof agents;
- a bounty lane where humans, teams, or agents could work offline for days or
  months and submit when ready.

A principle appeared here that survived nearly every later iteration: Lemma
should care about the final verified proof, not how it was produced.

A proof could come from an LLM, tactic search, retrieval, custom software, or a
human mathematician. Lean supplied the shared correctness boundary.

The difficulty was economic. Continuous Bittensor emissions and discrete,
long-horizon bounties are different instruments, and this design had not yet
cleanly separated them.

## 3. The Move Toward Binary Proof Rewards

Commit
[`6ab7b0d`](https://github.com/lemma-foundation/lemma/commit/6ab7b0d)
moved from judge-dominant ranking toward proof-centric scoring. It added
anti-copy controls, reputation, coldkey partitioning, commit/reveal machinery,
judge-profile pinning, and extensive mechanism documentation.

This phase became complicated because it tried to solve many problems at once:

- How should several valid proofs divide rewards?
- Should shorter or structurally better proofs receive more?
- How should copied proofs be handled?
- Can reputation improve incentives?
- How can validators demonstrate comparable judge configurations?
- Can multiple hotkeys or coldkeys game the mechanism?

The key philosophical turn came in
[`2bbc991`](https://github.com/lemma-foundation/lemma/commit/2bbc991):
live rewards became binary Lean pass/fail. Then
[`590470b`](https://github.com/lemma-foundation/lemma/commit/590470b)
allowed every verified miner entry to participate rather than dropping
identical valid proofs.

The emerging principle was:

> Reward the objectively verified unit. Treat proof metrics as analysis, not
> ground truth.

Every additional scoring heuristic weakened the cleanest part of Lemma. Lean
could prove that an artifact was correct. It could not prove that a particular
explanation, proof length, or stylistic property deserved more emissions.

## 4. The Winner-Take-All Experiment

Commit
[`f578b10`](https://github.com/lemma-foundation/lemma/commit/f578b10)
replaced the complicated subnet with an extremely narrow protocol:

1. Publish one ordered theorem.
2. Accept only a `proof_script`.
3. Verify it against the locked target.
4. Record the first passing solver.
5. Give the current champion all miner weight until another theorem is solved.

This design explicitly removed prose scores, proof-efficiency scores,
difficulty multipliers, subjective judges, and much of the earlier protocol
machinery.

Its philosophy was radical legibility: one theorem, one winner, and one
unambiguous rule.

The tradeoff was economic distortion. A miner who solved one target could
retain all weight while no new target was solved. It also converted a
continuous network into a succession of champion monopolies. The experiment
lasted only about a day.

## 5. Restoring the Subnet and Separating Bounties

Commit
[`a2e4ae0`](https://github.com/lemma-foundation/lemma/commit/a2e4ae0)
restored the pre-winner-take-all architecture.

Verified proofs again became eligible for normal miner rewards. The repository
also developed live task feeds, bounty acceptance, a solve portal, and manual
bounty-review procedures.

The design was beginning to distinguish two kinds of value:

- relative performance in an ongoing proof network;
- completion of a specific valuable theorem requested by someone.

This distinction matters because Bittensor weights can influence relative
future emissions, but they are not naturally exact payments for individual
tasks.

## 6. Escrow-Backed Proof Procurement

Commit
[`3e9e0d4`](https://github.com/lemma-foundation/lemma/commit/3e9e0d4)
temporarily rebuilt Lemma around smart-contract proof bounties.

Its governing sentence was:

> No funded escrow, no reward promise.

A sponsor would fund an EVM escrow. Solvers would commit to hidden artifacts,
reveal their proofs, and have validators check them with Lean. The contract
would pay after an attestation and challenge process.

This was the most marketplace-like version of Lemma. It treated proof
production as procurement:

- a specific sponsor;
- a specific theorem;
- a funded amount;
- a verifiable deliverable;
- a deterministic payout rule.

It solved the question of where a promised payment came from, but introduced
two identity systems, custody and contract risk, validator-attestation rules,
deployment complexity, and the still-unanswered question of who would fund
enough bounties.

## 7. The Corpus Becomes the Product

Later on May 17,
[`537db70`](https://github.com/lemma-foundation/lemma/commit/537db70)
replaced the bounty product with a training-first corpus MVP.

The thesis became: build a large and useful open corpus of verified Lean
theorem/proof data.

```text
tasks -> proof search -> Lean verification -> rewards -> public corpus
      -> improved theorem provers
```

Frontier benchmarks became evaluation targets rather than bounty streams.

Commit
[`b0464f1`](https://github.com/lemma-foundation/lemma/commit/b0464f1)
sharpened the idea into:

> The corpus is the product. The market is the means.

This introduced several lasting architectural ideas:

- task-bound submissions;
- replayable corpus rows;
- source and license metadata;
- proof hashes and stronger notions of proof identity;
- rewarded versus valid-but-unrewarded alternate proofs;
- public training and evaluation exports;
- first-accepted-unique-proof scoring.

This was a major improvement in coherence, but it contained an assumption that
had not been validated: producing more verified proof rows does not necessarily
produce useful training data.

## 8. The Verified Reasoning Network

Commit
[`1d0bcff`](https://github.com/lemma-foundation/lemma/commit/1d0bcff)
generalized the corpus thesis:

> Math is the wedge. Verified data is the product.

The essential unit was no longer specifically a lemma. It was any artifact
accepted by a deterministic verifier.

Proposed domains included:

- Lean theorem proofs;
- Verus-verified Rust;
- SAT/SMT certificates;
- LP/SDP optimization certificates;
- cryptanalytic witnesses.

The code briefly gained a generic verifier interface, domain-neutral task and
submission schemas, a domain registry, a disabled Verus adapter, domain
maturity levels, and cross-domain corpus exports.

Commit
[`8e3747e`](https://github.com/lemma-foundation/lemma/commit/8e3747e)
branded the project as a **Verified Reasoning Network**.

This was not intended as a network for subjectively judging arbitrary AI
answers. Every domain still had to provide deterministic verification,
replayable outputs, pinned tooling, sandboxing, licensing, and useful corpus
value.

Its weakness was positioning. The project had not demonstrated one working
domain, so presenting it as a universal substrate risked becoming architecture
in search of demand.

## 9. Formal Mathematics Becomes the Public Identity

Commit
[`291d0e3`](https://github.com/lemma-foundation/lemma/commit/291d0e3)
deliberately narrowed the thesis:

> Math is the domain. The product is the open mathematical corpus.

The broader verifier architecture was moved into background research, and Lean
became the only production domain.

The design philosophy was focus:

- Lean has a mature verifier.
- Mathlib provides a deep shared environment.
- Formal mathematics can support years of work.
- The correctness boundary is unusually clean.
- A narrow identity is more credible than a universal one.

Model training became a downstream use rather than the project's public
justification. Lemma was now positioned more like open mathematical
infrastructure with an incentive layer.

## 10. Building a Self-Sustaining Proof-Production Machine

From May 19 through May 23, development concentrated on making the mathematical
network continuously operational.

This period introduced public corpus snapshots and manifests, storage mirrors,
chain- and drand-derived task selection, active task windows, signed
submissions, commit/reveal buckets, public curriculum state, procedural task
generation, novelty receipts, difficulty controls, strong proof identity, and
canonical public artifacts.

The central operational problem was task supply:

> How can Lemma continuously create fresh, nontrivial, reproducible theorem
> work without relying on one operator to manually select every problem?

The answer was procedural generation from pinned public mathematical inputs.
Validators would independently reconstruct the same tasks from public sources
and randomness.

This was technically sophisticated, but the mechanism became much more
elaborate than the thesis. It could demonstrate that tasks were reproducibly
generated, but not that those tasks were mathematically useful.

## 11. Competition First, Data Second

Commit
[`054dff2`](https://github.com/lemma-foundation/lemma/commit/054dff2)
reframed Lemma as an open competition for formal proof. Commit
[`32de6a2`](https://github.com/lemma-foundation/lemma/commit/32de6a2)
restored the explicit proof-agent framing.

The emphasis shifted from manufacturing a dataset to operating a competition:

- miners build proof-search agents;
- agents compete on active Lean tasks;
- Lean judges the final output;
- winning proof work earns credit;
- proof records are preserved as a durable byproduct.

This avoided claiming that every verified row was inherently valuable training
data. The competition became the immediate activity, while the corpus became
evidence and reusable output.

## 12. The Adaptive Procedural Frontier

From May 25 through May 29, the procedural system was expanded and tuned:

- frontier depth adjusted with solve rates;
- active task count adjusted with validator capacity and cost;
- task families were diversified;
- Mathlib import graphs influenced selection;
- trivial or directly reusable source tasks were filtered;
- source-oracle checks tried to detect artificial difficulty;
- public state and replay lag prevented private operator decisions from
  silently changing rewards;
- auditor validators could inspect the active task supply.

The philosophy was a self-adjusting research arena: increase depth when agents
solve too easily, reduce task count when validation becomes expensive, and
keep the subnet tempo fixed while adapting the work inside it.

The failure mode was complexity and questionable task meaning. A procedurally
mutated theorem could be difficult and novel according to the machinery while
still being artificial or unimportant.

## 13. Real Tasks Replace Procedural Paid Work

Commit
[`970cc3b`](https://github.com/lemma-foundation/lemma/commit/970cc3b)
made the final major repository pivot:

> Production supply is registry-backed real Lean work.

Rewardable tasks now had to come from public sources such as SorryDB, Lean
projects containing actual missing proofs, reviewed human-curated targets, or
appropriate formal-conjecture sources.

Fixed fixtures, baseline-solved examples, source wrappers, benchmark-only
tasks, and generated calibration work were excluded from paid production.

Commit
[`ce49c87`](https://github.com/lemma-foundation/lemma/commit/ce49c87)
completed the real-task validation, scoring, Proof Atlas, site, and
launch-readiness path.

The repository's resulting philosophy is:

1. Use real, publicly attributable missing Lean proof work.
2. Pin exact task bytes.
3. Let agents use any proof-search strategy.
4. Verify only the final task-bound artifact.
5. Reward the first eligible accepted proof.
6. Leave unsolved reward share unearned rather than redistributing it.
7. Publish replayable proof artifacts.
8. Fail closed when provenance, identity, reproducibility, or privacy is
   inadequate.

This is the most operationally defensible repository version, although the
economic and research thesis still requires empirical validation.

## 14. The Research-Subnet Reframing

The following phase came from design review after the current public repository
history. It should therefore be understood as a proposed thesis rather than a
shipped protocol revision.

The review raised a fundamental concern: a correct proof does not automatically
have a buyer, get merged upstream, solve a problem someone values, or justify a
marketplace.

That concern led to an important distinction. A Bittensor subnet does not have
to be a conventional customer-funded marketplace. It can instead be an
emission-funded research competition.

The resulting thesis is:

> Lemma can plausibly operate as an open research network for developing and
> evaluating Lean proof agents. It should not call itself a proof marketplace
> without evidence of sponsor demand.

Under this model:

- miners develop proof agents;
- validators run them on fresh or hidden tasks;
- compute and inference budgets are controlled;
- emissions reward relative performance;
- proofs, failures, and trajectories become public research artifacts;
- success means improvement over a baseline, not merely a running subnet.

The smallest meaningful experiment would use:

- several hundred reproducible tasks;
- public-development and hidden-evaluation splits;
- one clear baseline prover;
- genuinely different agent strategies;
- equal compute or inference budgets;
- solve-rate, cost, time, reliability, and improvement measurements;
- a stop condition if competition does not outperform the baseline.

This framing does not require pretending that subnet emissions are exact
per-proof payments or that every accepted proof has a commercial customer.

## 15. Expansion Beyond Mathematics

The broader exploration returned to Lemma's most transferable abstraction:

> Difficult generation plus cheap, deterministic, replayable verification.

That pattern suggests Lemma-like agent competitions for software upgrades, bug
reproduction, API connectors, ETL and data-pipeline repair, infrastructure
repair, spreadsheet auditing, accessibility fixes, scientific reproduction,
standards compliance, and incident recovery.

This is broader than the earlier Verified Reasoning Network. The May 18 design
required a fully deterministic formal verifier. The later pattern allows:

- automated tests to establish objective properties;
- hidden tests to resist overfitting;
- humans to resolve ambiguous business meaning;
- task owners to accept the final artifact.

ETL is one concrete example. Agents could repair broken data pipelines against
hidden data-contract tests, while human owners decide meanings that tests cannot
infer, such as the intended definition of revenue or whether two identifiers
represent the same entity.

This wider pattern remains an exploration, not the current production identity
of Lemma.

## What Never Changed

Despite all the pivots, five beliefs remained remarkably stable.

### 1. The final artifact matters more than the method

Lemma should not care whether a proof came from an LLM, tactics, retrieval,
search, custom software, or a human.

### 2. Mechanical verification is the foundation

Subjective judging repeatedly entered the design and was repeatedly removed.

### 3. Tasks and outputs must be replayable

Exact statements, toolchains, hashes, imports, provenance, and verifier results
are essential.

### 4. Operator discretion is dangerous

Pinned registries, public randomness, deterministic selection, public
curriculum state, and canonical artifacts all reduce hidden authority.

### 5. Verified does not necessarily mean valuable

This is the unresolved issue behind the moves from generated tasks to corpora,
from corpora to competition, from procedural tasks to real tasks, and finally
from marketplace claims to a controlled research experiment.

## Overall Assessment

The project did not wander randomly. It repeatedly tested different answers to
three separate questions:

1. **What is produced?** A proof, a bounty deliverable, a corpus row, verified
   data, or better agents?
2. **Who values it?** The subnet, model trainers, mathematical projects,
   sponsors, or research observers?
3. **How should rewards attach to it?** Subjective ranking, all valid work,
   first valid work, champion weight, escrow payment, or relative experimental
   performance?

The strongest surviving answer is:

> Lemma is an open competition for improving Lean proof agents, grounded in
> exact mechanical verification, with replayable proofs and trajectories as
> public research output.

The real-task registry is useful infrastructure for that thesis, but it is not
itself proof of success. The decisive evidence would be sustained agent
improvement on controlled unseen tasks.

Everything before that, including a large repository, passing tests, on-chain
emissions, and a growing proof inventory, is infrastructure or activity rather
than confirmation that the research network works.

## Historical Scope

Sections 1 through 13 are reconstructed from the public Git history and linked
commits. Sections 14 and 15 describe subsequent design analysis that had not
been adopted as a committed production revision at the time this document was
written.

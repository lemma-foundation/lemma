# Protocol Invariants

These invariants protect Lemma's core promise: published theorem/proof records must be mechanically verified, replayable, licensed, and safe to publish as open proof data.

Lemma launches with Lean formal mathematics as the paid path. Research adapters stay outside production rewards unless they have the same deterministic verifier, replay, license, identity, and corpus guarantees.

Production invariants:

1. No paid row without deterministic Lean verifier acceptance.
2. No paid row without replay metadata.
3. No paid task without source and license metadata.
4. No paid task from an unknown or restricted source license.
5. No paid benchmark task.
6. No full production reward under weak proof identity.
7. No accepted corpus row without task id, task version, target hash, verifier version, registry hash context, and validator attribution.
8. No verifier run with network access in production mode.
9. No validator scoring in production mode outside an explicit supply contract: procedural launch supply or experimental ingredient fixture supply.
10. No production procedural supply without a pinned public source pool spanning Mathlib rows and prior accepted Lemma rows.
11. No production paid reward without live miner hotkey authentication.
12. No production paid reward without commit/reveal fields on revealed submissions.
13. No paid launch task unless supply is procedural depth-2 and generated from chain/drand epoch randomness.
14. No paid production task without Lean-backed Prop, kernel-canonical novelty, typecheck, triviality, baseline, and source-oracle gates.
15. No rewarded production proof without verifier-recorded Lean kernel dependencies driving the paid slot-weight receipt.
16. No paid production task without a recomputable `T(t)` retarget receipt from public burn history.
17. No paid production task without a chain-pinned operator-bundle version, operator-bundle hash, and two drand-keyed mutation steps with params plus input/output statement hashes.
18. No paid production task without a public novelty-cache receipt.
19. No paid production task without a source-pool receipt covering source counts, stream counts, alpha, and cap.
20. No production paid reward under weak proof identity.
21. No production settlement without canonical active-pool and accepted-entry digests bound into the tempo commitment payload.

`LEMMA_PROTOCOL_MODE=production` fails closed if the active configuration violates the launch boundary: enabled domains must be exactly `lean`; launch task supply must be procedural; the public source pool must be SHA-pinned and backed by an explicit prior-substrate mirror; paid launch tasks must be procedural depth-2, generated from chain/drand epoch randomness by the chain-pinned mutation engine, stamped with drand-keyed mutation params, stamped by the `lean` generation gate runner, and carrying source-pool, public novelty-cache, Lean-elaborated kernel-normal `kernel_canonical_hash`, source-oracle/import-hygiene, and `T(t)` receipts that recompute from public inputs; live miner authentication and commit/reveal fields must be required; strong proof identity must be required for reward; and the Lean verifier network mode must be disabled. Production submissions must arrive through the bucket path with a matching miner chain commitment plus commit/reveal metadata. Rewarded slot weights are recomputed from the Lean verifier's recorded kernel dependencies for the accepted proof. Each validator pass emits canonical active-pool and accepted-entry artifacts plus a compact tempo commitment payload. Registry files can still be published as caches, but launch validators rebuild the active task set from public inputs.

`LEMMA_TASK_SUPPLY_MODE=ingredient` is an experimental fixture-gated supply contract for the ingredient subnet redesign. It is not the launch/operator path. It requires a current active-registry cache whose task count matches effective `LEMMA_ACTIVE_K`, schema version 1 with no unknown top-level registry fields and no local registry side channel, supplied selection/validation registry envelopes matching that cache, a recognized registry signature status, no unverified registry signature metadata, verified registry signatures carrying paired trimmed metadata, and a non-placeholder registry digest, a pinned full-schema ingredient manifest SHA with no unknown manifest fields, matching corpus snapshot hash, exact trimmed Mathlib commit, recipe bundle hash, and non-placeholder ingredient/graph/policy hashes, exact trimmed ingredient repository commit, a nonempty canonical public difficulty-state JSONL file with sorted unique exact `tempo`/`difficulty_lane` rows and exactly one row for the active tempo/lane, reward-eligible `source_stream=ingredient` tasks, task version 1, source license `Apache-2.0`, fixture triviality classification, narrow ingredient source refs with no URL or path side channel, no task lifecycle window, local timing/randomness metadata, metadata-shadow side channel, or unknown ingredient metadata fields, the fixture Lean verifier identity, toolchain, import envelope, theorem header, submission stub, non-placeholder gate and shortcut receipts, the `restricted_helpers` submission policy, and receipt metadata matching those pins with exact integer fields. Validators must be able to recompute the nonempty, trimmed, duplicate-free raw ingredient selection with canonical public selected selector, recipe, definition, fact, and bridge IDs plus an exact selected-parameter map, ingredient count, fixture hidden-lemma count, novelty-family hash, active target hash, generation receipt hash including active task id and corpus snapshot hash, and reject task metadata that does not match the current tempo, active K, difficulty lane, selected selector, selected recipe, definitions, facts, bridges, parameters, selection seed, theorem statement hash, novelty-family hash, receipt hash. Optional envelope quorum evidence is verified from generation-receipt envelopes with distinct signer metadata, not from task metadata.

For ingredient active K greater than one, the active registry must cover exactly the public queue-position slots `0..K-1`. Selection replay uses a slot seed derived from the public challenge seed, queue position, and active K; duplicate slot selection seeds are rejected.

Explicit active-registry files must be regular files, active-registry cache directories must be concrete directories, and cache entries must be regular files before runtime registry bytes are parsed; symlinked registry paths are never a valid active-registry source.
Operator cache inspection uses the same active-registry cache reader instead of a separate local file reader.
Local task-registry URL/file inputs must also be regular files before shared task registry bytes are parsed; HTTP(S) registry fetching remains unchanged.

Ingredient task construction and verification use the same fixed public envelope values for source license, Lean toolchain, and submission policy; task constructors cannot emit alternate values and then rely on a later verifier to catch them.

Selection receipt selected selector and recipe IDs must be canonical public labels, and selected definition, fact, and bridge ID arrays must be duplicate-free before receipt hashing.

Statement-gate and shortcut-gate receipt details must repeat the selected selector ID from the selection receipt, so receipt artifacts expose the selector path they bind instead of relying on task metadata alone.
Task artifact manifests must also report the active target hash, theorem statement hash, selected selector and recipe IDs, selected-parameter hash, theorem-type hash, novelty-family hash, corpus snapshot hash, ingredient repository commit, Mathlib commit, and recipe bundle hash from the task, manifest, selection receipt, and generation receipt, so bundle manifests name the replayed task identity and provenance without requiring private context. Public task build and verification summaries must report the selected selector and recipe IDs, and task build/bundle summaries must echo the manifest-bound provenance pins.

Ingredient active task IDs in generation receipts, gate receipts, and task artifact manifests must live under the `lemma.ingredient.*` public namespace.

Ingredient fact IDs are one public namespace across `facts.jsonl`, `source_theorems.jsonl`, and `source_lemmas.jsonl`; cross-catalog duplicates fail root inspection and shortcut receipt replay.

Fact rows marked `metadata.usable_as_source_fact=false` are not eligible for deterministic raw-ingredient selection.

Fact metadata fields `statement_family`, `topic`, and `subtopic` must be canonical public labels.

Fact dependency metadata `direct_dependency_count` and `dependency_depth` must be exact nonnegative integers when present.

Raw ingredient and generated task import arrays must be duplicate-free, Mathlib-only, and sorted before hashing.
Public ingredient JSONL component rows must be sorted by each component's public row ID before hashing.
Public ingredient manifest inputs must be regular files before their canonical bytes are parsed.
Public ingredient repository roots must be concrete directories before root-relative component, report, recipe, selection, or receipt evidence is read.
Ingredient repository output roots must be absent or concrete directories before extraction or compatibility scaffold writers create public root-relative artifacts.
Ingredient repository writers reject symlinked or non-regular root-relative artifact paths before writing or preserving public artifacts.
Standalone public ingredient task inputs must be regular files before their canonical bytes are parsed.
Production ingredient invariant settings for the public manifest and difficulty-state JSONL must be regular files before runtime replay parses their bytes.
The public root `mathlib_commit.txt` provenance pin must be a regular file before its commit token is accepted.
Public ingredient manifest component files must be regular files before their hashes or schema rows are accepted.
Public recipe artifact JSON files and declared soundness-template Lean files must be regular files before their hashes or selection inputs are accepted.
Statement-gate receipt construction rechecks the selected soundness template as a regular file before binding its hash.
Public repository report JSON files must be regular files before their hashes or count claims are accepted.

Production ingredient invariant checks use the same public import-envelope rule, so valid generated tasks may import `Mathlib.*` modules beyond the root `Mathlib` import when the sorted envelope and task hashes agree.
Generated ingredient theorem type expressions must be canonical single-line expressions with no inert whitespace normalization before task hashing or gate receipt construction. Generated ingredient theorem statements must use the exact two-line skeleton `theorem <name> : <type> := by` followed by `  sorry`; alternate whitespace, trailing lines, comments, and extra declarations are invalid.
Task statement-file inputs must be regular files before generated theorem statement bytes are accepted for task, target, and receipt hashing.

Definition rows with `metadata.allowed_recipes` are eligible only for those listed recipes during deterministic raw-ingredient selection.
All public recipe references, including recipe bundle entries, definition `metadata.allowed_recipes`, selector `recipe_ids`, bridge `safe_recipes`, and compatibility-edge `recipe_id`, must be canonical public labels.
Definition `metadata.allowed_recipes` arrays must be sorted before hashing because selection treats them as recipe allow-sets.

Definition metadata `simp_risk`, when present, must be one of `low`, `medium`, or `high`.

Recipe parameter rules `none`, `finite_nat`, `finite_int`, and `finite_bool` are executable. `finite_nat` selects one canonical decimal-string `Nat` value from a public `Nat` set; `finite_int` selects one canonical signed decimal-string `Int` value from a public `Int` set; `finite_bool` selects one public `Bool` value, exactly `true` or `false`. Selection receipts may carry either no selected parameters or one shaped selected parameter: `Nat`, `Int`, or `Bool` with the same canonical value grammar.
Ingredient manifest component/policy hashes, selection receipt hashes, generation receipt hashes, and task artifact manifest seed hashes must not be the all-zero placeholder. Public Mathlib and ingredient repository commit tokens must not be all-zero placeholders.
Challenge seed derivation accepts only exact nonnegative public `netuid`/`tempo` integers, a nonempty trimmed epoch seed, and non-placeholder manifest, recipe-bundle, and difficulty-state hashes.
Generation receipt construction applies the same nonempty trimmed epoch-seed rule before hashing `epoch_seed_sha256`.
Ingredient novelty policy supports theorem-type cache checks and selected-family cache checks in canonical order. Public novelty cache rows must be canonical sorted `statement_hash` or `novelty_family_hash` objects, and statement-gate novelty details must bind both checks when a selected-family hash is present.
Ingredient shortcut policy supports `source_oracle`, `source_subterm_oracle`, `source_numeric_skeleton_oracle`, `source_shape_skeleton_oracle`, `source_token_multiset_oracle`, `simp`, `aesop`, `omega`, and `grind` checks in canonical order. Every paid recipe must declare `source_oracle`; recipe shortcut checks use the same canonical order; recipes that declare `simp`, `aesop`, `omega`, or `grind` require a bounded Lean shortcut-tactic probe, and shortcut receipts must bind exact non-placeholder tactic-probe details.
Bootstrapped paid recipes declare every supported non-tactic source oracle, so the public compatibility writer does not leave semantic shortcut checks optional for generated real recipes.
The public `recipes/recipe_rules.json` artifact has exactly one top-level field, `recipes`, and recipe rows must be ordered by canonical `recipe_id`.
Recipe `soundness_template` paths must be canonical `soundness_templates/<public-label>.lean` paths inside the public recipe artifact tree.
Every soundness template in the public recipe artifact tree must be declared by a recipe.
The soundness-template directory may contain only direct `.lean` template files; nested files and non-Lean files are invalid.
Soundness-template import lines must be duplicate-free, sorted, and Mathlib-only.
Soundness templates must contain at least one top-level `theorem` or `lemma` declaration before they can be hashed, inspected, or typechecked as public soundness artifacts.
Recipe `domains`, `required_ingredient_classes`, `required_definitions`, and `required_fact_kinds` arrays must be sorted before hashing. Domain/class sorting matches set-style compatibility checks; definition/fact-kind sorting removes inert recipe-row hash drift before deterministic selection.
Public parameter-set arrays must also be sorted by their canonical type order before hashing.

Selector `ingredient_filters.max_simp_risk`, when present, excludes recipes whose required definitions exceed that public risk cap; definitions without `simp_risk` count as `high`. Selector `ingredient_filters.min_dependency_depth`, when present, requires selected facts to carry public `metadata.dependency_depth` greater than or equal to that minimum.
Selector `recipe_ids` and `ingredient_filters.domains` arrays must be sorted before hashing because selection hash-orders recipe candidates and treats domain filters as sets.
Quality reports that advertise nonzero `estimated_theorem_space_size` must mark `reserve_selector_health.ready=true`.

Bridge rows are eligible for a safe recipe only when both bridge domains are declared by that recipe.
Bridge `safe_recipes` arrays must be sorted before hashing because bridge safety checks treat them as allow-sets.
Bridge `metadata.meaning`, when present, must be a canonical public label, not free-form text.

Compatibility edges may only declare allowed domains from their recipe domain list and may only reference bridges that are safe for that recipe with domains declared by that recipe.
Compatibility-edge `difficulty_lanes`, `allowed_domains`, `allowed_fact_patterns`, `allowed_definition_ids`, and `bridge_ids` arrays must be sorted before hashing because selection and bridge checks treat them as sets.

Gate receipt `runner` and duplicate-free `checks` are non-placeholder public tokens, not paths or free-form notes. Generation-receipt envelopes must carry a `generation_receipt_sha256` matching the embedded canonical receipt, and envelope signer/signature metadata must be paired non-placeholder public tokens. Statement-gate receipt `checks` must be the canonical ordered executable check groups for the claimed runner and optional soundness, triviality, and novelty gates; passed Lean statement and soundness gates use the canonical success reason `ok`; triviality-gate details must be exact with a non-placeholder probe hash; and novelty-gate details must be exact with non-placeholder cache/statement hashes.
Statement-gate receipt details must explicitly bind the selected parameter map, its canonical hash, and the generated theorem-type hash for the realized task.
Recipe-realized theorem statements must be deterministic functions of the public selected recipe and selected parameters; unsupported recipe realization fails closed before task artifacts are written.
Theorem-dependent gates must follow a successful statement gate; soundness-template, bounded-triviality, novelty-cache, and shortcut-tactic gates cannot be requested without `--run-statement-gate`.
In `LEMMA_PROTOCOL_MODE=production`, `tasks build-ingredient-task` must use the public difficulty-state JSONL and must run the Lean statement gate, soundness-template gate, bounded-triviality gate, and novelty-cache gate before it can emit task artifacts. Public difficulty-state and novelty-cache JSONL inputs must be regular files before replay context is derived.
Production `ingredients verify-bundle` must replay the public difficulty-state JSONL and epoch seed, require the public novelty cache, and reject bundles whose statement-gate receipt lacks the same Lean statement, soundness-template, bounded-triviality, and novelty checks.
Production `ingredients verify-task` must replay the public difficulty-state JSONL, netuid, and epoch seed, and must verify either a public generation receipt artifact or generation-receipt envelope rather than reporting reconstructed task metadata as production envelope evidence. Standalone receipt artifact and envelope inputs must be regular files before parsing.
Bundle verification may combine the bundle's embedded generation-receipt envelope with additional external regular-file generation-receipt envelopes for configured envelope quorum and signature checks.
Task artifact builders must write into an absent or empty concrete output directory. Task bundle roots must be concrete directories, not symlinks. `artifact-manifest.json` and every referenced bundle artifact must resolve to the expected regular files at bundle root; symlinks, non-regular files, and nested artifact paths are rejected even when referenced bytes could match.
Generation receipts, gate receipts, and generation-receipt envelopes reject all-zero hash placeholders in their public hash fields before receipt hashing or verification.

Task artifact manifest refs must be exact role-specific canonical bundle-root filenames with non-placeholder file hashes; nested paths, absolute paths, traversal, wrong root filenames, whitespace variants, and all-zero hash placeholders are invalid before artifact hash verification. Manifest-bound target, statement, selected-parameter, theorem-type, novelty-family, corpus snapshot, recipe bundle, receipt, and state hashes must also be non-placeholder, and manifest-bound provenance commits must be non-placeholder public hex commit tokens.

# Protocol Invariants

These are the production fail-closed rules. The implementation should stay smaller than this document; each rule exists because it protects the live proof competition boundary.

1. Production supply is registry-backed real Lean work: `LEMMA_TASK_SUPPLY_MODE=registry`.
2. The loaded registry bytes must match `LEMMA_TASK_REGISTRY_SHA256_EXPECTED`.
3. Paid tasks must be real missing-proof rows from public source streams: `formal_conjectures`, `sorrydb`, `lean_project`, or reviewed `human_curated` work.
4. Fixed fixtures, baseline-solved tasks, source wrappers, and benchmark-only rows are not paid production supply.
5. Production domains are exactly `lean`; future verifier domains remain research until explicitly activated.
6. Active selection is deterministic from the pinned registry, `LEMMA_ACTIVE_K`, frontier depth, queue seed, public curriculum state, and chain/drand epoch randomness.
7. Production epoch randomness requires `LEMMA_ACTIVE_SEED_MODE=epoch_randomness` and `LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand`.
8. Live miner responses require hotkey-authenticated signatures.
9. Paid production submissions require commit/reveal metadata.
10. Paid rewards require strong Lean-derived proof identity.
11. Lean verifier networking is disabled for production verification.
12. Accepted proof rows must not contain local paths, hostnames, IPs, credentials, environment files, operator notes, or raw private logs.
13. Proof Atlas snapshots contain public accepted proofs, registry caches, exports, canonical tempo artifacts, and manifests only.

## Registry Supply

A production task row must carry a stable task ID, task version, Lean target hash, theorem name, theorem type, source stream, source reference, source license, imports, statement with the target hole, and submission stub with the same target hole.

For `formal_conjectures`, `sorrydb`, and `lean_project` rows, the source reference must include public URL, commit, and path metadata so validators can audit where the task came from. Reviewed `human_curated` rows may omit repository coordinates only when the task source itself is directly public and not a fixed fixture.

Registry signatures are a distribution check. The SHA pin is the byte-level task authority, and production preflight reports signature status separately so an unsigned cache cannot be confused with a verified registry.

## Active Window

Validators select the active set after loading the pinned registry. Registry cache files such as `tempo-<tempo>.registry.json` may speed startup, but they are still checked against the same registry/task contract and active-window settings.

Curriculum retargeting is allowed only from public state. Retarget rows activate after one full tempo of public replay lag, so a private local solve-rate log cannot change the next paid window.

## Submission And Scoring

Submissions bind to `task_id`, `task_version`, `target_sha256`, theorem statement, import envelope, and production submission policy. The verifier rejects changed statements, disallowed imports, `sorry`, `admit`, custom axioms, unsafe code, native execution tricks, and non-production proof identity.

Only the rank-0 unique accepted proof for an active slot earns that slot's deterministic weight. On the bucket path, rank is ordered by miner Merkle-root commit block, extrinsic index, event index, and commitment hash before local receipt time. Unsolved slot weight stays in `unearned_share` and burns by default.

## Operator Boundary

`lemma operator preflight` is the gate before live validation. It checks the pinned registry, active-window shape, real-task production supply, signature policy, epoch-randomness settings, live submission authentication, commit/reveal settings, strong proof identity, and Lean sandbox network mode.

The public validator path is still `lemma validate`. Extra diagnostics and publisher tools are operator plumbing; they must not become hidden task authority.

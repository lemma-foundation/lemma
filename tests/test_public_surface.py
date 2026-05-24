"""Public-surface stale-language checks."""

from __future__ import annotations

from pathlib import Path


def _public_text() -> str:
    paths = [
        Path("README.md"),
        Path("LITEPAPER.md"),
        Path(".env.example"),
        Path("lemma/cli/main.py"),
        Path("examples/operator-smoke/README.md"),
        *sorted(Path("docs").glob("*.md")),
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_env_example_has_no_bounty_or_escrow_keys() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    stale_prefix = "LEMMA_" + "BOUNTY_"
    forbidden = [
        f"{stale_prefix}REGISTRY_URL",
        f"{stale_prefix}REWARD_CUSTODY",
        f"{stale_prefix}EVM_RPC_URL",
        f"{stale_prefix}ESCROW_CONTRACT_ADDRESS",
    ]
    for item in forbidden:
        assert item not in text


def test_final_docs_structure_exists() -> None:
    docs = {path.name for path in Path("docs").glob("*.md")}

    assert docs == {
        "what-is-lemma.md",
        "how-it-works.md",
        "corpus.md",
        "miner.md",
        "validator.md",
        "tasks.md",
        "mathlib-extraction.md",
        "operator-registry-flow.md",
        "mainnet-readiness.md",
        "scoring.md",
        "security-and-gaming.md",
        "architecture.md",
        "cli.md",
        "PROTOCOL_INVARIANTS.md",
        "dependency-graph.md",
        "license-policy.md",
        "proof-identity.md",
        "useful-verified-row.md",
        "production.md",
        "testing.md",
        "faq.md",
    }


def test_public_docs_keep_corpus_and_economics_invariant() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    scoring = Path("docs/scoring.md").read_text(encoding="utf-8")

    assert "Lemma is an open competition for formal proof" in readme
    assert "Agents compete. Lean checks. Verified proofs earn credit." in Path(
        "docs/what-is-lemma.md"
    ).read_text(
        encoding="utf-8"
    )
    assert "weight(miner) = credit(miner) / sum(all_credits)" not in scoring
    assert "previous weights" not in scoring.lower()
    assert "unearned_share = 1.0" in scoring


def test_public_docs_frame_cli_as_reference_client() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    cli = Path("docs/cli.md").read_text(encoding="utf-8")
    miner = Path("docs/miner.md").read_text(encoding="utf-8")
    validator = Path("docs/validator.md").read_text(encoding="utf-8")
    testing = Path("docs/testing.md").read_text(encoding="utf-8")
    public_path = "\n".join([readme, cli, miner, validator, testing])

    assert "The public CLI is a thin reference client" in cli
    assert "not the competitive mining engine" in cli
    assert "Competitive miners can replace the CLI entirely" in miner
    assert "public validator path is the single validation command" in validator
    assert "uv run lemma worker --check" not in public_path


def test_future_domain_docs_are_research_only() -> None:
    assert not Path("docs/domain-adapter-spec.md").exists()
    assert not Path("docs/domains/verus.md").exists()
    research = Path("docs/research/future-verifier-domains.md").read_text(encoding="utf-8")
    assert "This is archived background research, not Lemma's production thesis or roadmap." in research
    assert "Do not use this doc to imply a broader roadmap" in research


def test_readme_does_not_link_background_research() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Background Research:" not in readme
    assert "docs/research/" not in readme


def test_public_surfaces_do_not_reintroduce_legacy_protocol_language() -> None:
    text = _public_text()
    lowered = text.lower()

    forbidden = [
        "sum(all_credits)",
        "previous weights",
        "reasoning_steps",
        "verified reasoning network",
        "verified reasoning data",
        "lemma" + "-cli",
        "openai" + "_api" + "_key",
        "lemma_bounty_",
        "custody " + "system",
        "v1 roadmap",
        "v1 focuses",
        "v1 public thesis",
        "v1 production",
        "v1 scoring",
        "v1 credit",
        "v1 payout",
        "v1 training",
        "v1 public focus",
        "for lemma v1",
        "v1 identity",
        "v1 rewards",
        "spacetime" + "-tao",
        "lemma" + "-wta",
        "lemma_protocol_mode=testnet",
        "before it needs to broaden",
        "not a library like mathlib",
        "open corpus of reusable proof data",
        "added to an open corpus",
        "reusable proof data",
        "corpus is the durable byproduct",
        "the corpus is the product",
    ]
    for fragment in forbidden:
        assert fragment not in lowered

    assert "git clone https://github.com/lemma-foundation/lemma.git" in text
    assert "https://github.com/lemma-foundation/lemma-corpus" in text
    assert "weight = miner_score" in lowered
    assert "validator-runs.jsonl" in text


def test_public_docs_do_not_make_alpha_endorsement_or_payout_claims() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in Path("docs").glob("*.md"))

    assert "is endorsed by Google DeepMind" not in text
    assert "official AlphaProof" not in text
    assert "pays Formal Conjectures" not in text


def test_operator_registry_flow_covers_registry_validation_and_export() -> None:
    text = Path("docs/operator-registry-flow.md").read_text(encoding="utf-8")

    required = [
        "uv run lemma tasks build-mathlib-snapshot",
        "LEMMA_TASK_SUPPLY_MODE=procedural",
        "LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED=<source-pool-sha256>",
        "LEMMA_PROCEDURAL_NOVELTY_CACHE_JSONL=public-entry-cache.jsonl",
        "LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL=public-import-graph.jsonl",
        "LEMMA_ACTIVE_K=10",
        "uv run lemma validate",
        "operator-diagnostics-before.json",
        "operator-diagnostics-after.json",
        "artifact counts for validator runs",
        "validator-runs.jsonl",
        "--bucket-reveals-jsonl bucket-reveals.jsonl",
        "Rank-0 accepted proofs earn their deterministic active slot share",
        "unearned_share",
        "uv run lemma corpus benchmark-export",
        "uv run python scripts/leak_check.py",
    ]
    for fragment in required:
        assert fragment in text

    assert "Payment uses deterministic active slot weights" in text


def test_mainnet_readiness_doc_covers_launch_gates() -> None:
    text = Path("docs/mainnet-readiness.md").read_text(encoding="utf-8")

    required = [
        "uv run python scripts/workstream_audit.py --profile mainnet --skip-site",
        "RUN_DOCKER_LEAN=1",
        "BT_NETWORK=test",
        "BT_NETUID=467",
        "weight-submissions.jsonl",
        "success=true",
        "LEMMA_PROTOCOL_MODE=production",
        "LEMMA_REQUIRE_STRONG_PROOF_IDENTITY=1",
        "LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL=public-import-graph.jsonl",
        "LEAN_SANDBOX_NETWORK=none",
        "Do not commit or publish local notes",
    ]
    for fragment in required:
        assert fragment in text

"""Public-surface stale-language checks."""

from __future__ import annotations

from pathlib import Path


def _public_text() -> str:
    paths = [
        Path("README.md"),
        Path(".env.example"),
        Path("lemma/cli/main.py"),
        Path("examples/operator-smoke/README.md"),
        *sorted(Path("docs").glob("*.md")),
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_env_example_has_no_bounty_or_escrow_keys() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    forbidden = [
        "LEMMA_BOUNTY_REGISTRY_URL",
        "LEMMA_BOUNTY_REWARD_CUSTODY",
        "LEMMA_BOUNTY_EVM_RPC_URL",
        "LEMMA_BOUNTY_ESCROW_CONTRACT_ADDRESS",
    ]
    for item in forbidden:
        assert item not in text


def test_final_docs_structure_exists() -> None:
    docs = {path.name for path in Path("docs").glob("*.md")}

    assert docs == {
        "what-is-lemma.md",
        "open-alphaproof-engine.md",
        "exec-plan-open-alphaproof.md",
        "how-it-works.md",
        "corpus.md",
        "miner.md",
        "validator.md",
        "tasks.md",
        "mathlib-extraction.md",
        "operator-registry-flow.md",
        "scoring.md",
        "security-and-gaming.md",
        "benchmarks.md",
        "formal-conjectures.md",
        "model-apis.md",
        "architecture.md",
        "cli.md",
        "production.md",
        "testing.md",
        "faq.md",
    }


def test_public_docs_keep_corpus_and_economics_invariant() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    scoring = Path("docs/scoring.md").read_text(encoding="utf-8")

    assert "The corpus is the product" in readme
    assert "weight(miner) = credit(miner) / sum(all_credits)" not in scoring
    assert "previous weights" not in scoring.lower()
    assert "unearned_share = 1.0" in scoring


def test_public_surfaces_do_not_reintroduce_legacy_protocol_language() -> None:
    text = _public_text()
    lowered = text.lower()

    forbidden = [
        "sum(all_credits)",
        "previous weights",
        "reasoning_steps",
        "lemma-cli",
        "openai" + "_api" + "_key",
        "lemma_bounty_",
    ]
    for fragment in forbidden:
        assert fragment not in lowered

    assert "weight = credit / k" in lowered
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
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED=<registry_sha256>",
        "LEMMA_ACTIVE_K=10",
        "uv run lemma validate",
        "operator-diagnostics-before.json",
        "operator-diagnostics-after.json",
        "artifact counts for validator runs",
        "validator-runs.jsonl",
        "--submissions-jsonl submissions.jsonl",
        "Accepted unique proofs earn `credit / K`",
        "unearned_share",
        "uv run lemma corpus benchmark-export",
        "uv run python scripts/leak_check.py",
    ]
    for fragment in required:
        assert fragment in text

    assert "must not change the reward denominator" in text

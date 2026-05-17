"""Public-surface stale-language checks."""

from __future__ import annotations

from pathlib import Path


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
        "how-it-works.md",
        "corpus.md",
        "miner.md",
        "validator.md",
        "tasks.md",
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

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
        "overview.md",
        "corpus.md",
        "miner.md",
        "validator.md",
        "task-supply.md",
        "incentives.md",
        "security.md",
        "benchmarks.md",
        "model-api.md",
        "architecture.md",
        "production.md",
        "testing.md",
        "faq.md",
    }

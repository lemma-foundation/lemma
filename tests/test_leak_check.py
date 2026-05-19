"""Leak-check pattern coverage without storing credential-shaped literals."""

from __future__ import annotations

from scripts.leak_check import _patterns, _private_path_label


def _labels(text: str) -> set[str]:
    return {label for label, pattern in _patterns() if pattern.search(text)}


def test_leak_check_recognizes_common_credential_shapes() -> None:
    samples = {
        "openai-key": "sk-" + ("a" * 24),
        "github-credential": "ghp_" + ("a" * 36),
        "slack-credential": "xoxb-" + ("a" * 24),
        "aws-access-credential": "AKIA" + ("0" * 16),
        "google-credential": "AIza" + ("A" * 35),
        "credential-assignment": "api" + "_key = '" + ("A" * 24) + "'",
    }

    for label, sample in samples.items():
        assert label in _labels(sample)


def test_leak_check_allows_placeholder_credential_names() -> None:
    text = "\n".join(
        [
            'LEMMA_PROVER_API_KEY=""',
            "to" + 'ken = "short"',
            "sec" + 'ret = "<set-me>"',
        ]
    )

    assert "credential-assignment" not in _labels(text)


def test_leak_check_blocks_private_operator_paths() -> None:
    blocked = {
        ".env": "env-path",
        ".env.local": "env-path",
        "AGENT" + "_STATE.md": "agent-state-path",
        "notes/agent-state.md": "agent-state-path",
        "wallets/miner/key": "private-operator-path",
        ".bittensor/wallets/miner/key": "private-operator-path",
        ".ssh/id_ed25519": "private-operator-path",
    }
    for path, label in blocked.items():
        assert _private_path_label(path) == label

    assert _private_path_label(".env.example") is None

"""Leak-check pattern coverage without storing credential-shaped literals."""

from __future__ import annotations

from scripts.leak_check import _patterns


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

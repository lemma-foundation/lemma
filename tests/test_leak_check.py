"""Leak-check pattern coverage without storing credential-shaped literals."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import leak_check
from scripts.leak_check import _patterns, _private_path_label

ROOT = Path(__file__).resolve().parents[1]


def _labels(text: str) -> set[str]:
    return {label for label, pattern in _patterns() if pattern.search(text)}


def test_leak_check_recognizes_common_credential_shapes() -> None:
    samples = {
        "agent-state": "agent" + "_state",
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
            "forbidden to" + "ken: recipe_rules:list_length_v1:sorry",
        ]
    )

    assert "credential-assignment" not in _labels(text)


def test_leak_check_allows_lean_projection_chains() -> None:
    address = ".".join(("192", "0", "2", "1"))
    assert "ip-address" in _labels(f"validator mirror {address}")
    assert "ip-address" not in _labels("Primrec fun a => pr a.1 a.2.1 a.2.2.2.1")


def test_leak_check_blocks_private_operator_paths() -> None:
    blocked = {
        ".env": "env-path",
        ".env.local": "env-path",
        ".envrc": "env-path",
        ".envrc.local": "env-path",
        "AGENT" + "_STATE.md": "agent-state-path",
        "notes/" + "agent" + "-state.md": "agent-state-path",
        "wallets/miner/key": "private-operator-path",
        ".bittensor/wallets/miner/key": "private-operator-path",
        ".ssh/id_ed25519": "private-operator-path",
    }
    for path, label in blocked.items():
        assert _private_path_label(path) == label

    assert _private_path_label(".env.example") is None
    assert _private_path_label(".envrc.example") is None


def test_leak_check_scans_explicit_repo_without_absolute_paths(tmp_path: Path) -> None:
    repo = tmp_path / "site"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / ".gitignore").write_text("AGENT" + "_STATE.md\n", encoding="utf-8")
    (repo / "README.md").write_text("Public site\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=repo, check=True, capture_output=True)

    assert leak_check.check_repo(repo) == []

    (repo / "notes.md").write_text("AGENT" + "_STATE\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True, capture_output=True)

    assert any(finding == "site:agent-state:notes.md" for finding in leak_check.check_repo(repo))


def test_leak_check_skips_common_service_account_usernames(monkeypatch) -> None:
    monkeypatch.setattr(leak_check.getpass, "getuser", lambda: "root")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    assert "local-username" not in {label for label, _pattern in leak_check._patterns()}


def test_gitignore_blocks_env_variants_but_keeps_example() -> None:
    private_env_paths = {".env.bak", ".env.miner3", ".envrc", ".envrc.local"}
    ignored = subprocess.run(
        ["git", "check-ignore", *private_env_paths],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    public_example = subprocess.run(
        ["git", "check-ignore", ".env.example"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert ignored.returncode == 0
    assert set(ignored.stdout.splitlines()) == private_env_paths
    assert public_example.returncode == 1

    public_envrc_example = subprocess.run(
        ["git", "check-ignore", ".envrc.example"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert public_envrc_example.returncode == 1

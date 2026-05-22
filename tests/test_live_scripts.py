"""Live bucket wrapper defaults."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bucket_live_wrappers_prefer_process_env() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "lemma-validator-bucket-live").read_text(encoding="utf-8")

    expected = 'export LEMMA_PREFER_PROCESS_ENV="${LEMMA_PREFER_PROCESS_ENV:-1}"'
    assert expected in miner
    assert expected in validator


def test_bucket_miner_defaults_to_local_docker_verify() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")

    assert 'export LEMMA_LEAN_VERIFY_REMOTE_URL="${LEMMA_LEAN_VERIFY_REMOTE_URL:-}"' in miner
    assert "http://localhost:8787" not in miner

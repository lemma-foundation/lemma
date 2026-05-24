"""Live bucket wrapper defaults."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bucket_live_wrappers_prefer_process_env() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "lemma-validator-bucket-live").read_text(encoding="utf-8")
    prebuild = (ROOT / "scripts" / "lemma-active-registry-prebuild").read_text(encoding="utf-8")

    expected = 'export LEMMA_PREFER_PROCESS_ENV="${LEMMA_PREFER_PROCESS_ENV:-1}"'
    assert expected in miner
    assert expected in validator
    assert expected in prebuild


def test_live_wrappers_sync_public_curriculum_state_before_work() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "lemma-validator-bucket-live").read_text(encoding="utf-8")
    prebuild = (ROOT / "scripts" / "lemma-active-registry-prebuild").read_text(encoding="utf-8")
    sync = (ROOT / "scripts" / "lemma-sync-curriculum-state").read_text(encoding="utf-8")

    expected = '"${LEMMA_CURRICULUM_SYNC_BIN:-$script_dir/lemma-sync-curriculum-state}"'
    assert expected in miner
    assert expected in validator
    assert expected in prebuild
    assert "LEMMA_CURRICULUM_STATE_URL" in sync
    assert "LEMMA_CURRICULUM_STATE_JSONL" in sync
    assert 'tmp="${path}.tmp.$$"' in sync


def test_bucket_miner_defaults_to_local_docker_verify() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")

    assert 'export LEMMA_LEAN_VERIFY_REMOTE_URL="${LEMMA_LEAN_VERIFY_REMOTE_URL:-}"' in miner
    assert "http://localhost:8787" not in miner


def test_bucket_miner_prefers_verified_proof_dir_before_hosted_prover() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")

    assert '["/usr/local/bin/lemma-proof-dir-prover", "--resolve", task.id, str(proof_dir)]' in miner
    assert "has_verified_proof = resolved_proof.returncode == 0 and resolved_proof_path.is_file()" in miner
    assert "prover_command = proof_dir_prover if has_verified_proof else configured_prover or None" in miner


def test_bucket_miner_publishes_once_per_chain_tempo() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")

    assert 'last_bucket_tempo = Path(os.environ["LEMMA_OPERATOR_DATA_DIR"]) / "last_bucket_tempo"' in miner
    assert "last_bucket_tempo.read_text(encoding=\"utf-8\").strip() == str(tempo)" in miner
    assert '"reason": f"already published tempo {tempo}"' in miner
    assert 'printf \'%s\\n\' "$tempo" > "$state_dir/last_bucket_tempo"' in miner


def test_bucket_miner_idles_when_active_registry_cache_is_missing() -> None:
    miner = (ROOT / "scripts" / "lemma-miner-once-to-bucket").read_text(encoding="utf-8")

    assert "active registry cache missing or stale" in miner
    assert "effective_settings = curriculum_controlled_settings(settings, tempo=tempo)" in miner
    assert "cached_active_registry_for_tempo(effective_settings, tempo=tempo)" in miner
    assert "return None, str(cache_path), False" in miner
    assert "write_registry(registry.tasks" not in miner


def test_validator_bucket_wrapper_requires_explicit_weight_write_flag() -> None:
    validator = (ROOT / "scripts" / "lemma-validator-bucket-live").read_text(encoding="utf-8")

    assert 'weight_flag="--no-set-weights"' in validator
    assert 'LEMMA_VALIDATOR_SET_WEIGHTS:-0' in validator
    assert '"$weight_flag"' in validator


def test_active_registry_prebuild_wrapper_calls_hidden_cli() -> None:
    prebuild = (ROOT / "scripts" / "lemma-active-registry-prebuild").read_text(encoding="utf-8")

    assert 'workdir="${LEMMA_APP_DIR:-/opt/lemma-sn467/app}"' in prebuild
    assert 'exec "$uv_bin" run lemma tasks prebuild-active-procedural-registry "$@"' in prebuild

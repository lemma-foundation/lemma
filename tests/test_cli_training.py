"""CLI smoke tests for task and submission commands."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from bittensor_wallet import Keypair
from click.testing import CliRunner
from lemma.chain.commitments import ciphertext_sha256, miner_submission_merkle_root
from lemma.chain.miner_buckets import MinerBucketReveal, RevealedBucketBlob
from lemma.cli.main import main
from lemma.corpus import build_corpus_row, write_jsonl
from lemma.lean.sandbox import VerifyResult
from lemma.operator import OperatorDiagnosticsReport, OperatorPreflightReport, OperatorRegistryInspectReport
from lemma.submissions import build_submission, sign_submission
from lemma.supply.controller import CurriculumTempoRecord, append_curriculum_record
from lemma.task_supply import make_task, write_registry


def _true_intro_proof() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem true_intro_sample : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def test_tasks_list_uses_default_registry() -> None:
    result = CliRunner().invoke(main, ["tasks", "list"])

    assert result.exit_code == 0
    assert "lemma.sample.true_intro" in result.output


def test_task_show_aliases_match_goal_language() -> None:
    for args in (["tasks", "show", "lemma.sample.true_intro"], ["task", "show", "lemma.sample.true_intro"]):
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 0
        assert "Submission stub" in result.output


def test_verify_uses_procedural_registry(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    proof = tmp_path / "Proof.lean"
    proof.write_text(_true_intro_proof(), encoding="utf-8")
    task = make_task(
        task_id="lemma.procedural.test",
        title="Procedural test",
        theorem_name="true_intro_sample",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    )
    registry = type("Registry", (), {"get": lambda self, task_id: task})()
    calls = {}

    def fake_registry(settings, *, tempo):  # noqa: ANN001
        calls["mode"] = settings.task_supply_mode
        calls["tempo"] = tempo
        return registry

    def fake_verify(settings, *, verify_timeout_s, problem, proof_script, submission_policy):  # noqa: ANN001
        calls["task_id"] = problem.id
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.validator.current_active_tempo", lambda settings: 17)
    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fake_registry)
    monkeypatch.setattr("lemma.lean.verify_runner.run_lean_verify", fake_verify)

    result = CliRunner().invoke(
        main,
        ["verify", "lemma.procedural.test", "--submission", str(proof)],
        env={"LEMMA_PREFER_PROCESS_ENV": "1", "LEMMA_TASK_SUPPLY_MODE": "procedural"},
    )

    assert result.exit_code == 0, result.output
    assert calls == {"mode": "procedural", "tempo": 17, "task_id": "lemma.procedural.test"}


def test_rebuild_active_procedural_registry_bypasses_active_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    task = make_task(
        task_id="lemma.procedural.rebuilt",
        title="Rebuilt",
        theorem_name="rebuilt",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    )
    calls = {}

    def fake_registry(settings, *, tempo):  # noqa: ANN001
        calls["tempo"] = tempo
        calls["active_registry_json"] = settings.active_registry_json
        calls["active_registry_cache_dir"] = settings.active_registry_cache_dir
        return type("Registry", (), {"tasks": (task,)})()

    monkeypatch.setattr("lemma.validator.current_active_tempo", lambda settings: 19)
    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fake_registry)
    output = tmp_path / "rebuilt.registry.json"

    result = CliRunner().invoke(
        main,
        ["tasks", "rebuild-active-procedural-registry", "--output", str(output)],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_JSON": str(tmp_path / "stale.registry.json"),
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(tmp_path / "cache"),
        },
    )

    assert result.exit_code == 0, result.output
    assert calls == {"tempo": 19, "active_registry_json": None, "active_registry_cache_dir": None}
    assert "lemma.procedural.rebuilt" in output.read_text(encoding="utf-8")


def test_prebuild_active_procedural_registry_writes_configured_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    task = make_task(
        task_id="lemma.procedural.prebuilt",
        title="Prebuilt",
        theorem_name="prebuilt",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    )
    calls = {}

    def fake_registry(settings, *, tempo):  # noqa: ANN001
        calls["tempo"] = tempo
        calls["active_registry_json"] = settings.active_registry_json
        calls["active_registry_cache_dir"] = settings.active_registry_cache_dir
        return type("Registry", (), {"tasks": (task,)})()

    monkeypatch.setattr("lemma.validator.current_active_tempo", lambda settings: 23)
    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fake_registry)
    cache_dir = tmp_path / "cache"

    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(cache_dir),
            "LEMMA_ACTIVE_TEMPO_SOURCE": "chain",
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["built"] is True
    assert payload["tempo"] == 23
    assert calls == {"tempo": 23, "active_registry_json": None, "active_registry_cache_dir": None}
    assert "lemma.procedural.prebuilt" in (cache_dir / "tempo-23.registry.json").read_text(encoding="utf-8")


def test_prebuild_active_procedural_registry_skips_existing_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    task = make_task(
        task_id="lemma.procedural.cached",
        title="Cached",
        theorem_name="cached",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    ).model_copy(update={"frontier_depth": 0})
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    write_registry([task], cache_dir / "tempo-29.registry.json")

    def fail_registry(settings, *, tempo):  # noqa: ANN001
        raise AssertionError("existing cache should not rebuild")

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fail_registry)

    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry", "--tempo", "29"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(cache_dir),
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["built"] is False
    assert payload["tasks"] == 1


def test_prebuild_active_procedural_registry_auditor_mode_refuses_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    def fail_registry(settings, *, tempo):  # noqa: ANN001
        raise AssertionError("auditor mode should not generate")

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fail_registry)

    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry", "--tempo", "30"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_ROLE": "auditor",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(tmp_path / "cache"),
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code != 0
    assert "auditor mode requires a current public/cache registry" in result.output


def test_prebuild_active_procedural_registry_refreshes_stale_frontier_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    old_task = make_task(
        task_id="lemma.procedural.old-frontier",
        title="Old Frontier",
        theorem_name="old_frontier",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    ).model_copy(update={"frontier_depth": 2})
    new_task = make_task(
        task_id="lemma.procedural.new-frontier",
        title="New Frontier",
        theorem_name="new_frontier",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    ).model_copy(update={"frontier_depth": 0})
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "tempo-30.registry.json"
    write_registry([old_task], cache_path)

    def fake_registry(settings, *, tempo):  # noqa: ANN001
        assert tempo == 30
        assert settings.active_registry_json is None
        assert settings.active_registry_cache_dir is None
        return type("Registry", (), {"tasks": (new_task,)})()

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fake_registry)

    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry", "--tempo", "30"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(cache_dir),
            "LEMMA_ACTIVE_K": "1",
            "LEMMA_FRONTIER_DEPTH": "0",
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["built"] is True
    assert "lemma.procedural.new-frontier" in cache_path.read_text(encoding="utf-8")


def test_prebuild_active_procedural_registry_refreshes_stale_curriculum_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    old_task = make_task(
        task_id="lemma.procedural.old",
        title="Old",
        theorem_name="old",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    )
    new_task = make_task(
        task_id="lemma.procedural.new",
        title="New",
        theorem_name="new",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    )
    second_task = make_task(
        task_id="lemma.procedural.second",
        title="Second",
        theorem_name="second",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "tempo-32.registry.json"
    write_registry([old_task], cache_path)
    os.utime(cache_path, (1, 1))
    state_path = tmp_path / "curriculum.jsonl"
    append_curriculum_record(
        state_path,
        CurriculumTempoRecord(
            tempo=30,
            active_K=2,
            frontier_depth=0,
            ema_solve_rate=0.5,
            solved_slots=1,
            parked_task_ids=(),
            action="hold",
            variant_stream_requested=False,
        ),
    )
    os.utime(state_path, (2, 2))

    def fake_registry(settings, *, tempo):  # noqa: ANN001
        assert tempo == 32
        assert settings.active_task_count == 2
        assert settings.active_registry_json is None
        assert settings.active_registry_cache_dir is None
        return type("Registry", (), {"tasks": (new_task, second_task)})()

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fake_registry)

    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry", "--tempo", "32"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(cache_dir),
            "LEMMA_CURRICULUM_RETARGET": "1",
            "LEMMA_CURRICULUM_STATE_JSONL": str(state_path),
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["built"] is True
    assert payload["tasks"] == 2
    assert "lemma.procedural.new" in cache_path.read_text(encoding="utf-8")


def test_prebuild_active_procedural_registry_rechecks_curriculum_after_slow_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    old_task = make_task(
        task_id="lemma.procedural.old-frontier",
        title="Old",
        theorem_name="old_frontier",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    ).model_copy(update={"frontier_depth": 4})
    new_task = make_task(
        task_id="lemma.procedural.new-frontier",
        title="New",
        theorem_name="new_frontier",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    ).model_copy(update={"frontier_depth": 5})
    state_path = tmp_path / "curriculum.jsonl"
    calls: list[int] = []

    def fake_registry(settings, *, tempo):  # noqa: ANN001
        assert tempo == 7
        calls.append(settings.frontier_depth)
        if len(calls) == 1:
            append_curriculum_record(
                state_path,
                CurriculumTempoRecord(
                    tempo=5,
                    active_K=1,
                    frontier_depth=5,
                    ema_solve_rate=0.5,
                    solved_slots=1,
                    parked_task_ids=(),
                    action="advance_frontier",
                    variant_stream_requested=False,
                ),
            )
            return type("Registry", (), {"tasks": (old_task,)})()
        return type("Registry", (), {"tasks": (new_task,)})()

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fake_registry)

    cache_dir = tmp_path / "cache"
    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry", "--tempo", "7"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(cache_dir),
            "LEMMA_ACTIVE_K": "1",
            "LEMMA_FRONTIER_DEPTH": "4",
            "LEMMA_CURRICULUM_RETARGET": "1",
            "LEMMA_CURRICULUM_STATE_JSONL": str(state_path),
        },
    )

    assert result.exit_code == 0, result.output
    assert calls == [4, 5]
    assert "lemma.procedural.new-frontier" in (cache_dir / "tempo-7.registry.json").read_text(encoding="utf-8")


def test_prebuild_active_procedural_registry_keeps_cache_for_inactive_curriculum_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    task = make_task(
        task_id="lemma.procedural.cached",
        title="Cached",
        theorem_name="cached",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
    ).model_copy(update={"frontier_depth": 0})
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "tempo-32.registry.json"
    write_registry([task], cache_path)
    os.utime(cache_path, (1, 1))
    state_path = tmp_path / "curriculum.jsonl"
    append_curriculum_record(
        state_path,
        CurriculumTempoRecord(
            tempo=31,
            active_K=2,
            frontier_depth=0,
            ema_solve_rate=0.5,
            solved_slots=1,
            parked_task_ids=(),
            action="hold",
            variant_stream_requested=False,
        ),
    )
    os.utime(state_path, (2, 2))

    def fail_registry(settings, *, tempo):  # noqa: ANN001
        raise AssertionError("inactive curriculum row should not rebuild")

    monkeypatch.setattr("lemma.validator.task_registry_for_validation", fail_registry)

    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry", "--tempo", "32"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(cache_dir),
            "LEMMA_CURRICULUM_RETARGET": "1",
            "LEMMA_CURRICULUM_STATE_JSONL": str(state_path),
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["built"] is False
    assert payload["tasks"] == 1


def test_prebuild_active_procedural_registry_requires_cache_dir(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry", "--tempo", "1"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_JSON": str(tmp_path / "one.registry.json"),
        },
    )

    assert result.exit_code != 0
    assert "requires LEMMA_ACTIVE_REGISTRY_CACHE_DIR" in result.output


def test_prebuild_active_procedural_registry_requires_explicit_or_chain_tempo(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        ["tasks", "prebuild-active-procedural-registry"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_SUPPLY_MODE": "procedural",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR": str(tmp_path / "cache"),
            "LEMMA_ACTIVE_TEMPO_SOURCE": "wall_clock",
        },
    )

    assert result.exit_code != 0
    assert "requires --tempo unless LEMMA_ACTIVE_TEMPO_SOURCE=chain" in result.output


def test_root_help_exposes_only_barebones_public_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    positions = [result.output.index(name) for name in ["setup", "status", "mine", "validate"]]
    assert positions == sorted(positions)
    for hidden in ["operator", "tasks", "task", "verify", "submit", "corpus", "export-corpus", "worker"]:
        assert f"  {hidden}" not in result.output
    assert "Reference client for Lemma's proof protocol." in result.output
    assert "Examples:" in result.output


def test_setup_writes_operator_settings(tmp_path) -> None:
    output = tmp_path / "lemma-env"

    result = CliRunner().invoke(
        main,
        [
            "setup",
            "--env-file",
            str(output),
            "--task-registry-url",
            "tasks/live.registry.json",
            "--task-registry-sha256",
            "a" * 64,
            "--operator-data-dir",
            "operator-data",
            "--submission-spool-dir",
            "submission-inbox",
            "--active-k",
            "10",
            "--frontier-depth",
            "2",
            "--active-queue-seed",
            "live-seed",
            "--netuid",
            "42",
            "--unearned-policy",
            "hold",
            "--unearned-uid",
            "9",
        ],
    )

    assert result.exit_code == 0, result.output
    text = output.read_text(encoding="utf-8")
    assert 'LEMMA_TASK_REGISTRY_URL="tasks/live.registry.json"' in text
    assert f'LEMMA_TASK_REGISTRY_SHA256_EXPECTED="{"a" * 64}"' in text
    assert 'LEMMA_OPERATOR_DATA_DIR="operator-data"' in text
    assert 'LEMMA_SUBMISSION_SPOOL_DIR="submission-inbox"' in text
    assert 'LEMMA_ACTIVE_K="10"' in text
    assert 'LEMMA_FRONTIER_DEPTH="2"' in text
    assert 'LEMMA_ACTIVE_QUEUE_SEED="live-seed"' in text
    assert 'BT_NETUID="42"' in text
    assert 'LEMMA_UNEARNED_ALLOCATION_POLICY="hold"' in text
    assert 'LEMMA_UNEARNED_UID="9"' in text


def test_submit_writes_task_bound_package(tmp_path) -> None:
    proof = tmp_path / "Submission.lean"
    proof.write_text(_true_intro_proof(), encoding="utf-8")
    output = tmp_path / "submission.json"

    result = CliRunner().invoke(
        main,
        [
            "submit",
            "lemma.sample.true_intro",
            "--submission",
            str(proof),
            "--solver-hotkey",
            "hk1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert '"task_id": "lemma.sample.true_intro"' in text
    assert '"target_sha256":' in text


def test_mine_once_with_fake_prover(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    prover = tmp_path / "prover.py"
    prover.write_text(
        "import json, sys\n"
        "task = json.load(sys.stdin)\n"
        "print(json.dumps({'task_id': task['task_id'], 'proof_script': " + repr(_true_intro_proof()) + "}))\n",
        encoding="utf-8",
    )
    output = tmp_path / "submission.json"
    monkeypatch.setenv("LEMMA_OPERATOR_DATA_DIR", str(tmp_path / "operator"))

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    result = CliRunner().invoke(
        main,
        [
            "mine",
            "--once",
            "--task-id",
            "lemma.sample.true_intro",
            "--prover-command",
            f"{sys.executable} {prover}",
            "--solver-hotkey",
            "hk1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"task_id": "lemma.sample.true_intro"' in output.read_text(encoding="utf-8")


def test_mine_once_accepts_registry_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    task = make_task(
        task_id="lemma.test.cached_true",
        title="Cached True",
        theorem_name="cached_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    registry = tmp_path / "registry.json"
    write_registry([task], registry)
    prover = tmp_path / "prover.py"
    prover.write_text(
        "import json, sys\n"
        "task = json.load(sys.stdin)\n"
        "print(json.dumps({'task_id': task['task_id'], 'proof_script': " + repr(_true_intro_proof()) + "}))\n",
        encoding="utf-8",
    )
    output = tmp_path / "submission.json"
    monkeypatch.setenv("LEMMA_OPERATOR_DATA_DIR", str(tmp_path / "operator"))

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    result = CliRunner().invoke(
        main,
        [
            "mine",
            "--once",
            "--registry",
            str(registry),
            "--task-id",
            task.id,
            "--prover-command",
            f"{sys.executable} {prover}",
            "--solver-hotkey",
            "hk1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["task_id"] == task.id


def test_mine_once_signs_with_configured_wallet(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    keypair = Keypair.create_from_uri("//LemmaCliMiner")
    prover = tmp_path / "prover.py"
    prover.write_text(
        "import json, sys\n"
        "task = json.load(sys.stdin)\n"
        "print(json.dumps({'task_id': task['task_id'], 'proof_script': " + repr(_true_intro_proof()) + "}))\n",
        encoding="utf-8",
    )
    output = tmp_path / "submission.json"
    monkeypatch.setenv("LEMMA_OPERATOR_DATA_DIR", str(tmp_path / "operator"))
    monkeypatch.setattr(
        "lemma.miner.sign_submission_with_wallet",
        lambda _settings, submission: sign_submission(
            submission.model_copy(update={"solver_hotkey": keypair.ss58_address}),
            keypair,
        ),
    )

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    result = CliRunner().invoke(
        main,
        [
            "mine",
            "--once",
            "--sign",
            "--task-id",
            "lemma.sample.true_intro",
            "--prover-command",
            f"{sys.executable} {prover}",
            "--output",
            str(output),
        ],
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result.exit_code == 0, result.output
    assert payload["solver_hotkey"] == keypair.ss58_address
    assert payload["signature"].startswith("0x")


def test_validate_once_no_set_weights() -> None:
    result = CliRunner().invoke(main, ["validate", "--once", "--no-set-weights"])

    assert result.exit_code == 0
    assert '"scores": {}' in result.output
    assert '"weights_set": false' in result.output


def test_validate_set_weights_requires_enable_flag() -> None:
    result = CliRunner().invoke(main, ["validate", "--once", "--set-weights"])

    assert result.exit_code == 1
    assert "LEMMA_ENABLE_SET_WEIGHTS=1" in result.output


def test_export_corpus_defaults_to_lean(tmp_path) -> None:
    output = tmp_path / "lean_corpus.jsonl"
    result = CliRunner().invoke(
        main,
        ["export-corpus", "--out", str(output)],
        env={"LEMMA_PREFER_PROCESS_ENV": "1", "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "empty-corpus")},
    )

    assert result.exit_code == 0, result.output
    assert "0 lean rows" in result.output
    assert output.read_text(encoding="utf-8") == ""


def test_validate_consumes_submission_spool(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    task = make_task(
        task_id="lemma.sample.true_intro",
        title="Smoke-test True",
        theorem_name="true_intro_sample",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    registry_path = tmp_path / "registry.json"
    write_registry([task], registry_path)
    submission = build_submission(
        task,
        solver_hotkey="hk-spool",
        proof_script=_true_intro_proof(),
    )
    (spool / "submission.json").write_text(submission.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)
    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_SUBMISSION_SPOOL_DIR": str(spool),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_ACTIVE_K": "3",
    }

    first = CliRunner().invoke(main, ["validate", "--once", "--no-set-weights"], env=env)

    assert first.exit_code == 0, first.output
    payload = json.loads(first.output)
    assert payload["verified"] == 1
    assert payload["accepted_unique"] == 1
    assert payload["submission_files_consumed"] == 1
    assert not (spool / "submission.json").exists()
    assert len(list((spool / "processed").glob("*.json"))) == 1

    second = CliRunner().invoke(main, ["validate", "--once", "--no-set-weights"], env=env)

    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["verified"] == 0


def test_validate_consumes_latest_bucket_reveal_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    inbox = tmp_path / "bucket-reveals"
    inbox.mkdir()
    task = make_task(
        task_id="lemma.sample.true_intro",
        title="Smoke-test True",
        theorem_name="true_intro_sample",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    registry_path = tmp_path / "registry.json"
    write_registry([task], registry_path)
    proof = _true_intro_proof()

    def reveal(name: str, *, tempo: int) -> None:
        ciphertext = f"{name}-cipher"
        row = MinerBucketReveal(
            tempo=tempo,
            miner_hotkey=f"hk-{name}",
            drand_round=77,
            drand_signature="0xsig",
            commit_block=tempo,
            commit_extrinsic_hash=f"0x{name}",
            merkle_root=miner_submission_merkle_root(
                ((0, ciphertext_sha256(ciphertext.encode("utf-8"))),)
            ),
            blobs=(RevealedBucketBlob(slot_index=0, ciphertext=ciphertext, proof_script=proof),),
        )
        (inbox / f"{name}.json").write_text(row.model_dump_json() + "\n", encoding="utf-8")

    reveal("old", tempo=6)
    reveal("latest", tempo=7)
    (inbox / "bad.json").write_text("{not json}\n", encoding="utf-8")

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)
    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_TASK_REGISTRY_URL": str(registry_path),
        "LEMMA_ACTIVE_K": "1",
    }

    result = CliRunner().invoke(
        main,
        ["validate", "--once", "--bucket-reveals-dir", str(inbox), "--no-set-weights"],
        env=env,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verified"] == 1
    assert payload["accepted_unique"] == 1
    assert payload["bucket_reveals_consumed"] == 1
    assert payload["bucket_reveals_rejected"] == 1
    assert payload["scores"] == {"hk-latest": 1.0}
    assert (inbox / "processed" / "latest.json").exists()
    assert (inbox / "stale" / "old.json").exists()
    assert (inbox / "rejected" / "bad.json").exists()

    second = CliRunner().invoke(
        main,
        ["validate", "--once", "--bucket-reveals-dir", str(inbox), "--no-set-weights"],
        env=env,
    )

    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["verified"] == 0


def test_production_bucket_reveals_wait_for_completed_chain_tempo(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    inbox = tmp_path / "bucket-reveals"
    inbox.mkdir()
    task = make_task(
        task_id="lemma.sample.true_intro",
        title="Smoke-test True",
        theorem_name="true_intro_sample",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    registry_path = tmp_path / "registry.json"
    write_registry([task], registry_path)
    ciphertext = "current-cipher"
    reveal = MinerBucketReveal(
        tempo=7,
        miner_hotkey="hk-current",
        drand_round=77,
        drand_signature="0xsig",
        commit_block=7,
        commit_extrinsic_hash="0xcurrent",
        merkle_root=miner_submission_merkle_root(((0, ciphertext_sha256(ciphertext.encode("utf-8"))),)),
        blobs=(RevealedBucketBlob(slot_index=0, ciphertext=ciphertext, proof_script=_true_intro_proof()),),
    )
    reveal_path = inbox / "current.json"
    reveal_path.write_text(reveal.model_dump_json() + "\n", encoding="utf-8")
    monkeypatch.setattr("lemma.validator.current_active_tempo", lambda settings: 7)

    result = CliRunner().invoke(
        main,
        ["validate", "--once", "--bucket-reveals-dir", str(inbox), "--no-set-weights"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_PROTOCOL_MODE": "production",
            "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
            "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_ACTIVE_K": "1",
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["reason"] == "bucket reveal tempo is not complete"
    assert payload["bucket_reveals_consumed"] == 0
    assert payload["bucket_tempo"] == 7
    assert reveal_path.exists()
    assert not (inbox / "processed").exists()


def _write_preflight_registry(tmp_path, *, task_count: int = 2) -> tuple[str, str]:
    tasks = [
        make_task(
            task_id=f"lemma.test.preflight_{idx}",
            title=f"Preflight {idx}",
            theorem_name=f"preflight_true_{idx}",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=0,
        )
        for idx in range(task_count)
    ]
    path = tmp_path / "registry.json"
    write_registry(tasks, path)
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def test_operator_preflight_passes_with_pinned_registry(tmp_path) -> None:
    registry_url, registry_sha256 = _write_preflight_registry(tmp_path)

    result = CliRunner().invoke(
        main,
        ["operator", "preflight"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": registry_url,
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
            "LEMMA_ACTIVE_K": "2",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_ACTIVE_QUEUE_SEED": "pytest-preflight",
            "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
            "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        },
    )

    assert result.exit_code == 0, result.output
    payload = OperatorPreflightReport.model_validate_json(result.output)
    checks = {check.name: check for check in payload.checks}
    assert payload.ok is True
    assert payload.registry_sha256 == registry_sha256
    assert checks["registry_hash_pin"].ok is True
    assert checks["active_window"].detail.startswith("2 active / K=2")
    assert (tmp_path / "corpus").is_dir()
    assert (tmp_path / "operator").is_dir()


def test_operator_preflight_fails_without_registry_pin(tmp_path) -> None:
    registry_url, _ = _write_preflight_registry(tmp_path, task_count=1)

    result = CliRunner().invoke(
        main,
        ["operator", "preflight"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": registry_url,
            "LEMMA_ACTIVE_K": "1",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
            "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        },
    )

    assert result.exit_code == 1, result.output
    payload = OperatorPreflightReport.model_validate_json(result.output)
    checks = {check.name: check for check in payload.checks}
    assert payload.ok is False
    assert checks["registry_hash_pin"].ok is False


def test_operator_diagnostics_writes_public_safe_report(tmp_path) -> None:
    registry_url, registry_sha256 = _write_preflight_registry(tmp_path)
    output_path = tmp_path / "diagnostics" / "operator.json"
    operator_dir = tmp_path / "operator"
    corpus_dir = tmp_path / "corpus"
    operator_dir.mkdir()
    corpus_dir.mkdir()
    (operator_dir / "validator-runs.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (operator_dir / "verification-records.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (operator_dir / "score-events.jsonl").write_text("{}\n", encoding="utf-8")
    (corpus_dir / "epoch-000001.jsonl").write_text("{}\n{}\n{}\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["operator", "diagnostics", "--output", str(output_path)],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": registry_url,
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
            "LEMMA_ACTIVE_K": "2",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_ACTIVE_QUEUE_SEED": "pytest-preflight",
            "LEMMA_CORPUS_OUTPUT_DIR": str(corpus_dir),
            "LEMMA_OPERATOR_DATA_DIR": str(operator_dir),
        },
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    payload_text = output_path.read_text(encoding="utf-8")
    payload = OperatorDiagnosticsReport.model_validate_json(payload_text)
    checks = {check.name: check for check in payload.preflight.checks}
    assert summary["active_task_count"] == 2
    assert summary["eligible_task_count"] == 2
    assert summary["parked_task_count"] == 0
    assert summary["waiting_task_count"] == 0
    assert summary["verification_record_count"] == 2
    assert summary["score_event_count"] == 1
    assert summary["corpus_row_count"] == 3
    assert summary["validator_run_count"] == 2
    assert summary["curriculum_can_increase_K"] is False
    assert summary["curriculum_latest_tempo"] is None
    assert summary["validator_capacity"] == 0
    assert payload.preflight.ok is True
    assert payload.registry_sha256 == registry_sha256
    assert payload.curriculum.enabled is False
    assert payload.curriculum.current_active_K == 2
    assert payload.artifacts.validator_run_count == 2
    assert payload.artifacts.verification_record_count == 2
    assert payload.artifacts.score_event_count == 1
    assert payload.artifacts.corpus_jsonl_file_count == 1
    assert payload.artifacts.corpus_row_count == 3
    assert payload.registry_inspect is not None
    assert payload.registry_inspect.registry_sha256 == registry_sha256
    assert payload.registry_inspect.active_task_count == 2
    assert payload.registry_inspect.eligible_task_count == 2
    assert payload.registry_inspect.parked_task_count == 0
    assert set(payload.active_task_ids) == {"lemma.test.preflight_0", "lemma.test.preflight_1"}
    assert checks["corpus_output_dir"].detail == "ready"
    assert checks["operator_data_dir"].detail == "ready"
    assert checks["curriculum_controller"].detail == "retarget disabled"
    assert str(tmp_path) not in payload_text
    assert "LEMMA_TASK_REGISTRY_URL" not in payload_text


def test_operator_registry_inspect_counts_active_waiting_and_parked(tmp_path) -> None:
    tasks = [
        make_task(
            task_id=f"lemma.test.inspect_{idx}",
            title=f"Inspect {idx}",
            theorem_name=f"inspect_true_{idx}",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=queue_depth,
        )
        for idx, queue_depth in enumerate((0, 0, 0, 2))
    ]
    registry_path = tmp_path / "registry.json"
    write_registry(tasks, registry_path)
    registry_sha256 = hashlib.sha256(registry_path.read_bytes()).hexdigest()

    result = CliRunner().invoke(
        main,
        ["operator", "registry-inspect"],
        env={
            "LEMMA_PREFER_PROCESS_ENV": "1",
            "LEMMA_TASK_REGISTRY_URL": str(registry_path),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
            "LEMMA_ACTIVE_K": "2",
            "LEMMA_FRONTIER_DEPTH": "0",
            "LEMMA_ACTIVE_QUEUE_SEED": "pytest-inspect",
        },
    )

    assert result.exit_code == 0, result.output
    payload = OperatorRegistryInspectReport.model_validate_json(result.output)
    assert payload.registry_sha256 == registry_sha256
    assert payload.total_task_count == 4
    assert payload.active_task_count == 2
    assert payload.eligible_task_count == 3
    assert payload.waiting_task_count == 1
    assert payload.parked_task_count == 1
    assert payload.max_queue_depth == 2
    assert payload.queue_depth_counts == {"0": 3, "2": 1}


def test_operator_preflight_flags_shallow_source_for_requested_frontier(tmp_path) -> None:
    from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
    from lemma.supply.procedural import source_pool_hash

    runner = CliRunner()
    fixture_dir = Path("examples/operator-smoke")
    snapshot_path = fixture_dir / "snapshot.jsonl"
    registry_path = tmp_path / "tasks" / "registry.json"
    build = runner.invoke(
        main,
        [
            "tasks",
            "build-mathlib-snapshot",
            "--input",
            str(snapshot_path),
            "--output",
            str(registry_path),
            "--seed",
            "operator-smoke",
            "--frontier-depth",
            "7",
        ],
        env={"LEMMA_PREFER_PROCESS_ENV": "1"},
    )
    registry_sha256 = json.loads(build.output)["registry_sha256"]
    source_hash = source_pool_hash(mathlib_candidates_from_jsonl(snapshot_path))
    env = {
        "LEMMA_PREFER_PROCESS_ENV": "1",
        "LEMMA_TASK_SUPPLY_MODE": "procedural",
        "LEMMA_ACTIVE_REGISTRY_JSON": str(registry_path),
        "LEMMA_PROCEDURAL_SOURCE_JSONL": str(snapshot_path),
        "LEMMA_PROCEDURAL_SOURCE_SHA256_EXPECTED": source_hash,
        "LEMMA_TASK_REGISTRY_SHA256_EXPECTED": registry_sha256,
        "LEMMA_ACTIVE_K": "1",
        "LEMMA_FRONTIER_DEPTH": "7",
        "LEMMA_ACTIVE_QUEUE_SEED": "operator-smoke",
        "LEMMA_CORPUS_OUTPUT_DIR": str(tmp_path / "corpus"),
        "LEMMA_OPERATOR_DATA_DIR": str(tmp_path / "operator"),
        "LEMMA_USE_DOCKER": "0",
        "LEMMA_ALLOW_HOST_LEAN": "1",
    }

    preflight = runner.invoke(main, ["operator", "preflight"], env=env)

    assert preflight.exit_code == 1
    payload = OperatorPreflightReport.model_validate_json(preflight.output)
    checks = {check.name: check for check in payload.checks}
    assert checks["source_snapshot"].ok is False
    assert "max_depth=2" in checks["source_snapshot"].detail


def test_tasks_build_mathlib_snapshot_writes_pinned_registry(tmp_path) -> None:
    manifest = tmp_path / "snapshot.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "theorem_name": "True.intro",
                "type_expr": "True",
                "mathlib_rev": "abc123",
                "source_path": "Mathlib/Init.lean",
                "source_license": "Apache-2.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "registry.json"

    result = CliRunner().invoke(
        main,
        [
            "tasks",
            "build-mathlib-snapshot",
            "--input",
            str(manifest),
            "--output",
            str(output),
            "--frontier-depth",
            "4",
            "--signed-by",
            "fixture-signer",
            "--signature",
            "fixture-signature",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["registry_sha256"]
    assert payload["tasks"] == 1
    from lemma.tasks import load_task_registry

    loaded = load_task_registry(output.read_bytes(), payload["registry_sha256"])
    assert loaded.sha256 == payload["registry_sha256"]
    registry = json.loads(output.read_text(encoding="utf-8"))
    task = registry["tasks"][0]
    assert registry["signed_by"] == "fixture-signer"
    assert registry["signature"] == "fixture-signature"
    assert task["source_stream"] == "mathlib_snapshot"
    assert task["queue_position"] == 0
    assert task["frontier_depth"] == 4


def test_tasks_inspect_mathlib_snapshot_reports_source_quality(tmp_path) -> None:
    manifest = tmp_path / "snapshot.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "theorem_name": "Frontier.target",
                "type_expr": "True",
                "mathlib_rev": "abc123",
                "source_path": "Mathlib/Frontier.lean",
                "source_license": "Apache-2.0",
                "queue_depth": 7,
                "difficulty_score": 9,
                "citation_weight": 12,
                "direct_dependency_count": 8,
                "dependency_depth": 14,
                "transitive_dependency_hash": "b" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["tasks", "inspect-mathlib-snapshot", "--input", str(manifest)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["frontier_rows"] == 1
    assert payload["metadata_coverage"]["dependency_depth"] == 1
    assert payload["max_signal"]["citation_weight"] == 12.0


def test_corpus_benchmark_export_cli_writes_jsonl_and_index(tmp_path) -> None:
    task = make_task(
        task_id="lemma.test.cli_benchmark",
        title="CLI benchmark",
        theorem_name="cli_benchmark_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    proof = "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem cli_benchmark_true : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )
    row = build_corpus_row(
        task,
        build_submission(task, solver_hotkey="hk1", proof_script=proof, created_at="2026-01-01T00:00:00Z"),
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk1",
        rewarded=True,
        accepted_at="2026-01-01T00:00:01Z",
    )
    corpus_dir = tmp_path / "corpus"
    output = tmp_path / "export" / "proofs.jsonl"
    index = tmp_path / "export" / "index.json"
    write_jsonl([row], corpus_dir / "epoch-1.jsonl")

    result = CliRunner().invoke(
        main,
        ["corpus", "benchmark-export", "--input", str(corpus_dir), "--output", str(output), "--index", str(index)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["row_count"] == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["task"]["id"] == "lemma.test.cli_benchmark"
    assert json.loads(index.read_text(encoding="utf-8"))["export"]["path"] == "proofs.jsonl"

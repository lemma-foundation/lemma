from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from lemma.common.config import LemmaSettings
from lemma.current_problem_server import CurrentProblemService
from lemma.current_problems import build_current_problems_snapshot, write_current_problems_snapshot
from lemma.supply.controller import CurriculumTempoRecord, append_curriculum_record
from lemma.task_supply import make_task
from lemma.tasks import TaskRegistry

ROOT = Path(__file__).resolve().parents[1]


def _registry() -> TaskRegistry:
    tasks = (
        make_task(
            task_id="lemma.test.alpha",
            title="Alpha",
            theorem_name="alpha",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=0,
        ),
        make_task(
            task_id="lemma.test.beta",
            title="Beta",
            theorem_name="beta",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=0,
        ).model_copy(update={"difficulty_band": "medium"}),
        make_task(
            task_id="lemma.test.parked",
            title="Parked",
            theorem_name="parked",
            type_expr="True",
            source_stream="human_curated",
            source_name="pytest",
            queue_depth=2,
        ),
    )
    return TaskRegistry(schema_version=1, tasks=tasks, sha256="a" * 64)


def test_current_problem_snapshot_is_public_safe() -> None:
    settings = LemmaSettings(active_task_count=2, frontier_depth=0, active_queue_seed="pytest")

    snapshot = build_current_problems_snapshot(
        settings,
        registry=_registry(),
        generated_at="2026-05-20T00:00:00Z",
        tempo=0,
    )
    payload = snapshot.model_dump(mode="json", exclude_none=True)
    text = json.dumps(payload, sort_keys=True)

    assert payload["schema_version"] == 1
    assert payload["registry_sha256"] == "a" * 64
    assert payload["registry_task_count"] == 3
    assert payload["active_K"] == 2
    assert payload["tempo"] == 0
    assert payload["active_tempo_source"] == "wall_clock"
    assert payload["active_tempo_seconds"] == 4320
    assert payload["task_count"] == 2
    assert {task["task_id"] for task in payload["tasks"]} == {"lemma.test.alpha", "lemma.test.beta"}
    assert "proof_script" not in text
    assert "signature" not in text
    assert "submission_stub" not in text


def test_write_current_problem_snapshot(tmp_path: Path) -> None:
    settings = LemmaSettings(active_task_count=1, frontier_depth=0, active_queue_seed="pytest")
    output = tmp_path / "data" / "current-problems.json"
    snapshot = build_current_problems_snapshot(
        settings,
        registry=_registry(),
        generated_at="2026-05-20T00:00:00Z",
        tempo=0,
    )

    write_current_problems_snapshot(output, snapshot)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["task_count"] == 1
    assert payload["tasks"][0]["queue_depth"] == 0


def test_current_problem_snapshot_rotates_by_tempo() -> None:
    settings = LemmaSettings(active_task_count=1, frontier_depth=0, active_queue_seed="pytest")

    first = build_current_problems_snapshot(settings, registry=_registry(), tempo=0)
    second = build_current_problems_snapshot(settings, registry=_registry(), tempo=1)

    assert first.tasks[0].task_id != second.tasks[0].task_id


def test_current_problem_snapshot_reports_curriculum_effective_window(tmp_path: Path) -> None:
    curriculum = tmp_path / "curriculum.jsonl"
    append_curriculum_record(
        curriculum,
        CurriculumTempoRecord(
            tempo=1,
            active_K=1,
            frontier_depth=2,
            ema_solve_rate=0.58,
            solved_slots=1,
            parked_task_ids=(),
            action="hold",
            variant_stream_requested=False,
        ),
    )
    settings = LemmaSettings(
        active_task_count=20,
        active_tempo_source="chain",
        frontier_depth=0,
        active_queue_seed="pytest",
        curriculum_retarget_enabled=True,
        curriculum_state_jsonl=curriculum,
        curriculum_state_public=True,
    )

    snapshot = build_current_problems_snapshot(settings, registry=_registry(), tempo=3)

    assert snapshot.active_K == 1
    assert snapshot.active_tempo_source == "chain"
    assert snapshot.frontier_depth == 2
    assert snapshot.task_count == 1
    assert {task.frontier_depth for task in snapshot.tasks} == {2}


def test_current_problem_snapshot_can_render_active_registry_without_randomness() -> None:
    settings = LemmaSettings(
        active_task_count=1,
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
    )

    snapshot = build_current_problems_snapshot(
        settings,
        registry=_registry(),
        registry_is_active=True,
        tempo=2,
        include_randomness_hashes=False,
    )

    assert snapshot.task_count == 1
    assert snapshot.tasks[0].queue_position == 0
    assert snapshot.active_epoch_randomness_sha256 is None
    assert snapshot.active_selection_seed_sha256 is None


def test_current_problem_snapshot_enforces_production_boundary() -> None:
    settings = LemmaSettings(
        protocol_mode="production",
        task_registry_sha256_expected="a" * 64,
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
    )

    with pytest.raises(RuntimeError, match="LEMMA_TASK_SUPPLY_MODE=procedural"):
        build_current_problems_snapshot(settings, registry=_registry(), tempo=0)


def test_refresh_site_current_problems_script_writes_site_json(tmp_path: Path) -> None:
    site_repo = tmp_path / "lemmasub.net"
    site_repo.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/refresh_site_current_problems.py",
            "--site-repo",
            str(site_repo),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads(result.stdout)
    payload = json.loads((site_repo / "data" / "current-problems.json").read_text(encoding="utf-8"))
    assert summary["task_count"] == payload["task_count"]
    assert payload["schema_version"] == 1


def test_current_problem_service_serves_snapshot() -> None:
    settings = LemmaSettings(active_task_count=1, frontier_depth=0, active_queue_seed="pytest")

    def snapshot_builder(_settings: LemmaSettings, *, tempo: int | None = None):
        return build_current_problems_snapshot(
            settings,
            registry=_registry(),
            generated_at="2026-05-20T00:00:00Z",
            tempo=0 if tempo is None else tempo,
        )

    service = CurrentProblemService(settings, snapshot_builder=snapshot_builder)
    status, body = service.response("/current-problems.json?t=1")
    payload = json.loads(body)

    assert status == 200
    assert payload["schema_version"] == 1
    assert payload["task_count"] == 1


def test_current_problem_service_caches_snapshot_response() -> None:
    calls = 0

    def snapshot_builder(_settings: LemmaSettings, *, tempo: int | None = None):
        nonlocal calls
        calls += 1
        return build_current_problems_snapshot(
            LemmaSettings(active_task_count=1, frontier_depth=0, active_queue_seed="pytest"),
            registry=_registry(),
            generated_at="2026-05-20T00:00:00Z",
            tempo=0 if tempo is None else tempo,
        )

    service = CurrentProblemService(LemmaSettings(), snapshot_builder=snapshot_builder)
    first_status, first_body = service.response("/current-problems.json")
    second_status, second_body = service.response("/current-problems.json?t=2")

    assert first_status == 200
    assert second_status == 200
    assert first_body == second_body
    assert calls == 1


def test_current_problem_service_serves_stale_cache_if_refresh_fails() -> None:
    calls = 0

    def snapshot_builder(_settings: LemmaSettings, *, tempo: int | None = None):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("temporary chain lookup failure")
        return build_current_problems_snapshot(
            LemmaSettings(active_task_count=1, frontier_depth=0, active_queue_seed="pytest"),
            registry=_registry(),
            generated_at="2026-05-20T00:00:00Z",
            tempo=0 if tempo is None else tempo,
        )

    service = CurrentProblemService(LemmaSettings(), snapshot_builder=snapshot_builder, cache_ttl_s=0)
    first_status, first_body = service.response("/current-problems.json")
    second_status, second_body = service.response("/current-problems.json")

    assert first_status == 200
    assert second_status == 200
    assert first_body == second_body
    assert calls == 2


def test_current_problem_service_serves_snapshot_file(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "current-problems.json"
    snapshot_path.write_text('{"schema_version":1,"task_count":4}\n', encoding="utf-8")
    service = CurrentProblemService(LemmaSettings(), snapshot_path=snapshot_path)

    status, body = service.response("/current-problems.json?t=1")

    assert status == 200
    assert json.loads(body) == {"schema_version": 1, "task_count": 4}


def test_current_problem_service_serves_stale_snapshot_file_if_refresh_fails(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "current-problems.json"
    snapshot_path.write_text('{"schema_version":1,"task_count":4}\n', encoding="utf-8")
    service = CurrentProblemService(LemmaSettings(), snapshot_path=snapshot_path)
    first_status, first_body = service.response("/current-problems.json")
    snapshot_path.unlink()
    second_status, second_body = service.response("/current-problems.json")

    assert first_status == 200
    assert second_status == 200
    assert first_body == second_body


def test_current_problem_service_fails_closed() -> None:
    def snapshot_builder(_settings: LemmaSettings, *, tempo: int | None = None):
        raise RuntimeError("private detail")

    service = CurrentProblemService(LemmaSettings(), snapshot_builder=snapshot_builder)
    status, body = service.response("/current-problems.json")
    payload = json.loads(body)

    assert status == 503
    assert payload == {"error": "problem feed unavailable"}


def test_current_problem_service_has_health_and_404() -> None:
    service = CurrentProblemService(LemmaSettings())

    health_status, health_body = service.response("/healthz")
    missing_status, missing_body = service.response("/missing")

    assert health_status == 200
    assert json.loads(health_body)["ok"] is True
    assert missing_status == 404
    assert json.loads(missing_body)["error"] == "not found"

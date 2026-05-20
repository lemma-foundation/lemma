from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lemma.common.config import LemmaSettings
from lemma.current_problems import build_current_problems_snapshot, write_current_problems_snapshot
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

    snapshot = build_current_problems_snapshot(settings, registry=_registry(), generated_at="2026-05-20T00:00:00Z")
    payload = snapshot.model_dump(mode="json", exclude_none=True)
    text = json.dumps(payload, sort_keys=True)

    assert payload["schema_version"] == 1
    assert payload["registry_sha256"] == "a" * 64
    assert payload["registry_task_count"] == 3
    assert payload["active_K"] == 2
    assert payload["task_count"] == 2
    assert {task["task_id"] for task in payload["tasks"]} == {"lemma.test.alpha", "lemma.test.beta"}
    assert "proof_script" not in text
    assert "signature" not in text
    assert "submission_stub" not in text


def test_write_current_problem_snapshot(tmp_path: Path) -> None:
    settings = LemmaSettings(active_task_count=1, frontier_depth=0, active_queue_seed="pytest")
    output = tmp_path / "data" / "current-problems.json"
    snapshot = build_current_problems_snapshot(settings, registry=_registry(), generated_at="2026-05-20T00:00:00Z")

    write_current_problems_snapshot(output, snapshot)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["task_count"] == 1
    assert payload["tasks"][0]["queue_depth"] == 0


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

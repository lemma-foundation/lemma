"""Launch-readiness assessment over a task registry."""

from __future__ import annotations

from lemma.launch import LaunchReport, assess_launch_readiness
from lemma.launch.readiness import CriterionResult
from lemma.tasks import LemmaTask, TaskRegistry

ENV_HASH = "a" * 64


def _task(task_id: str, *, task_format: str = "isolated_proof", reproduction: str = "lake build Demo") -> LemmaTask:
    return LemmaTask(
        id=task_id,
        title=f"Task {task_id}",
        task_format=task_format,
        task_class="source_sorry" if task_format == "patch" else "formal_conjecture",
        source_value="low",
        source_stream="sorrydb" if task_format == "patch" else "formal_conjectures",
        source_ref={"kind": "sorrydb", "name": "Repo", "url": "https://github.com/example/repo", "commit": "abc123"},
        source_license="Apache-2.0",
        imports=("Mathlib",),
        allowed_files=("Demo/Gap.lean",) if task_format == "patch" else (),
        theorem_name="demo_lemma",
        type_expr="True",
        statement="theorem demo_lemma : True := by\n  sorry",
        submission_stub="theorem demo_lemma : True := by\n  sorry\n",
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        environment_sha256=ENV_HASH,
        reproduction_command=reproduction,
    )


def _registry(*tasks: LemmaTask) -> TaskRegistry:
    return TaskRegistry(schema_version=1, tasks=tuple(tasks), sha256="b" * 64)


def _healthy_registry() -> TaskRegistry:
    return _registry(
        _task("lemma.a", task_format="patch"),
        _task("lemma.b", task_format="patch"),
        _task("lemma.c"),
        _task("lemma.d"),
    )


def _by_id(report: LaunchReport, criterion_id: str) -> CriterionResult:
    return next(c for c in report.criteria if c.id == criterion_id)


def test_healthy_registry_is_ready() -> None:
    report = assess_launch_readiness(_healthy_registry(), min_tasks=4, epochs=3, active_K=2)
    assert report.ready is True
    assert report.fail_count == 0
    assert _by_id(report, "packaged_task_volume").metric["count"] == 4
    assert _by_id(report, "duplicate_scored_correctly").status == "pass"
    assert _by_id(report, "cheats_never_rewarded").status == "pass"
    assert _by_id(report, "epochs_run_end_to_end").status == "pass"
    assert _by_id(report, "atlas_site_accurate").status == "pass"
    assert _by_id(report, "solved_replay_metadata_present").status == "pass"
    assert _by_id(report, "rejection_reasons_understandable").status == "pass"


def test_container_replay_is_reported_but_not_measured() -> None:
    report = assess_launch_readiness(_healthy_registry(), min_tasks=4)
    replay = _by_id(report, "container_replay")
    assert replay.status == "not_measured"
    assert report.not_measured_count >= 1
    # not_measured does not by itself block readiness
    assert report.ready is True


def test_volume_below_minimum_fails() -> None:
    report = assess_launch_readiness(_healthy_registry(), min_tasks=100, epochs=2, active_K=2)
    assert report.ready is False
    volume = _by_id(report, "packaged_task_volume")
    assert volume.status == "fail"
    assert volume.metric["count"] == 4


def test_missing_reproduction_command_fails() -> None:
    registry = _registry(
        _task("lemma.a"),
        _task("lemma.b", reproduction=""),
    )
    report = assess_launch_readiness(registry, min_tasks=2, epochs=2, active_K=2)
    repro = _by_id(report, "reproduction_command_present")
    assert repro.status == "fail"
    assert "lemma.b" in repro.metric["missing"]
    assert report.ready is False


def test_assessment_is_deterministic() -> None:
    registry = _healthy_registry()
    first = assess_launch_readiness(registry, min_tasks=4)
    second = assess_launch_readiness(registry, min_tasks=4)
    assert first.model_dump() == second.model_dump()
    assert _by_id(first, "deterministic_active_selection").status == "pass"


def test_duplicate_miner_earns_no_credit() -> None:
    report = assess_launch_readiness(_healthy_registry(), min_tasks=4, epochs=3, active_K=2)
    dup = _by_id(report, "duplicate_scored_correctly")
    assert dup.metric["duplicate_credit"] == 0
    assert dup.metric["honest_credit"] > 0

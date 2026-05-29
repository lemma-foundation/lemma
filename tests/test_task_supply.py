"""Task supply activation gates."""

from __future__ import annotations

from lemma.task_supply import deterministic_queue, eligible_tasks, generated_tasks, make_task


def test_generated_tasks_have_source_license_and_hashes() -> None:
    tasks = generated_tasks(2)

    assert len(tasks) == 2
    assert all(task.source_license for task in tasks)
    assert all(len(task.target_sha256) == 64 for task in tasks)
    assert all(task.queue_depth >= 0 for task in tasks)


def test_activation_excludes_baseline_solved_and_held_out_tasks() -> None:
    active = make_task(
        task_id="lemma.test.active",
        title="Active",
        theorem_name="active_task",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    baseline = active.model_copy(update={"id": "lemma.test.baseline", "metadata": {"baseline_solved": True}})
    held_out = active.model_copy(update={"id": "lemma.test.held_out", "metadata": {"held_out_benchmark": True}})

    assert eligible_tasks([active, baseline, held_out]) == [active]


def test_deterministic_queue_interleaves_frontier_and_foundation_levels() -> None:
    deep = make_task(
        task_id="lemma.test.deep",
        title="Deep",
        theorem_name="deep_task",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
        queue_depth=3,
    )
    shallow = deep.model_copy(update={"id": "lemma.test.shallow", "queue_depth": 0})
    mid = deep.model_copy(update={"id": "lemma.test.mid", "queue_depth": 1})
    harder = deep.model_copy(update={"id": "lemma.test.harder", "queue_depth": 2})

    assert [task.queue_depth for task in deterministic_queue([shallow, mid, harder, deep], seed="tempo")] == [
        3,
        0,
        2,
        1,
    ]

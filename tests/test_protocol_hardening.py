"""Production boundaries for registry-backed real Lean tasks."""

from __future__ import annotations

import pytest
from lemma.common.config import LemmaSettings
from lemma.protocol_invariants import (
    enforce_production_invariants,
    production_supply_rejection_reason,
)
from lemma.task_supply import make_task
from lemma.tasks import SourceRef, TaskRegistry
from lemma.validator import active_tasks_for_validation


def _real_task(*, source_stream: str = "sorrydb", task_id: str = "lemma.real.true"):
    return make_task(
        task_id=task_id,
        title="Real missing proof",
        theorem_name="real_true",
        type_expr="True",
        source_stream=source_stream,
        source_name="pytest-real-source",
        source_license="Apache-2.0",
        triviality_status="paid_medium",
        metadata={"triviality_checked": True},
    ).model_copy(
        update={
            "difficulty_band": "medium",
            "source_ref": SourceRef(
                kind=source_stream,
                name="pytest-real-source",
                url="https://example.test/repo",
                commit="abc123",
                path="Smoke.lean",
            ),
        }
    )


def _registry(*tasks) -> TaskRegistry:
    return TaskRegistry(schema_version=1, tasks=tasks or (_real_task(),), sha256="0" * 64, signature_status="verified")


def _production_settings(**updates: object) -> LemmaSettings:
    base = {
        "protocol_mode": "production",
        "task_registry_sha256_expected": "0" * 64,
        "enabled_domains": ("lean",),
        "lean_sandbox_network": "none",
        "require_submission_signatures": True,
        "require_commit_reveal": True,
        "require_strong_proof_identity": True,
        "active_seed_mode": "epoch_randomness",
        "active_epoch_randomness_source": "chain_drand",
        "active_task_count": 1,
        "frontier_depth": 0,
    }
    base.update(updates)
    return LemmaSettings(_env_file=None, **base)


def test_production_accepts_registry_backed_real_task() -> None:
    enforce_production_invariants(_production_settings(), _registry())


def test_production_rejects_fixed_fixture_paid_source() -> None:
    task = _real_task(source_stream="fixed_fixture").model_copy(
        update={"source_ref": SourceRef(kind="fixed_fixture", name="pytest", path="tasks/registry.json")}
    )

    assert production_supply_rejection_reason(task) == "source_stream:fixed_fixture"


def test_production_real_sources_require_public_source_location() -> None:
    task = _real_task().model_copy(update={"source_ref": SourceRef(kind="sorrydb", name="pytest")})

    assert production_supply_rejection_reason(task) == "source_ref.url"


def test_production_mode_requires_registry_supply_mode() -> None:
    settings = _production_settings().model_copy(update={"task_supply_mode": "procedural"})

    with pytest.raises(RuntimeError, match="LEMMA_TASK_SUPPLY_MODE=registry"):
        enforce_production_invariants(settings, _registry())


def test_production_active_selection_uses_epoch_randomness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lemma.validator.resolve_active_epoch_randomness",
        lambda settings, *, tempo: "pytest-randomness",
    )

    active = active_tasks_for_validation(_registry(), _production_settings(), tempo=7)

    assert len(active) == 1
    assert active[0].id == "lemma.real.true"


def test_production_rejects_private_curriculum_retarget_state(tmp_path) -> None:  # noqa: ANN001
    settings = _production_settings(
        curriculum_retarget_enabled=True,
        curriculum_state_jsonl=tmp_path / "curriculum.jsonl",
    )

    with pytest.raises(RuntimeError, match="LEMMA_CURRICULUM_STATE_PUBLIC"):
        active_tasks_for_validation(_registry(), settings, tempo=3)

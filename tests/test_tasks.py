"""Training task registry behavior."""

from __future__ import annotations

import json

import httpx
import pytest
from lemma.common.config import LemmaSettings
from lemma.tasks import LemmaTask, fetch_task_registry, load_task_registry, problem_target_sha256


def _submission_stub() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem test_true : True := by",
            "  sorry",
            "",
            "end Submission",
            "",
        ]
    )


def _task_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "lemma.test.true",
        "task_version": 1,
        "title": "True task",
        "source_stream": "human_curated",
        "source_ref": {"kind": "unit_test", "name": "pytest"},
        "source_license": "CC-BY-4.0",
        "imports": ["Mathlib"],
        "theorem_name": "test_true",
        "type_expr": "True",
        "statement": "theorem test_true : True := by\n  sorry",
        "submission_stub": _submission_stub(),
        "lean_toolchain": "leanprover/lean4:v4.30.0-rc2",
        "mathlib_rev": "5450b53e5ddc",
        "policy": "restricted_helpers",
        "metadata": {"difficulty": "sample"},
    }


def test_task_schema_roundtrip_and_target_hash_stability() -> None:
    task = LemmaTask.model_validate(_task_payload())
    payload = task.model_dump()
    restored = LemmaTask.model_validate(payload)

    assert restored == task
    assert task.target_sha256 == problem_target_sha256(task.to_problem())
    assert task.task_version == 1
    assert task.source_ref.name == "pytest"


def test_registry_loads_from_bytes() -> None:
    raw = json.dumps({"schema_version": 1, "tasks": [_task_payload()]}).encode()

    registry = load_task_registry(raw)

    assert registry.get("lemma.test.true").theorem_name == "test_true"


def test_registry_fetches_from_http(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps({"schema_version": 1, "tasks": [_task_payload()]}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://example.test/tasks.json"
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr("lemma.tasks.httpx.get", lambda *args, **kwargs: handler(httpx.Request("GET", args[0])))
    settings = LemmaSettings(_env_file=None, task_registry_url="https://example.test/tasks.json")

    registry = fetch_task_registry(settings)

    assert registry.tasks[0].id == "lemma.test.true"


def test_task_requires_source_metadata() -> None:
    payload = _task_payload()
    payload.pop("source_ref")

    with pytest.raises(ValueError):
        LemmaTask.model_validate(payload)

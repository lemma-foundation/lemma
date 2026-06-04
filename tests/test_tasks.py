"""Training task registry behavior."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from lemma.common.config import LemmaSettings
from lemma.tasks import (
    LemmaTask,
    TaskError,
    fetch_task_registry,
    load_task_registry,
    problem_target_sha256,
    target_type_sha256,
)


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
        "task_format": "isolated_proof",
        "task_class": "canary",
        "source_value": "calibration",
        "source_stream": "human_curated",
        "source_ref": {"kind": "unit_test", "name": "pytest"},
        "source_license": "CC-BY-4.0",
        "imports": ["Mathlib"],
        "allowed_files": ["Submission.lean"],
        "allowed_imports": ["Mathlib"],
        "theorem_name": "test_true",
        "type_expr": "True",
        "statement": "theorem test_true : True := by\n  sorry",
        "submission_stub": _submission_stub(),
        "lean_toolchain": "leanprover/lean4:v4.30.0-rc2",
        "mathlib_rev": "5450b53e5ddc",
        "policy": "restricted_helpers",
        "target_type_sha256": target_type_sha256("True"),
        "reproduction_command": "lake build",
        "metadata": {"difficulty": "sample"},
    }


def test_task_schema_roundtrip_and_target_hash_stability() -> None:
    task = LemmaTask.model_validate(_task_payload())
    payload = task.model_dump()
    restored = LemmaTask.model_validate(payload)

    assert restored == task
    assert task.target_sha256 == problem_target_sha256(task.to_problem())
    assert task.target_type_sha256 == target_type_sha256("True")
    assert task.task_version == 1
    assert task.task_format == "isolated_proof"
    assert task.source_ref.name == "pytest"


def test_real_task_manifest_fields_export_to_v2() -> None:
    payload = _task_payload()
    payload.update(
        {
            "id": "lemma.test.patch",
            "task_format": "patch",
            "task_class": "source_sorry",
            "source_value": "high",
            "source_stream": "sorrydb",
            "allowed_files": ["Mathlib/Fixture.lean"],
            "allowed_imports": ["Mathlib"],
            "environment_sha256": "a" * 64,
            "reproduction_command": "lake build Mathlib.Fixture",
        }
    )
    task = LemmaTask.model_validate(payload)
    exported = task.to_v2()

    assert exported["task_type"] == "patch"
    assert exported["constraints"]["allowed_files"] == ["Mathlib/Fixture.lean"]
    assert exported["constraints"]["allowed_imports"] == ["Mathlib"]
    assert exported["constraints"]["target_type_sha256"] == target_type_sha256("True")
    assert exported["constraints"]["environment_sha256"] == "a" * 64
    assert exported["constraints"]["reproduction_command"] == "lake build Mathlib.Fixture"
    assert exported["metadata"]["task_class"] == "source_sorry"
    assert exported["metadata"]["source_value"] == "high"


def test_patch_task_fixture_manifest_is_repo_relative() -> None:
    fixture_root = Path("tests/fixtures/lean_patch_project")
    source_path = fixture_root / "PatchFixture.lean"
    patch_path = fixture_root / "patches/add_zero_fixture.patch"
    source = source_path.read_text(encoding="utf-8")
    patch = patch_path.read_text(encoding="utf-8")
    payload = _task_payload()
    payload.update(
        {
            "id": "lemma.fixture.patch.add_zero",
            "title": "Patch fixed Nat.add_zero fixture",
            "task_format": "patch",
            "task_class": "source_sorry",
            "source_stream": "fixed_fixture",
            "source_ref": {
                "kind": "fixed_fixture",
                "name": "lean_patch_project",
                "path": source_path.as_posix(),
            },
            "imports": [],
            "allowed_files": ["PatchFixture.lean"],
            "allowed_imports": [],
            "theorem_name": "PatchFixture.add_zero_fixture",
            "type_expr": "forall n : Nat, n + 0 = n",
            "statement": source,
            "submission_stub": source,
            "target_type_sha256": target_type_sha256("forall n : Nat, n + 0 = n"),
            "reproduction_command": "lake build PatchFixture",
        }
    )

    task = LemmaTask.model_validate(payload)
    exported = task.to_v2()

    assert source_path.is_file()
    assert patch_path.is_file()
    assert "  sorry" in source
    assert "+  exact Nat.add_zero n" in patch
    assert task.task_format == "patch"
    assert task.source_ref.path == "tests/fixtures/lean_patch_project/PatchFixture.lean"
    assert not Path(task.source_ref.path or "").is_absolute()
    assert exported["constraints"]["allowed_files"] == ["PatchFixture.lean"]
    assert exported["constraints"]["reproduction_command"] == "lake build PatchFixture"


def test_task_rejects_wrong_target_type_hash() -> None:
    payload = _task_payload()
    payload["target_type_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="target_type_sha256 mismatch"):
        LemmaTask.model_validate(payload)


def test_task_rejects_invalid_environment_hash() -> None:
    payload = _task_payload()
    payload["environment_sha256"] = "not-a-sha"

    with pytest.raises(ValueError, match="environment_sha256"):
        LemmaTask.model_validate(payload)


@pytest.mark.parametrize("allowed_file", ("/tmp/Solution.lean", "../Solution.lean", "src//Solution.lean"))
def test_task_rejects_unsafe_allowed_files(allowed_file: str) -> None:
    payload = _task_payload()
    payload["allowed_files"] = [allowed_file]

    with pytest.raises(ValueError, match="allowed_files"):
        LemmaTask.model_validate(payload)


def test_registry_loads_from_bytes() -> None:
    raw = json.dumps({"schema_version": 1, "tasks": [_task_payload()]}).encode()

    registry = load_task_registry(raw)

    assert registry.get("lemma.test.true").theorem_name == "test_true"
    assert registry.signature_status == "unsigned"


@pytest.mark.parametrize("payload", ([], "registry", 1, None))
def test_registry_rejects_non_object_json(payload: object) -> None:
    raw = json.dumps(payload).encode()

    with pytest.raises(TaskError, match="task registry must be a JSON object"):
        load_task_registry(raw)


@pytest.mark.parametrize("schema_version", (True, 1.0, "1"))
def test_registry_rejects_loose_schema_version(schema_version: object) -> None:
    raw = json.dumps({"schema_version": schema_version, "tasks": [_task_payload()]}).encode()

    with pytest.raises(TaskError, match="task registry schema_version must be 1"):
        load_task_registry(raw)


@pytest.mark.parametrize("schema_version", (True, 1.0, "1"))
def test_task_rejects_loose_schema_version(schema_version: object) -> None:
    payload = _task_payload()
    payload["schema_version"] = schema_version
    raw = json.dumps({"schema_version": 1, "tasks": [payload]}).encode()

    with pytest.raises(TaskError, match="task schema_version must be 1"):
        load_task_registry(raw)


@pytest.mark.parametrize(
    "field",
    ("task_version", "queue_position", "queue_depth", "frontier_depth", "active_epoch", "expires_epoch"),
)
@pytest.mark.parametrize("value", (True, 1.0, "1"))
def test_task_rejects_loose_public_int_fields(field: str, value: object) -> None:
    payload = _task_payload()
    payload[field] = value
    raw = json.dumps({"schema_version": 1, "tasks": [payload]}).encode()

    with pytest.raises(TaskError, match=f"task {field} must be exact integer"):
        load_task_registry(raw)


def test_registry_signature_metadata_is_not_trusted_without_verifier() -> None:
    raw = json.dumps(
        {
            "schema_version": 1,
            "signed_by": "fixture-signer",
            "signature": "fixture-signature",
            "tasks": [_task_payload()],
        },
        sort_keys=True,
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()

    registry = load_task_registry(raw, digest)

    assert registry.signed_by == "fixture-signer"
    assert registry.signature == "fixture-signature"
    assert registry.signature_status == "metadata_only"

    tampered = raw.replace(b"True task", b"False task")
    with pytest.raises(TaskError, match="sha256 mismatch"):
        load_task_registry(tampered, digest)


def test_registry_signature_fields_must_be_paired() -> None:
    raw = json.dumps({"schema_version": 1, "signed_by": "fixture-signer", "tasks": [_task_payload()]}).encode()

    with pytest.raises(TaskError, match="provided together"):
        load_task_registry(raw)


def test_registry_signature_verifier_interface_marks_verified() -> None:
    class FixtureVerifier:
        def verify_registry(self, *, raw: bytes, signed_by: str, signature: str) -> bool:
            assert raw
            return signed_by == "fixture-signer" and signature == "fixture-signature"

    raw = json.dumps(
        {
            "schema_version": 1,
            "signed_by": "fixture-signer",
            "signature": "fixture-signature",
            "tasks": [_task_payload()],
        },
        sort_keys=True,
    ).encode()

    registry = load_task_registry(
        raw,
        hashlib.sha256(raw).hexdigest(),
        signature_verifier=FixtureVerifier(),
    )

    assert registry.signature_status == "verified"


def test_registry_signature_verifier_rejects_bad_signature() -> None:
    class RejectingVerifier:
        def verify_registry(self, *, raw: bytes, signed_by: str, signature: str) -> bool:
            return False

    raw = json.dumps(
        {
            "schema_version": 1,
            "signed_by": "fixture-signer",
            "signature": "bad-signature",
            "tasks": [_task_payload()],
        },
        sort_keys=True,
    ).encode()

    with pytest.raises(TaskError, match="signature verification failed"):
        load_task_registry(raw, hashlib.sha256(raw).hexdigest(), signature_verifier=RejectingVerifier())


def test_registry_fetches_from_http(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps({"schema_version": 1, "tasks": [_task_payload()]}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://example.test/tasks.json"
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr("lemma.tasks.httpx.get", lambda *args, **kwargs: handler(httpx.Request("GET", args[0])))
    settings = LemmaSettings(_env_file=None, task_registry_url="https://example.test/tasks.json")

    registry = fetch_task_registry(settings)

    assert registry.tasks[0].id == "lemma.test.true"


def test_registry_rejects_symlink_file_url(tmp_path) -> None:  # noqa: ANN001
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"schema_version": 1, "tasks": [_task_payload()]}) + "\n", encoding="utf-8")
    symlink_path = tmp_path / "registry-link.json"
    symlink_path.symlink_to(registry_path)
    settings = LemmaSettings(_env_file=None, task_registry_url=symlink_path.as_uri())

    with pytest.raises(TaskError, match="task registry path invalid"):
        fetch_task_registry(settings)


def test_task_requires_source_metadata() -> None:
    payload = _task_payload()
    payload.pop("source_ref")

    with pytest.raises(ValueError):
        LemmaTask.model_validate(payload)

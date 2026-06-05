"""Real-task Proof Atlas publishing layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lemma.atlas import (
    apply_instructions,
    build_env_index,
    build_real_task_snapshot,
    build_solved_ledger,
    build_task_bundles,
    write_real_task_snapshot,
)
from lemma.atlas.realtask import load_atlas_registry_union, read_accepted_rows
from lemma.tasks import LemmaTask, TaskRegistry

ENV_HASH = "a" * 64

PRIVATE_PATH = "/" + "Users/example/secret"


def _proof_task() -> LemmaTask:
    return LemmaTask(
        id="lemma.fc.easy_lemma",
        title="Formal conjecture lemma",
        task_format="isolated_proof",
        task_class="formal_conjecture",
        source_value="low",
        source_stream="formal_conjectures",
        source_ref={
            "kind": "formal_conjectures",
            "name": "EasyConj",
            "url": "https://github.com/example/formal-conjectures",
            "commit": "deadbeefcafebabe",
        },
        source_license="Apache-2.0",
        imports=("Mathlib",),
        theorem_name="easy_lemma",
        type_expr="True",
        statement="theorem easy_lemma : True := by\n  sorry",
        submission_stub="import Mathlib\n\ntheorem easy_lemma : True := by\n  sorry\n",
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        environment_sha256=ENV_HASH,
        reproduction_command="lake build EasyConj",
        activation_status="benchmark",
    )


def _patch_task() -> LemmaTask:
    return LemmaTask(
        id="lemma.sorrydb.fix_gap",
        title="Fill a sorry",
        task_format="patch",
        task_class="source_sorry",
        source_value="medium",
        source_stream="sorrydb",
        source_ref={
            "kind": "sorrydb",
            "name": "ExampleRepo",
            "url": "https://github.com/example/example-repo",
            "commit": "0123456789abcdef",
            "path": "Example/Gap.lean",
        },
        source_license="Apache-2.0",
        imports=("Mathlib",),
        allowed_files=("Example/Gap.lean",),
        theorem_name="gap_lemma",
        type_expr="True",
        statement="theorem gap_lemma : True := by\n  sorry",
        submission_stub="theorem gap_lemma : True := by\n  sorry\n",
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        environment_sha256=ENV_HASH,
        reproduction_command="lake build Example",
        activation_status="paid",
        # An operator-private note must never reach a public bundle.
        metadata={"local_path": PRIVATE_PATH},
    )


def _registry(*tasks: LemmaTask) -> TaskRegistry:
    return TaskRegistry(schema_version=1, tasks=tuple(tasks), sha256="b" * 64)


def _accepted_row(
    task_id: str,
    *,
    kind: str,
    rewarded: bool = True,
    miner: str = "miner-hk",
    artifact_sha256: str = "c" * 64,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "accepted_artifact": {
            "kind": kind,
            "artifact_sha256": artifact_sha256,
            "proof_identity": "pi-" + task_id,
            "proof_identity_strength": "strong",
        },
        "provenance": {"miner_hotkey": miner, "validator_hotkey": "val-hk", "block": 42},
        "metadata": {"rewarded": rewarded},
    }


def test_bundles_carry_real_source_and_environment_metadata() -> None:
    bundles = build_task_bundles(_registry(_patch_task(), _proof_task()))
    assert [b.task_id for b in bundles] == ["lemma.fc.easy_lemma", "lemma.sorrydb.fix_gap"]
    patch_bundle = next(b for b in bundles if b.task_format == "patch")
    assert patch_bundle.source_ref["url"] == "https://github.com/example/example-repo"
    assert patch_bundle.source_ref["commit"] == "0123456789abcdef"
    assert patch_bundle.allowed_files == ["Example/Gap.lean"]
    assert patch_bundle.environment_sha256 == ENV_HASH
    assert patch_bundle.reproduction_command == "lake build Example"
    assert patch_bundle.target_sha256
    assert patch_bundle.target_type_sha256


def test_env_index_groups_tasks_by_environment_hash() -> None:
    bundles = build_task_bundles(_registry(_patch_task(), _proof_task()))
    envs = build_env_index(bundles)
    assert len(envs) == 1
    entry = envs[0]
    assert entry.environment_sha256 == ENV_HASH
    assert entry.task_count == 2
    assert entry.task_ids == ["lemma.fc.easy_lemma", "lemma.sorrydb.fix_gap"]
    assert sorted(entry.source_kinds) == ["formal_conjectures", "sorrydb"]


def test_solved_ledger_joins_rewarded_rows_to_bundles() -> None:
    bundles = build_task_bundles(_registry(_patch_task(), _proof_task()))
    rows = [
        _accepted_row("lemma.sorrydb.fix_gap", kind="patch"),
        _accepted_row("lemma.fc.easy_lemma", kind="proof", rewarded=False),
    ]
    solved = build_solved_ledger(rows, bundles)
    assert [entry.task_id for entry in solved] == ["lemma.sorrydb.fix_gap"]
    entry = solved[0]
    assert entry.artifact_kind == "patch"
    assert entry.miner_hotkey == "miner-hk"
    assert entry.block == 42
    assert entry.source_ref["commit"] == "0123456789abcdef"
    assert "apply the accepted patch" in entry.apply_instructions.lower()


def test_solved_ledger_dedupes_first_winner_per_task() -> None:
    bundles = build_task_bundles(_registry(_proof_task()))
    rows = [
        _accepted_row("lemma.fc.easy_lemma", kind="proof", miner="winner"),
        _accepted_row("lemma.fc.easy_lemma", kind="proof", miner="late"),
    ]
    solved = build_solved_ledger(rows, bundles)
    assert len(solved) == 1
    assert solved[0].miner_hotkey == "winner"


def test_apply_instructions_for_isolated_proof_mentions_environment() -> None:
    bundle = build_task_bundles(_registry(_proof_task()))[0]
    text = apply_instructions(bundle, artifact_kind="proof")
    assert "easy_lemma" in text
    assert "leanprover/lean4:v4.30.0-rc2" in text
    assert "lake build EasyConj" in text


def test_snapshot_manifest_is_deterministic_and_hashes_files() -> None:
    registry = _registry(_patch_task(), _proof_task())
    rows = [_accepted_row("lemma.sorrydb.fix_gap", kind="patch")]
    first, payloads = build_real_task_snapshot(netuid="sn467", registry=registry, accepted_rows=rows)
    second, _ = build_real_task_snapshot(netuid="sn467", registry=registry, accepted_rows=rows)
    assert first.model_dump() == second.model_dump()
    assert first.bundle_count == 2
    assert first.solved_count == 1
    assert first.environment_count == 1
    assert set(payloads) == {
        "tasks/sn467/bundles/index.json",
        "proofs/sn467/solved-ledger.json",
        "envs/sn467/index.json",
        "sources/sn467/index.json",
    }
    assert all(len(digest) == 64 for digest in first.files.values())


def test_write_snapshot_emits_public_layout_without_leaking_private_state(tmp_path: Path) -> None:
    registry = _registry(_patch_task(), _proof_task())
    rows = [_accepted_row("lemma.sorrydb.fix_gap", kind="patch")]
    manifest = write_real_task_snapshot(tmp_path, netuid="sn467", registry=registry, accepted_rows=rows)

    bundles_index = tmp_path / "tasks" / "sn467" / "bundles" / "index.json"
    solved_ledger = tmp_path / "proofs" / "sn467" / "solved-ledger.json"
    env_index = tmp_path / "envs" / "sn467" / "index.json"
    sources_index = tmp_path / "sources" / "sn467" / "index.json"
    for path in (bundles_index, solved_ledger, env_index, sources_index):
        assert path.is_file()

    assert manifest.solved_count == 1
    blob = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))
    assert PRIVATE_PATH not in blob
    assert "local_path" not in blob

    solved = json.loads(solved_ledger.read_text(encoding="utf-8"))
    assert solved["solved"][0]["task_id"] == "lemma.sorrydb.fix_gap"


def _write_registry_file(registries_dir: Path, *tasks: LemmaTask, tempo: str = "1") -> str:
    import hashlib

    payload = {"schema_version": 1, "tasks": [t.model_dump(mode="json", exclude_none=True) for t in tasks]}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    registries_dir.mkdir(parents=True, exist_ok=True)
    (registries_dir / f"{sha}.json").write_bytes(raw)
    index_path = registries_dir / "index.json"
    index: dict[str, Any] = {"schema_version": 1, "netuid": "sn467", "registries": {}}
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    index["registries"][tempo] = {"sha256": sha, "path": f"{sha}.json"}
    index_path.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    return sha


def test_load_atlas_registry_union_merges_newest_first(tmp_path: Path) -> None:
    registries = tmp_path / "tasks" / "sn467" / "registries"
    _write_registry_file(registries, _proof_task(), tempo="1")
    _write_registry_file(registries, _proof_task(), _patch_task(), tempo="2")

    registry = load_atlas_registry_union(registries)
    assert registry is not None
    ids = sorted(task.id for task in registry.tasks)
    assert ids == ["lemma.fc.easy_lemma", "lemma.sorrydb.fix_gap"]


def test_load_atlas_registry_union_returns_none_without_registry(tmp_path: Path) -> None:
    assert load_atlas_registry_union(tmp_path / "missing") is None
    empty = tmp_path / "tasks" / "sn467" / "registries"
    empty.mkdir(parents=True)
    assert load_atlas_registry_union(empty) is None


def test_load_atlas_registry_union_single_registry_keeps_its_hash(tmp_path: Path) -> None:
    registries = tmp_path / "tasks" / "sn467" / "registries"
    sha = _write_registry_file(registries, _proof_task(), tempo="7")
    registry = load_atlas_registry_union(registries)
    assert registry is not None
    assert registry.sha256 == sha


def test_read_accepted_rows_from_directory(tmp_path: Path) -> None:
    accepted = tmp_path / "accepted"
    accepted.mkdir()
    row = _accepted_row("lemma.fc.easy_lemma", kind="proof")
    (accepted / "epoch-000001.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    rows = read_accepted_rows(accepted)
    assert len(rows) == 1
    assert rows[0]["task_id"] == "lemma.fc.easy_lemma"

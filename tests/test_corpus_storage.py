from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lemma.chain.commitments import (
    compact_storage_commitment_payload,
    compact_tempo_cid_commitment_payload,
    compact_tempo_commitment_payload,
)
from lemma.corpus.storage import (
    build_active_pool_storage,
    build_epoch_storage_from_rows,
    build_storage_index,
    canonical_json_bytes,
    merkle_root,
    sha256_hex,
)
from lemma.task_supply import make_task


def _write_epoch(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_build_storage_index_writes_per_epoch_commitment_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "lemma-corpus"
    rows = [
        {
            "row_id": "a" * 64,
            "task_id": "task.a",
            "proof_sha256": "b" * 64,
            "queue_position": 2,
            "solver_hotkey": "solver",
            "validator_hotkey": "validator",
        },
        {
            "row_id": "c" * 64,
            "task_id": "task.b",
            "proof_sha256": "d" * 64,
            "queue_position": 7,
            "solver_hotkey": "solver",
            "validator_hotkey": "validator",
        },
    ]
    _write_epoch(repo / "corpus" / "sn467" / "epoch-000042.jsonl", rows)

    index = build_storage_index(repo, "sn467", resolver="hippius-s3-arion")

    tempo_dir = repo / "canonical" / "sn467" / "tempos" / "tempo-000042"
    manifest = json.loads((tempo_dir / "manifest.json").read_text(encoding="utf-8"))
    commitment = json.loads(
        (repo / "canonical" / "sn467" / "commitments" / "tempo-000042.json").read_text(encoding="utf-8")
    )
    leaves = [sha256_hex(canonical_json_bytes(row)) for row in rows]
    expected_root = merkle_root(leaves)

    assert index["epochs"][0]["tempo"] == 42
    assert manifest["accepted_merkle_root"] == expected_root
    assert manifest["entries"][0]["file"] == "entries/slot-000002-aaaaaaaaaaaa.json"
    assert commitment["accepted_merkle_root"] == expected_root
    assert commitment["tempo_directory_cid"] is None
    assert commitment["commitment_payload"] == compact_storage_commitment_payload(
        netuid="sn467",
        tempo=42,
        tempo_directory_sha256=commitment["tempo_directory_sha256"],
        accepted_merkle_root=expected_root,
    )
    assert len(commitment["commitment_payload"].encode("utf-8")) <= 128
    assert (repo / "canonical" / "sn467" / "storage-index.json").is_file()


def test_active_pool_and_accepted_storage_share_tempo_commitment(tmp_path: Path) -> None:
    task = make_task(
        task_id="lemma.test.active",
        title="Active true",
        theorem_name="active_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    output_root = tmp_path / "canonical"

    active = build_active_pool_storage((task,), output_root, netuid="sn467", tempo=9, resolver="hippius-ipfs")
    accepted = build_epoch_storage_from_rows(
        [],
        output_root,
        netuid="sn467",
        tempo=9,
        resolver="hippius-ipfs",
        active_pool=active,
    )

    commitment = json.loads((output_root / "sn467" / "commitments" / "tempo-000009.json").read_text(encoding="utf-8"))
    expected = compact_tempo_commitment_payload(
        netuid="sn467",
        tempo=9,
        active_pool_directory_sha256=str(active["active_pool_directory_sha256"]),
        accepted_directory_sha256=str(accepted["tempo_directory_sha256"]),
        accepted_merkle_root=str(accepted["accepted_merkle_root"]),
    )

    assert (output_root / "sn467" / "active-pools" / "tempo-000009" / "manifest.json").is_file()
    assert (output_root / "sn467" / "tempos" / "tempo-000009" / "manifest.json").is_file()
    assert commitment["active_pool_directory_sha256"] == active["active_pool_directory_sha256"]
    assert commitment["commitment_payload"] == expected
    assert commitment["tempo_commitment_payload"] == expected


def test_rebuilding_same_tempo_removes_stale_storage_files(tmp_path: Path) -> None:
    output_root = tmp_path / "canonical"
    old_row = {
        "row_id": "a" * 64,
        "task_id": "task.old",
        "proof_sha256": "b" * 64,
        "queue_position": 0,
        "solver_hotkey": "solver",
        "validator_hotkey": "validator",
    }

    build_epoch_storage_from_rows([old_row], output_root, netuid="sn467", tempo=9, resolver="hippius-ipfs")
    rebuilt = build_epoch_storage_from_rows([], output_root, netuid="sn467", tempo=9, resolver="hippius-ipfs")

    tempo_dir = output_root / "sn467" / "tempos" / "tempo-000009"
    assert rebuilt["entry_count"] == 0
    assert sorted(path.relative_to(tempo_dir).as_posix() for path in tempo_dir.rglob("*") if path.is_file()) == [
        "manifest.json"
    ]


def test_validator_cid_publish_rewrites_tempo_commitment_payload(tmp_path: Path) -> None:
    from lemma.validator import _write_cid_bound_commitment

    task = make_task(
        task_id="lemma.test.active",
        title="Active true",
        theorem_name="active_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )
    output_root = tmp_path / "canonical"
    active = build_active_pool_storage((task,), output_root, netuid="sn467", tempo=9, resolver="hippius-ipfs")
    accepted = build_epoch_storage_from_rows(
        [],
        output_root,
        netuid="sn467",
        tempo=9,
        resolver="hippius-ipfs",
        active_pool=active,
    )
    artifacts = {**active, **accepted}

    payload = _write_cid_bound_commitment(artifacts, active_pool_cid="bafyactive", accepted_cid="bafyaccepted")
    commitment = json.loads((output_root / "sn467" / "commitments" / "tempo-000009.json").read_text(encoding="utf-8"))
    expected = compact_tempo_cid_commitment_payload(
        netuid="sn467",
        tempo=9,
        active_pool_directory_cid="bafyactive",
        active_pool_directory_sha256=str(active["active_pool_directory_sha256"]),
        accepted_directory_cid="bafyaccepted",
        accepted_directory_sha256=str(accepted["tempo_directory_sha256"]),
        accepted_merkle_root=str(accepted["accepted_merkle_root"]),
    )

    assert payload == expected
    assert commitment["active_pool_directory_cid"] == "bafyactive"
    assert commitment["tempo_directory_cid"] == "bafyaccepted"
    assert commitment["commitment_payload"] == expected


def test_publish_paths_to_s3_uploads_and_verifies_relative_artifacts(tmp_path: Path, monkeypatch) -> None:
    from lemma.corpus import publish

    root = tmp_path / "canonical"
    first = root / "sn467" / "active-pools" / "tempo-000009" / "manifest.json"
    second = root / "sn467" / "tempos" / "tempo-000009" / "manifest.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text('{"active":true}\n', encoding="utf-8")
    second.write_text('{"accepted":true}\n', encoding="utf-8")
    remote: dict[str, bytes] = {}

    def fake_run(command: list[str], *, capture_output: bool = False) -> bytes:
        assert command[:3] == ["aws", "s3", "cp"]
        if capture_output:
            return remote[command[3]]
        remote[command[4]] = Path(command[3]).read_bytes()
        return b""

    monkeypatch.setattr(publish, "_run", fake_run)

    result = publish.publish_paths_to_s3(
        (first.parent, second.parent),
        root=root,
        s3_uri="s3://lemma-corpus/canonical",
        endpoint_url="https://s3.hippius.com",
        aws=["aws"],
        verify=True,
    )

    assert [item.local_path for item in result] == [
        "sn467/active-pools/tempo-000009/manifest.json",
        "sn467/tempos/tempo-000009/manifest.json",
    ]
    assert set(remote) == {
        "s3://lemma-corpus/canonical/sn467/active-pools/tempo-000009/manifest.json",
        "s3://lemma-corpus/canonical/sn467/tempos/tempo-000009/manifest.json",
    }


def test_add_directory_to_ipfs_returns_verified_root_cid(tmp_path: Path, monkeypatch) -> None:
    from lemma.corpus import publish

    root = tmp_path / "tempo"
    (root / "entries").mkdir(parents=True)
    (root / "manifest.json").write_text('{"ok":true}\n', encoding="utf-8")
    (root / "entries" / "slot.json").write_text('{"slot":0}\n', encoding="utf-8")
    files: dict[str, bytes] = {}

    class Response:
        def __init__(self, *, text: str = "", content: bytes = b"") -> None:
            self.text = text
            self.content = content

        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, **kwargs: Any) -> Response:
        if url.endswith("/add"):
            for _field, (relative, body, _content_type) in kwargs["files"]:
                files[relative] = body
            return Response(
                text="\n".join(
                    [
                        json.dumps({"Name": "manifest.json", "Hash": "bafyfile"}),
                        json.dumps({"Name": "", "Hash": "bafyroot"}),
                    ]
                )
            )
        assert url.endswith("/cat")
        relative = kwargs["params"]["arg"].removeprefix("bafyroot/")
        return Response(content=files[relative])

    monkeypatch.setattr(publish.httpx, "post", fake_post)

    result = publish.add_directory_to_ipfs(root, api_url="http://ipfs.local:5001", verify=True, timeout_s=1.0)

    assert result.cid == "bafyroot"
    assert result.file_count == 2

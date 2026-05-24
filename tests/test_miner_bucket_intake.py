"""Commitment-anchored miner bucket reveal intake."""

from __future__ import annotations

from pathlib import Path

from lemma.chain.commitments import (
    ciphertext_sha256,
    miner_bucket_commitment_payload,
    miner_bucket_key,
    miner_submission_merkle_root,
)
from lemma.chain.drand import ciphertext_bytes, encode_ciphertext
from lemma.chain.miner_buckets import (
    MinerBucketReveal,
    RevealedBucketBlob,
    archive_bucket_reveal_batch,
    latest_bucket_reveal_batch,
    poll_bucket_reveals,
    prepare_miner_bucket_publication,
    submissions_from_bucket_reveals,
    upload_miner_bucket_publication,
    write_miner_bucket_publication,
)
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.submissions import build_submission
from lemma.task_supply import make_task
from lemma.tasks import TaskRegistry
from lemma.validator import active_tasks_for_validation, validate_once


def _task():
    return make_task(
        task_id="lemma.test.bucket_true",
        title="Bucket true",
        theorem_name="test_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
    )


def _proof(body: str) -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem test_true : True := by",
            body,
            "",
            "end Submission",
            "",
        ]
    )


def _settings(tmp_path: Path) -> LemmaSettings:
    return LemmaSettings(
        _env_file=None,
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
        lean_use_docker=False,
    )


def _reveal(
    *,
    miner: str,
    commit_block: int,
    ciphertext: str,
    proof: str,
    drand_signature: str | None = None,
) -> MinerBucketReveal:
    return MinerBucketReveal(
        tempo=7,
        miner_hotkey=miner,
        drand_round=77,
        drand_signature=drand_signature,
        commit_block=commit_block,
        commit_extrinsic_hash=f"0x{miner}",
        merkle_root=miner_submission_merkle_root(((0, ciphertext_sha256(ciphertext.encode("utf-8"))),)),
        bucket_url=f"https://bucket.example/{miner}",
        blobs=(RevealedBucketBlob(slot_index=0, ciphertext=ciphertext, proof_script=proof),),
    )


def test_miner_submission_merkle_root_is_slot_bound() -> None:
    digest = ciphertext_sha256(b"encrypted-proof")

    assert miner_bucket_key(3, 4) == "tempo_3/slot_4.bin"
    assert miner_submission_merkle_root(((4, digest),)) != miner_submission_merkle_root(((5, digest),))


def test_ciphertext_encoding_is_explicit() -> None:
    encoded = encode_ciphertext(b"\x00encrypted")

    assert ciphertext_bytes(encoded) == b"\x00encrypted"
    assert ciphertext_bytes("0x00ff") == b"\x00\xff"
    assert ciphertext_bytes("plain-dev-fixture") == b"plain-dev-fixture"


def test_miner_bucket_publication_writes_uploadable_slot_objects(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    proof = _proof("  trivial")
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=proof)
    ciphertext = b"sealed:" + proof.encode("utf-8")
    uploaded: dict[str, bytes] = {}

    publication = prepare_miner_bucket_publication(
        submissions=(submission,),
        active_tasks=active_tasks,
        tempo=7,
        miner_hotkey="hk-a",
        drand_round=77,
        bucket_url="https://bucket.example/miner",
        encrypt_timelocked=lambda payload, round: b"sealed:" + payload,
    )
    write_miner_bucket_publication(publication, tmp_path / "bucket")
    upload_miner_bucket_publication(publication, lambda key, body: uploaded.setdefault(key, body))

    assert publication.commitment_payload == miner_bucket_commitment_payload(
        tempo=7,
        drand_round=77,
        merkle_root=miner_submission_merkle_root(((0, ciphertext_sha256(ciphertext)),)),
    )
    assert (tmp_path / "bucket" / "tempo_7" / "slot_0.bin").read_bytes() == ciphertext
    assert uploaded == {"tempo_7/slot_0.bin": ciphertext}
    assert proof not in (tmp_path / "bucket" / "manifest.json").read_text(encoding="utf-8")


def test_bucket_reveal_inbox_selects_latest_tempo_and_archives(tmp_path: Path) -> None:
    inbox = tmp_path / "bucket-reveals"
    inbox.mkdir()
    old = _reveal(miner="hk-old", commit_block=5, ciphertext="old-cipher", proof=_proof("  trivial")).model_copy(
        update={"tempo": 6}
    )
    latest = _reveal(miner="hk-new", commit_block=6, ciphertext="new-cipher", proof=_proof("  trivial"))
    (inbox / "old.json").write_text(old.model_dump_json() + "\n", encoding="utf-8")
    (inbox / "latest.json").write_text(latest.model_dump_json() + "\n", encoding="utf-8")

    batch = latest_bucket_reveal_batch(inbox)

    assert batch.tempo == 7
    assert [reveal.miner_hotkey for reveal in batch.reveals] == ["hk-new"]
    assert [path.name for path in batch.paths] == ["latest.json"]
    assert [path.name for path in batch.stale_paths] == ["old.json"]

    archive_bucket_reveal_batch(batch)

    assert not (inbox / "old.json").exists()
    assert not (inbox / "latest.json").exists()
    assert (inbox / "processed" / "latest.json").exists()
    assert (inbox / "stale" / "old.json").exists()


def test_bucket_reveal_inbox_selects_latest_completed_tempo(tmp_path: Path) -> None:
    inbox = tmp_path / "bucket-reveals"
    inbox.mkdir()
    old = _reveal(miner="hk-old", commit_block=5, ciphertext="old-cipher", proof=_proof("  trivial")).model_copy(
        update={"tempo": 6}
    )
    complete = _reveal(
        miner="hk-complete", commit_block=6, ciphertext="complete-cipher", proof=_proof("  trivial")
    )
    current = _reveal(
        miner="hk-current", commit_block=7, ciphertext="current-cipher", proof=_proof("  trivial")
    ).model_copy(update={"tempo": 8})
    (inbox / "old.json").write_text(old.model_dump_json() + "\n", encoding="utf-8")
    (inbox / "complete.json").write_text(complete.model_dump_json() + "\n", encoding="utf-8")
    (inbox / "current.json").write_text(current.model_dump_json() + "\n", encoding="utf-8")

    batch = latest_bucket_reveal_batch(inbox, before_tempo=8)

    assert batch.tempo == 7
    assert [reveal.miner_hotkey for reveal in batch.reveals] == ["hk-complete"]
    assert [path.name for path in batch.paths] == ["complete.json"]
    assert [path.name for path in batch.stale_paths] == ["old.json"]

    archive_bucket_reveal_batch(batch)

    assert (inbox / "current.json").exists()
    assert (inbox / "processed" / "complete.json").exists()
    assert (inbox / "stale" / "old.json").exists()


def test_bucket_reveal_inbox_quarantines_mixed_tempo_file(tmp_path: Path) -> None:
    inbox = tmp_path / "bucket-reveals"
    inbox.mkdir()
    old = _reveal(miner="hk-old", commit_block=5, ciphertext="old-cipher", proof=_proof("  trivial")).model_copy(
        update={"tempo": 6}
    )
    latest = _reveal(miner="hk-new", commit_block=6, ciphertext="new-cipher", proof=_proof("  trivial"))
    (inbox / "mixed.jsonl").write_text(
        old.model_dump_json() + "\n" + latest.model_dump_json() + "\n",
        encoding="utf-8",
    )

    batch = latest_bucket_reveal_batch(inbox)

    assert batch.reveals == ()
    assert [path.name for path in batch.rejected_paths] == ["mixed.jsonl"]
    assert "exactly one tempo" in batch.rejections[0]

    archive_bucket_reveal_batch(batch)

    assert not (inbox / "mixed.jsonl").exists()
    assert (inbox / "rejected" / "mixed.jsonl").exists()


def test_bucket_reveal_validates_merkle_root_before_validator_scoring(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    reveal = _reveal(miner="hk-a", commit_block=10, ciphertext="cipher-a", proof=_proof("  trivial"))

    submissions, authenticated = submissions_from_bucket_reveals((reveal,), active_tasks)

    assert len(submissions) == 1
    assert submissions[0].metadata["bucket_key"] == "tempo_7/slot_0.bin"
    assert (submissions[0].task_id, "hk-a", submissions[0].proof_sha256) in authenticated

    bad = reveal.model_copy(update={"merkle_root": "1" * 64})
    try:
        submissions_from_bucket_reveals((bad,), active_tasks)
    except ValueError as e:
        assert "Merkle root mismatch" in str(e)
    else:
        raise AssertionError("bad Merkle root should fail closed")


def test_bucket_reveal_requires_matching_chain_commitment(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    reveal = _reveal(miner="hk-a", commit_block=10, ciphertext="cipher-a", proof=_proof("  trivial"))
    chain_commitments = {
        "hk-a": miner_bucket_commitment_payload(
            tempo=reveal.tempo,
            drand_round=reveal.drand_round,
            merkle_root=reveal.merkle_root,
        )
    }

    submissions, authenticated = submissions_from_bucket_reveals(
        (reveal,),
        active_tasks,
        chain_commitments=chain_commitments,
    )

    assert len(submissions) == 1
    assert (submissions[0].task_id, "hk-a", submissions[0].proof_sha256) in authenticated

    try:
        submissions_from_bucket_reveals((reveal,), active_tasks, chain_commitments={"hk-a": "wrong"})
    except ValueError as e:
        assert "chain commitment mismatch" in str(e)
    else:
        raise AssertionError("bad chain commitment should fail closed")


def test_bucket_reveal_can_verify_historical_chain_commitment(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    reveal = _reveal(miner="hk-a", commit_block=10, ciphertext="cipher-a", proof=_proof("  trivial"))
    historical = {
        10: {
            "hk-a": miner_bucket_commitment_payload(
                tempo=reveal.tempo,
                drand_round=reveal.drand_round,
                merkle_root=reveal.merkle_root,
            )
        },
        11: {"hk-a": "newer-commitment"},
    }

    submissions, authenticated = submissions_from_bucket_reveals(
        (reveal,),
        active_tasks,
        chain_commitments_by_block=historical,
    )

    assert len(submissions) == 1
    assert (submissions[0].task_id, "hk-a", submissions[0].proof_sha256) in authenticated


def test_bucket_reveal_requires_commit_block_for_historical_commitment(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    reveal = _reveal(miner="hk-a", commit_block=0, ciphertext="cipher-a", proof=_proof("  trivial"))

    try:
        submissions_from_bucket_reveals((reveal,), active_tasks, chain_commitments_by_block={})
    except ValueError as e:
        assert "missing chain commitment block" in str(e)
    else:
        raise AssertionError("historical commitment verification should require a commit block")


def test_bucket_reveal_can_skip_bad_chain_commitment_without_poisoning_batch(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    good = _reveal(miner="hk-good", commit_block=10, ciphertext="cipher-good", proof=_proof("  trivial"))
    bad = _reveal(miner="hk-bad", commit_block=11, ciphertext="cipher-bad", proof=_proof("  exact True.intro"))
    rejections: list[str] = []
    chain_commitments = {
        "hk-good": miner_bucket_commitment_payload(
            tempo=good.tempo,
            drand_round=good.drand_round,
            merkle_root=good.merkle_root,
        ),
        "hk-bad": "wrong",
    }

    submissions, authenticated = submissions_from_bucket_reveals(
        (bad, good),
        active_tasks,
        chain_commitments=chain_commitments,
        strict=False,
        rejection_log=rejections.append,
    )

    assert len(submissions) == 1
    assert submissions[0].solver_hotkey == "hk-good"
    assert (submissions[0].task_id, "hk-good", submissions[0].proof_sha256) in authenticated
    assert rejections == ["hk-bad: chain commitment mismatch"]


def test_bucket_reveal_can_verify_drand_decrypted_payload(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    proof = _proof("  trivial")
    reveal = _reveal(
        miner="hk-drand",
        commit_block=10,
        ciphertext="cipher-drand",
        proof=proof,
        drand_signature="0xsig",
    )

    submissions, _ = submissions_from_bucket_reveals(
        (reveal,),
        active_tasks,
        verify_drand=True,
        decrypt_timelocked=lambda ciphertext, signature: proof.encode("utf-8"),
    )

    assert submissions[0].proof_script == proof


def test_poll_bucket_reveals_builds_rows_from_public_bucket_and_chain_root(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    ciphertext = b"encrypted-proof"
    ciphertext_encoded = encode_ciphertext(ciphertext)
    merkle_root = miner_submission_merkle_root(((0, ciphertext_sha256(ciphertext)),))
    proof = _proof("  trivial")
    seen_urls: list[str] = []

    reveals = poll_bucket_reveals(
        miner_bucket_urls={"hk-a": "https://bucket.example/miner"},
        chain_commitments={
            "hk-a": miner_bucket_commitment_payload(tempo=7, drand_round=77, merkle_root=merkle_root)
        },
        commit_blocks={"hk-a": 123},
        active_tasks=active_tasks,
        tempo=7,
        drand_round=77,
        drand_signature="0xsig",
        get_object=lambda url: seen_urls.append(url) or ciphertext,
        decrypt_timelocked=lambda _ciphertext, _signature: proof.encode("utf-8"),
    )

    assert seen_urls == ["https://bucket.example/miner/tempo_7/slot_0.bin"]
    assert reveals[0].commit_block == 123
    assert reveals[0].blobs[0].ciphertext == ciphertext_encoded
    assert reveals[0].blobs[0].proof_script == proof


def test_poll_bucket_reveals_skips_bad_bucket_without_poisoning_batch(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    good_ciphertext = b"good-encrypted-proof"
    bad_ciphertext = b"bad-encrypted-proof"
    proof = _proof("  trivial")
    rejections: list[str] = []

    def get_object(url: str) -> bytes:
        return bad_ciphertext if "bad" in url else good_ciphertext

    def decrypt(ciphertext: str, _signature: str | None) -> bytes:
        if ciphertext_bytes(ciphertext) == bad_ciphertext:
            raise ValueError("bad miner ciphertext")
        return proof.encode("utf-8")

    reveals = poll_bucket_reveals(
        miner_bucket_urls={"hk-bad": "https://bucket.example/bad", "hk-good": "https://bucket.example/good"},
        chain_commitments={
            "hk-bad": miner_bucket_commitment_payload(
                tempo=7,
                drand_round=77,
                merkle_root=miner_submission_merkle_root(((0, ciphertext_sha256(bad_ciphertext)),)),
            ),
            "hk-good": miner_bucket_commitment_payload(
                tempo=7,
                drand_round=77,
                merkle_root=miner_submission_merkle_root(((0, ciphertext_sha256(good_ciphertext)),)),
            ),
        },
        active_tasks=active_tasks,
        tempo=7,
        drand_round=77,
        drand_signature="0xsig",
        get_object=get_object,
        decrypt_timelocked=decrypt,
        rejection_log=rejections.append,
    )

    assert [reveal.miner_hotkey for reveal in reveals] == ["hk-good"]
    assert rejections == ["hk-bad: bad miner ciphertext"]


def test_bucket_reveal_drand_verification_fails_closed(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    proof = _proof("  trivial")
    reveal = _reveal(
        miner="hk-drand",
        commit_block=10,
        ciphertext="cipher-drand",
        proof=proof,
        drand_signature="0xsig",
    )

    try:
        submissions_from_bucket_reveals(
            (reveal,),
            active_tasks,
            verify_drand=True,
            decrypt_timelocked=lambda ciphertext, signature: b"wrong proof",
        )
    except ValueError as e:
        assert "decrypted proof does not match reveal" in str(e)
    else:
        raise AssertionError("mismatched drand payload should fail closed")

    missing = reveal.model_copy(update={"drand_signature": None})
    try:
        submissions_from_bucket_reveals((missing,), active_tasks, verify_drand=True)
    except ValueError as e:
        assert "missing drand signature" in str(e)
    else:
        raise AssertionError("missing drand signature should fail closed")


def test_validator_accepts_chain_authenticated_reveals_and_ranks_by_commit_block(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    active_tasks = active_tasks_for_validation(registry, _settings(tmp_path), tempo=7)
    later = _reveal(miner="hk-late", commit_block=20, ciphertext="cipher-late", proof=_proof("  trivial"))
    earlier = _reveal(miner="hk-early", commit_block=10, ciphertext="cipher-early", proof=_proof("  exact True.intro"))
    submissions, authenticated = submissions_from_bucket_reveals((later, earlier), active_tasks)

    result = validate_once(
        _settings(tmp_path),
        submissions,
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        tempo=7,
        require_signatures=True,
        require_commit_reveal=True,
        no_set_weights=True,
        chain_authenticated_keys=authenticated,
    )

    assert result.score.winners == {task.id: "hk-early"}
    assert result.score.scores == {"hk-early": 1.0}
    assert [(row.solver_hotkey, row.rewarded, row.commit_block, row.drand_round) for row in result.corpus_rows] == [
        ("hk-early", True, 10, 77),
        ("hk-late", False, 20, 77),
    ]

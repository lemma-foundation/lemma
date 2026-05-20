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
from lemma.chain.miner_buckets import MinerBucketReveal, RevealedBucketBlob, submissions_from_bucket_reveals
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
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

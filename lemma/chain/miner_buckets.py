"""Miner bucket reveal artifacts for commitment-anchored validation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lemma.chain.commitments import (
    ciphertext_sha256,
    miner_bucket_commitment_payload,
    miner_bucket_key,
    miner_submission_merkle_root,
)
from lemma.chain.drand import decrypt_timelocked_payload
from lemma.submissions import LemmaSubmission, proof_sha256
from lemma.tasks import LemmaTask

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
ChainAuthenticatedKey = tuple[str, str, str]
DecryptTimelockedPayload = Callable[[str, str | None], bytes]
RejectionLog = Callable[[str], None]


class RevealedBucketBlob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_index: int = Field(ge=0)
    ciphertext: str = Field(min_length=1)
    proof_script: str = Field(min_length=1)


class MinerBucketReveal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tempo: int = Field(ge=0)
    miner_hotkey: str = Field(min_length=1)
    drand_round: int = Field(ge=0)
    drand_signature: str | None = None
    commit_block: int = Field(ge=0)
    commit_extrinsic_hash: str = Field(min_length=1)
    merkle_root: str
    bucket_url: str = ""
    blobs: tuple[RevealedBucketBlob, ...]

    @field_validator("merkle_root")
    @classmethod
    def _merkle_root_hex(cls, value: str) -> str:
        lowered = value.lower()
        if not _HEX64.fullmatch(lowered):
            raise ValueError("merkle_root must be a 64-char lowercase hex digest")
        return lowered


def read_bucket_reveals_jsonl(path: Path) -> tuple[MinerBucketReveal, ...]:
    reveals: list[MinerBucketReveal] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            reveals.append(MinerBucketReveal.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{path}:{no}: invalid miner bucket reveal: {e}") from e
    return tuple(reveals)


def submissions_from_bucket_reveals(
    reveals: tuple[MinerBucketReveal, ...],
    active_tasks: tuple[LemmaTask, ...],
    *,
    verify_drand: bool = False,
    chain_commitments: Mapping[str, str] | None = None,
    decrypt_timelocked: DecryptTimelockedPayload = decrypt_timelocked_payload,
    strict: bool = True,
    rejection_log: RejectionLog | None = None,
) -> tuple[tuple[LemmaSubmission, ...], frozenset[ChainAuthenticatedKey]]:
    submissions: list[LemmaSubmission] = []
    authenticated: set[ChainAuthenticatedKey] = set()
    for reveal in reveals:
        try:
            reveal_submissions, reveal_authenticated = _submissions_from_bucket_reveal(
                reveal,
                active_tasks,
                verify_drand=verify_drand,
                chain_commitments=chain_commitments,
                decrypt_timelocked=decrypt_timelocked,
            )
        except ValueError as e:
            if strict:
                raise
            if rejection_log is not None:
                rejection_log(str(e))
            continue
        submissions.extend(reveal_submissions)
        authenticated.update(reveal_authenticated)
    return tuple(submissions), frozenset(authenticated)


def _submissions_from_bucket_reveal(
    reveal: MinerBucketReveal,
    active_tasks: tuple[LemmaTask, ...],
    *,
    verify_drand: bool,
    chain_commitments: Mapping[str, str] | None,
    decrypt_timelocked: DecryptTimelockedPayload,
) -> tuple[tuple[LemmaSubmission, ...], frozenset[ChainAuthenticatedKey]]:
    pairs = _claimed_pairs(reveal)
    root = miner_submission_merkle_root(pairs)
    if root != reveal.merkle_root:
        raise ValueError(f"{reveal.miner_hotkey}: miner Merkle root mismatch")
    if chain_commitments is not None:
        expected = miner_bucket_commitment_payload(
            tempo=reveal.tempo,
            drand_round=reveal.drand_round,
            merkle_root=reveal.merkle_root,
        )
        if chain_commitments.get(reveal.miner_hotkey) != expected:
            raise ValueError(f"{reveal.miner_hotkey}: chain commitment mismatch")
    if verify_drand and not (reveal.drand_signature or "").strip():
        raise ValueError(f"{reveal.miner_hotkey}: missing drand signature")

    submissions: list[LemmaSubmission] = []
    authenticated: set[ChainAuthenticatedKey] = set()
    for blob in reveal.blobs:
        if blob.slot_index >= len(active_tasks):
            continue
        proof_script = _verified_proof_script(reveal, blob, verify_drand, decrypt_timelocked)
        task = active_tasks[blob.slot_index]
        submission = LemmaSubmission(
            task_id=task.id,
            task_version=task.task_version,
            target_sha256=task.target_sha256,
            solver_hotkey=reveal.miner_hotkey,
            proof_script=proof_script,
            proof_sha256=proof_sha256(proof_script),
            created_at=f"tempo:{reveal.tempo}:slot:{blob.slot_index}:commit:{reveal.commit_block}",
            timelock_ciphertext=blob.ciphertext,
            drand_round=reveal.drand_round,
            commit_block=reveal.commit_block,
            commit_extrinsic_hash=reveal.commit_extrinsic_hash,
            metadata={
                "bucket_key": miner_bucket_key(reveal.tempo, blob.slot_index),
                "bucket_url": reveal.bucket_url,
                "ciphertext_sha256": ciphertext_sha256(blob.ciphertext.encode("utf-8")),
                "commit_merkle_root": reveal.merkle_root,
                "slot_index": blob.slot_index,
                "tempo": reveal.tempo,
            },
        )
        submissions.append(submission)
        authenticated.add((submission.task_id, submission.solver_hotkey, submission.proof_sha256))
    return tuple(submissions), frozenset(authenticated)


def _verified_proof_script(
    reveal: MinerBucketReveal,
    blob: RevealedBucketBlob,
    verify_drand: bool,
    decrypt_timelocked: DecryptTimelockedPayload,
) -> str:
    if not verify_drand:
        return blob.proof_script
    try:
        decrypted = decrypt_timelocked(blob.ciphertext, reveal.drand_signature)
        proof_script = decrypted.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{reveal.miner_hotkey}: decrypted proof is not UTF-8") from e
    except Exception as e:
        raise ValueError(f"{reveal.miner_hotkey}: drand decrypt failed") from e
    if proof_script != blob.proof_script:
        raise ValueError(f"{reveal.miner_hotkey}: decrypted proof does not match reveal")
    return proof_script


def _claimed_pairs(reveal: MinerBucketReveal) -> tuple[tuple[int, str], ...]:
    seen: set[int] = set()
    pairs: list[tuple[int, str]] = []
    for blob in reveal.blobs:
        if blob.slot_index in seen:
            raise ValueError(f"{reveal.miner_hotkey}: duplicate slot index {blob.slot_index}")
        seen.add(blob.slot_index)
        pairs.append((blob.slot_index, ciphertext_sha256(blob.ciphertext.encode("utf-8"))))
    return tuple(sorted(pairs))

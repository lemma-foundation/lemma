"""Miner bucket publish/reveal artifacts for commitment-anchored validation."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lemma.chain.commitments import (
    ciphertext_sha256,
    miner_bucket_commitment_payload,
    miner_bucket_key,
    miner_submission_merkle_root,
    parse_miner_bucket_commitment_payload,
)
from lemma.chain.drand import (
    ciphertext_bytes,
    decrypt_timelocked_payload,
    encode_ciphertext,
    encrypt_timelocked_payload,
)
from lemma.submissions import LemmaSubmission, proof_sha256, validate_submission_for_task
from lemma.tasks import LemmaTask

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
ChainAuthenticatedKey = tuple[str, str, str]
DecryptTimelockedPayload = Callable[[str, str | None], bytes]
EncryptTimelockedPayload = Callable[[bytes, int], bytes]
GetBucketObject = Callable[[str], bytes | None]
PutBucketObject = Callable[[str, bytes], None]
RejectionLog = Callable[[str], None]


@dataclass(frozen=True)
class BucketRevealBatch:
    tempo: int | None
    reveals: tuple[MinerBucketReveal, ...]
    paths: tuple[Path, ...]
    stale_paths: tuple[Path, ...]
    rejected_paths: tuple[Path, ...] = ()
    rejections: tuple[str, ...] = ()


class RevealedBucketBlob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_index: int = Field(ge=0)
    ciphertext: str = Field(min_length=1)
    proof_script: str = Field(min_length=1)


class PublishedBucketBlob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_index: int = Field(ge=0)
    task_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    ciphertext: str = Field(min_length=1)
    ciphertext_sha256: str

    @field_validator("ciphertext_sha256")
    @classmethod
    def _ciphertext_sha256_hex(cls, value: str) -> str:
        lowered = value.lower()
        if not _HEX64.fullmatch(lowered):
            raise ValueError("ciphertext_sha256 must be a 64-char lowercase hex digest")
        return lowered


class MinerBucketPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tempo: int = Field(ge=0)
    miner_hotkey: str = Field(min_length=1)
    drand_round: int = Field(ge=0)
    bucket_url: str = ""
    merkle_root: str
    commitment_payload: str
    blobs: tuple[PublishedBucketBlob, ...]

    @field_validator("merkle_root")
    @classmethod
    def _merkle_root_hex(cls, value: str) -> str:
        lowered = value.lower()
        if not _HEX64.fullmatch(lowered):
            raise ValueError("merkle_root must be a 64-char lowercase hex digest")
        return lowered


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


def prepare_miner_bucket_publication(
    *,
    submissions: Sequence[LemmaSubmission],
    active_tasks: Sequence[LemmaTask],
    tempo: int,
    miner_hotkey: str,
    drand_round: int,
    bucket_url: str = "",
    encrypt_timelocked: EncryptTimelockedPayload = encrypt_timelocked_payload,
) -> MinerBucketPublication:
    """Build timelocked bucket objects plus the chain commitment payload."""
    by_task_id: dict[str, LemmaSubmission] = {}
    for submission in submissions:
        if submission.task_id in by_task_id:
            raise ValueError(f"{submission.task_id}: duplicate submission for bucket")
        by_task_id[submission.task_id] = submission
    blobs: list[PublishedBucketBlob] = []
    pairs: list[tuple[int, str]] = []
    for slot_index, task in enumerate(active_tasks):
        matched = by_task_id.get(task.id)
        if matched is None:
            continue
        validate_submission_for_task(matched, task)
        if matched.solver_hotkey != miner_hotkey:
            raise ValueError(f"{matched.task_id}: submission hotkey does not match miner_hotkey")
        ciphertext_raw = encrypt_timelocked(matched.proof_script.encode("utf-8"), drand_round)
        digest = ciphertext_sha256(ciphertext_raw)
        key = miner_bucket_key(tempo, slot_index)
        blobs.append(
            PublishedBucketBlob(
                slot_index=slot_index,
                task_id=task.id,
                key=key,
                ciphertext=encode_ciphertext(ciphertext_raw),
                ciphertext_sha256=digest,
            )
        )
        pairs.append((slot_index, digest))
    if not blobs:
        raise ValueError("no submissions matched active bucket slots")
    merkle_root = miner_submission_merkle_root(pairs)
    return MinerBucketPublication(
        tempo=tempo,
        miner_hotkey=miner_hotkey,
        drand_round=drand_round,
        bucket_url=bucket_url,
        merkle_root=merkle_root,
        commitment_payload=miner_bucket_commitment_payload(
            tempo=tempo,
            drand_round=drand_round,
            merkle_root=merkle_root,
        ),
        blobs=tuple(blobs),
    )


def write_miner_bucket_publication(publication: MinerBucketPublication, output_dir: Path) -> None:
    """Write uploadable bucket objects and a public-safe manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for blob in publication.blobs:
        target = output_dir / blob.key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ciphertext_bytes(blob.ciphertext))
    (output_dir / "manifest.json").write_text(
        publication.model_dump_json(indent=2, exclude={"blobs": {"__all__": {"ciphertext"}}}) + "\n",
        encoding="utf-8",
    )


def upload_miner_bucket_publication(publication: MinerBucketPublication, put_object: PutBucketObject) -> None:
    for blob in publication.blobs:
        put_object(blob.key, ciphertext_bytes(blob.ciphertext))


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


def read_bucket_reveal_file(path: Path) -> tuple[MinerBucketReveal, ...]:
    if path.suffix == ".jsonl":
        return read_bucket_reveals_jsonl(path)
    if path.suffix != ".json":
        raise ValueError(f"{path}: bucket reveal file must end in .json or .jsonl")
    try:
        return (MinerBucketReveal.model_validate(json.loads(path.read_text(encoding="utf-8"))),)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"{path}: invalid miner bucket reveal: {e}") from e


def latest_bucket_reveal_batch(reveal_dir: Path, *, before_tempo: int | None = None) -> BucketRevealBatch:
    if not reveal_dir.exists():
        return BucketRevealBatch(None, (), (), ())
    if not reveal_dir.is_dir():
        raise ValueError(f"{reveal_dir} is not a directory")
    by_path: list[tuple[Path, tuple[MinerBucketReveal, ...]]] = []
    rejected_paths: list[Path] = []
    rejections: list[str] = []
    for path in sorted(p for p in reveal_dir.iterdir() if p.is_file() and p.suffix in {".json", ".jsonl"}):
        try:
            reveals = read_bucket_reveal_file(path)
        except ValueError as e:
            rejected_paths.append(path)
            rejections.append(str(e))
            continue
        if not reveals:
            continue
        tempos = {reveal.tempo for reveal in reveals}
        if len(tempos) != 1:
            rejected_paths.append(path)
            rejections.append(f"{path}: bucket reveal file must contain exactly one tempo")
            continue
        by_path.append((path, reveals))
    eligible = tuple(
        (path, reveals) for path, reveals in by_path if before_tempo is None or reveals[0].tempo < before_tempo
    )
    if not eligible:
        return BucketRevealBatch(
            max((reveals[0].tempo for _, reveals in by_path), default=None),
            (),
            (),
            (),
            tuple(rejected_paths),
            tuple(rejections),
        )
    latest_tempo = max(reveals[0].tempo for _, reveals in eligible)
    selected_paths = tuple(path for path, reveals in eligible if reveals[0].tempo == latest_tempo)
    stale_paths = tuple(path for path, reveals in eligible if reveals[0].tempo != latest_tempo)
    selected = tuple(reveal for path, reveals in eligible if path in selected_paths for reveal in reveals)
    return BucketRevealBatch(
        latest_tempo,
        selected,
        selected_paths,
        stale_paths,
        tuple(rejected_paths),
        tuple(rejections),
    )


def archive_bucket_reveal_batch(batch: BucketRevealBatch) -> None:
    for path in batch.paths:
        _move_bucket_reveal_file(path, "processed")
    for path in batch.stale_paths:
        _move_bucket_reveal_file(path, "stale")
    for path in batch.rejected_paths:
        _move_bucket_reveal_file(path, "rejected")


def _move_bucket_reveal_file(path: Path, dirname: str) -> None:
    if not path.exists():
        return
    target_dir = path.parent / dirname
    target_dir.mkdir(exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}.{path.stat().st_mtime_ns}{path.suffix}"
    shutil.move(str(path), str(target))


def poll_bucket_reveals(
    *,
    miner_bucket_urls: Mapping[str, str],
    chain_commitments: Mapping[str, str],
    commit_blocks: Mapping[str, int] | None = None,
    active_tasks: tuple[LemmaTask, ...],
    tempo: int,
    drand_round: int,
    drand_signature: str,
    get_object: GetBucketObject | None = None,
    decrypt_timelocked: DecryptTimelockedPayload = decrypt_timelocked_payload,
    rejection_log: RejectionLog | None = None,
) -> tuple[MinerBucketReveal, ...]:
    """Build post-reveal rows by polling public miner buckets for active slot blobs."""
    getter = get_object or _http_get_object
    reveals: list[MinerBucketReveal] = []
    block_by_miner = commit_blocks or {}
    for miner_hotkey, bucket_url in sorted(miner_bucket_urls.items()):
        try:
            commitment = chain_commitments.get(miner_hotkey, "")
            committed_tempo, committed_round, merkle_root = parse_miner_bucket_commitment_payload(commitment)
            if committed_tempo != tempo or committed_round != drand_round:
                continue
            blobs: list[RevealedBucketBlob] = []
            for slot_index in range(len(active_tasks)):
                key = miner_bucket_key(tempo, slot_index)
                ciphertext_raw = getter(_bucket_object_url(bucket_url, key))
                if ciphertext_raw is None:
                    continue
                ciphertext = encode_ciphertext(ciphertext_raw)
                proof_script = decrypt_timelocked(ciphertext, drand_signature).decode("utf-8")
                blobs.append(
                    RevealedBucketBlob(slot_index=slot_index, ciphertext=ciphertext, proof_script=proof_script)
                )
            if not blobs:
                continue
            reveal = MinerBucketReveal(
                tempo=tempo,
                miner_hotkey=miner_hotkey,
                drand_round=drand_round,
                drand_signature=drand_signature,
                commit_block=max(0, int(block_by_miner.get(miner_hotkey, 0))),
                commit_extrinsic_hash=commitment,
                merkle_root=merkle_root,
                bucket_url=bucket_url,
                blobs=tuple(blobs),
            )
            if miner_submission_merkle_root(_claimed_pairs(reveal)) == merkle_root:
                reveals.append(reveal)
        except ValueError as e:
            if rejection_log is not None:
                rejection_log(f"{miner_hotkey}: {e}")
        except Exception as e:
            if rejection_log is not None:
                rejection_log(f"{miner_hotkey}: bucket poll failed: {e}")
    return tuple(reveals)


def submissions_from_bucket_reveals(
    reveals: tuple[MinerBucketReveal, ...],
    active_tasks: tuple[LemmaTask, ...],
    *,
    verify_drand: bool = False,
    chain_commitments: Mapping[str, str] | None = None,
    chain_commitments_by_block: Mapping[int, Mapping[str, str]] | None = None,
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
                chain_commitments_by_block=chain_commitments_by_block,
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
    chain_commitments_by_block: Mapping[int, Mapping[str, str]] | None,
    decrypt_timelocked: DecryptTimelockedPayload,
) -> tuple[tuple[LemmaSubmission, ...], frozenset[ChainAuthenticatedKey]]:
    pairs = _claimed_pairs(reveal)
    root = miner_submission_merkle_root(pairs)
    if root != reveal.merkle_root:
        raise ValueError(f"{reveal.miner_hotkey}: miner Merkle root mismatch")
    commitments = chain_commitments
    if chain_commitments_by_block is not None:
        if reveal.commit_block <= 0:
            raise ValueError(f"{reveal.miner_hotkey}: missing chain commitment block")
        commitments = chain_commitments_by_block.get(reveal.commit_block, {})
    if commitments is not None:
        expected = miner_bucket_commitment_payload(
            tempo=reveal.tempo,
            drand_round=reveal.drand_round,
            merkle_root=reveal.merkle_root,
        )
        if commitments.get(reveal.miner_hotkey) != expected:
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
                "ciphertext_sha256": ciphertext_sha256(ciphertext_bytes(blob.ciphertext)),
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
        pairs.append((blob.slot_index, ciphertext_sha256(ciphertext_bytes(blob.ciphertext))))
    return tuple(sorted(pairs))


def _bucket_object_url(bucket_url: str, key: str) -> str:
    base = bucket_url.rstrip("/") + "/"
    return urljoin(base, key)


def _http_get_object(url: str) -> bytes | None:
    import httpx

    response = httpx.get(url, timeout=20.0, follow_redirects=True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content

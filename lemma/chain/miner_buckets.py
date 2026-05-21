"""Miner bucket reveal artifacts for commitment-anchored validation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lemma.chain.commitments import (
    ciphertext_sha256,
    miner_bucket_commitment_payload,
    miner_bucket_key,
    miner_submission_merkle_root,
)
from lemma.chain.drand import decrypt_timelocked_payload, encode_ciphertext
from lemma.submissions import LemmaSubmission, proof_sha256
from lemma.tasks import LemmaTask

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
ChainAuthenticatedKey = tuple[str, str, str]
DecryptTimelockedPayload = Callable[[str, str | None], bytes]


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
    return _read_bucket_reveals_text(path.read_text(encoding="utf-8"), source=str(path))


def read_bucket_reveals_dir(root: Path) -> tuple[tuple[MinerBucketReveal, ...], tuple[Path, ...]]:
    paths = tuple(
        sorted(
            path
            for suffix in ("*.json", "*.jsonl")
            for path in root.rglob(suffix)
            if path.is_file() and "processed" not in path.parts
        )
    )
    reveals: list[MinerBucketReveal] = []
    for path in paths:
        reveals.extend(read_bucket_reveals_jsonl(path))
    return tuple(reveals), paths


def fetch_bucket_reveals_url(url: str, *, timeout_s: float) -> tuple[MinerBucketReveal, ...]:
    response = httpx.get(url, timeout=timeout_s, follow_redirects=True)
    response.raise_for_status()
    return _read_bucket_reveals_text(response.text, source=url)


def bucket_reveal_path(root: Path, *, tempo: int, miner_hotkey: str) -> Path:
    safe_hotkey = re.sub(r"[^0-9A-Za-z_.-]", "_", miner_hotkey)
    return root / f"tempo_{tempo}" / f"{safe_hotkey}.json"


def write_bucket_reveal(path: Path, reveal: MinerBucketReveal) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reveal.model_dump_json(indent=2) + "\n", encoding="utf-8")


def archive_bucket_reveals(paths: tuple[Path, ...], root: Path) -> None:
    from datetime import UTC, datetime

    processed = root / "processed"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for idx, path in enumerate(paths):
        if not path.exists():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = Path(path.name)
        target = processed / f"{stamp}-{idx:04d}" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)


def build_revealed_bucket_blob(*, slot_index: int, proof_script: str) -> RevealedBucketBlob:
    ciphertext = encode_ciphertext(proof_script.encode("utf-8"))
    return RevealedBucketBlob(slot_index=slot_index, ciphertext=ciphertext, proof_script=proof_script)


def build_bucket_reveal(
    *,
    tempo: int,
    miner_hotkey: str,
    drand_round: int,
    commit_block: int,
    commit_extrinsic_hash: str,
    blobs: tuple[RevealedBucketBlob, ...],
    bucket_url: str = "",
) -> MinerBucketReveal:
    return MinerBucketReveal(
        tempo=tempo,
        miner_hotkey=miner_hotkey,
        drand_round=drand_round,
        commit_block=commit_block,
        commit_extrinsic_hash=commit_extrinsic_hash,
        merkle_root=miner_submission_merkle_root(_blob_pairs(blobs)),
        bucket_url=bucket_url,
        blobs=blobs,
    )


def _read_bucket_reveals_text(text: str, *, source: str) -> tuple[MinerBucketReveal, ...]:
    reveals: list[MinerBucketReveal] = []
    stripped = text.strip()
    if not stripped:
        return ()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return (MinerBucketReveal.model_validate(payload),)
    if isinstance(payload, list):
        return tuple(MinerBucketReveal.model_validate(item) for item in payload)
    if payload is not None:
        raise ValueError(f"{source}: expected reveal object, array, or JSONL rows")

    for no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            reveals.append(MinerBucketReveal.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{source}:{no}: invalid miner bucket reveal: {e}") from e
    return tuple(reveals)


def submissions_from_bucket_reveals(
    reveals: tuple[MinerBucketReveal, ...],
    active_tasks: tuple[LemmaTask, ...],
    *,
    verify_drand: bool = False,
    chain_commitments: Mapping[str, str] | None = None,
    decrypt_timelocked: DecryptTimelockedPayload = decrypt_timelocked_payload,
) -> tuple[tuple[LemmaSubmission, ...], frozenset[ChainAuthenticatedKey]]:
    submissions: list[LemmaSubmission] = []
    authenticated: set[ChainAuthenticatedKey] = set()
    for reveal in reveals:
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
    return _blob_pairs(reveal.blobs, miner_hotkey=reveal.miner_hotkey)


def _blob_pairs(
    blobs: tuple[RevealedBucketBlob, ...],
    *,
    miner_hotkey: str = "miner",
) -> tuple[tuple[int, str], ...]:
    seen: set[int] = set()
    pairs: list[tuple[int, str]] = []
    for blob in blobs:
        if blob.slot_index in seen:
            raise ValueError(f"{miner_hotkey}: duplicate slot index {blob.slot_index}")
        seen.add(blob.slot_index)
        pairs.append((blob.slot_index, ciphertext_sha256(blob.ciphertext.encode("utf-8"))))
    return tuple(sorted(pairs))

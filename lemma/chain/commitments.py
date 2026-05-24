"""Commitment envelopes for proof reveals and corpus storage roots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_PATH = re.compile("/" + "Users" + r"/[^\s]+")
_ROOT_LOGIN = re.compile("".join(("ro", "ot")) + r"@[^\s]+")
_IP_ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SS58_ADDRESS = re.compile(r"\b5[1-9A-HJ-NP-Za-km-z]{40,}\b")
_TEMPO_FILE = re.compile(r"tempo-(\d+)\.json$")
_STORAGE_PREFIX = "lemma-storage-v1"
_ACTIVE_POOL_PREFIX = "lemma-active-pool-v1"
_TEMPO_PREFIX = "lemma-tempo-v1"
_TEMPO_CID_PREFIX = "lemma-tempo-cid-v1"
_MINER_BUCKET_PREFIX = "lemma-bucket"


class CommitmentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    task_version: int = Field(default=1, ge=1)
    target_sha256: str
    miner_hotkey: str
    drand_round: int = Field(ge=0)
    ciphertext_sha256: str
    commit_block: int = Field(ge=0)
    extrinsic_hash: str

    def rank_key(self, tie_break_seed: str) -> tuple[int, str]:
        digest = hashlib.sha256(f"{tie_break_seed}:{self.extrinsic_hash}".encode()).hexdigest()
        return self.commit_block, digest

    def signing_payload(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))


def ciphertext_sha256(ciphertext: bytes) -> str:
    return hashlib.sha256(ciphertext).hexdigest()


def miner_bucket_key(tempo: int, slot_index: int) -> str:
    if tempo < 0:
        raise ValueError("tempo must be non-negative")
    if slot_index < 0:
        raise ValueError("slot_index must be non-negative")
    return f"tempo_{tempo}/slot_{slot_index}.bin"


def miner_submission_leaf_hash(slot_index: int, ciphertext_digest: str) -> str:
    if slot_index < 0:
        raise ValueError("slot_index must be non-negative")
    digest = ciphertext_digest.lower()
    if not _HEX64.fullmatch(digest):
        raise ValueError("ciphertext digest must be a 64-char lowercase hex digest")
    payload = json.dumps(
        {"ciphertext_sha256": digest, "slot_index": slot_index},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def miner_submission_merkle_root(entries: Iterable[tuple[int, str]]) -> str:
    leaves = [miner_submission_leaf_hash(slot, digest) for slot, digest in sorted(entries)]
    if not leaves:
        return ciphertext_sha256(b"")
    level = [bytes.fromhex(item) for item in leaves]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [hashlib.sha256(level[index] + level[index + 1]).digest() for index in range(0, len(level), 2)]
    return level[0].hex()


def miner_bucket_commitment_payload(*, tempo: int, drand_round: int, merkle_root: str) -> str:
    if tempo < 0:
        raise ValueError("tempo must be non-negative")
    if drand_round < 0:
        raise ValueError("drand_round must be non-negative")
    root = merkle_root.lower()
    if not _HEX64.fullmatch(root):
        raise ValueError("merkle_root must be a 64-char lowercase hex digest")
    return f"{_MINER_BUCKET_PREFIX}:{tempo}:{drand_round}:{root}"


def parse_miner_bucket_commitment_payload(payload: str) -> tuple[int, int, str]:
    parts = payload.strip().split(":")
    if len(parts) != 4 or parts[0] != _MINER_BUCKET_PREFIX:
        raise ValueError("not a lemma miner bucket commitment")
    try:
        tempo = int(parts[1])
        drand_round = int(parts[2])
    except ValueError as e:
        raise ValueError("miner bucket commitment has invalid tempo or drand round") from e
    merkle_root = parts[3].lower()
    if tempo < 0 or drand_round < 0 or not _HEX64.fullmatch(merkle_root):
        raise ValueError("miner bucket commitment payload is invalid")
    return tempo, drand_round, merkle_root


@dataclass(frozen=True)
class ChainCommitmentSubmission:
    success: bool
    payload: str
    hotkey: str = ""
    message: str = ""
    extrinsic_function: str = ""
    extrinsic_hash: str = ""
    block_hash: str = ""
    block_number: int | None = None
    extrinsic_fee_rao: int | None = None


def storage_commitment_files(repo: Path, netuid: str) -> list[Path]:
    root = repo / "canonical" / netuid / "commitments"
    if not root.is_dir():
        raise SystemExit(f"missing storage commitments directory: {root}")
    files = sorted(root.glob("tempo-*.json"))
    if not files:
        raise SystemExit(f"no storage commitment files under {root}")
    return files


def latest_storage_commitment_file(repo: Path, netuid: str) -> Path:
    def key(path: Path) -> int:
        match = _TEMPO_FILE.fullmatch(path.name)
        if not match:
            return -1
        return int(match.group(1))

    return max(storage_commitment_files(repo, netuid), key=key)


def storage_commitment_file(repo: Path, netuid: str, tempo: int | None = None) -> Path:
    if tempo is None:
        return latest_storage_commitment_file(repo, netuid)
    path = repo / "canonical" / netuid / "commitments" / f"tempo-{tempo:06d}.json"
    if not path.is_file():
        raise SystemExit(f"missing storage commitment file: {path}")
    return path


def storage_commitment_preimage(
    *, netuid: object, tempo: object, tempo_directory_sha256: str, accepted_merkle_root: str
) -> str:
    return f"{_STORAGE_PREFIX}:{netuid}:{tempo}:{tempo_directory_sha256}:{accepted_merkle_root}"


def compact_storage_commitment_payload(
    *, netuid: object, tempo: object, tempo_directory_sha256: str, accepted_merkle_root: str
) -> str:
    preimage = storage_commitment_preimage(
        netuid=netuid,
        tempo=tempo,
        tempo_directory_sha256=tempo_directory_sha256,
        accepted_merkle_root=accepted_merkle_root,
    )
    return f"{_STORAGE_PREFIX}:{netuid}:{tempo}:{hashlib.sha256(preimage.encode('utf-8')).hexdigest()}"


def active_pool_commitment_preimage(*, netuid: object, tempo: object, active_pool_directory_sha256: str) -> str:
    return f"{_ACTIVE_POOL_PREFIX}:{netuid}:{tempo}:{active_pool_directory_sha256}"


def compact_active_pool_commitment_payload(*, netuid: object, tempo: object, active_pool_directory_sha256: str) -> str:
    preimage = active_pool_commitment_preimage(
        netuid=netuid,
        tempo=tempo,
        active_pool_directory_sha256=active_pool_directory_sha256,
    )
    return f"{_ACTIVE_POOL_PREFIX}:{netuid}:{tempo}:{hashlib.sha256(preimage.encode('utf-8')).hexdigest()}"


def tempo_commitment_preimage(
    *,
    netuid: object,
    tempo: object,
    active_pool_directory_sha256: str,
    accepted_directory_sha256: str,
    accepted_merkle_root: str,
) -> str:
    return (
        f"{_TEMPO_PREFIX}:{netuid}:{tempo}:{active_pool_directory_sha256}:"
        f"{accepted_directory_sha256}:{accepted_merkle_root}"
    )


def compact_tempo_commitment_payload(
    *,
    netuid: object,
    tempo: object,
    active_pool_directory_sha256: str,
    accepted_directory_sha256: str,
    accepted_merkle_root: str,
) -> str:
    preimage = tempo_commitment_preimage(
        netuid=netuid,
        tempo=tempo,
        active_pool_directory_sha256=active_pool_directory_sha256,
        accepted_directory_sha256=accepted_directory_sha256,
        accepted_merkle_root=accepted_merkle_root,
    )
    return f"{_TEMPO_PREFIX}:{netuid}:{tempo}:{hashlib.sha256(preimage.encode('utf-8')).hexdigest()}"


def tempo_cid_commitment_preimage(
    *,
    netuid: object,
    tempo: object,
    active_pool_directory_cid: str,
    active_pool_directory_sha256: str,
    accepted_directory_cid: str,
    accepted_directory_sha256: str,
    accepted_merkle_root: str,
) -> str:
    return (
        f"{_TEMPO_CID_PREFIX}:{netuid}:{tempo}:{active_pool_directory_cid}:"
        f"{active_pool_directory_sha256}:{accepted_directory_cid}:"
        f"{accepted_directory_sha256}:{accepted_merkle_root}"
    )


def compact_tempo_cid_commitment_payload(
    *,
    netuid: object,
    tempo: object,
    active_pool_directory_cid: str,
    active_pool_directory_sha256: str,
    accepted_directory_cid: str,
    accepted_directory_sha256: str,
    accepted_merkle_root: str,
) -> str:
    preimage = tempo_cid_commitment_preimage(
        netuid=netuid,
        tempo=tempo,
        active_pool_directory_cid=_cid(active_pool_directory_cid),
        active_pool_directory_sha256=active_pool_directory_sha256,
        accepted_directory_cid=_cid(accepted_directory_cid),
        accepted_directory_sha256=accepted_directory_sha256,
        accepted_merkle_root=accepted_merkle_root,
    )
    return f"{_TEMPO_CID_PREFIX}:{netuid}:{tempo}:{hashlib.sha256(preimage.encode('utf-8')).hexdigest()}"


def _cid(value: object) -> str:
    cid = str(value).strip()
    if not cid or any(char.isspace() for char in cid):
        raise ValueError("CID must be non-empty and contain no whitespace")
    return cid


def load_storage_commitment(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    expected = {
        "schema_version",
        "accepted_merkle_root",
        "commitment_payload",
        "netuid",
        "resolver",
        "tempo",
        "tempo_directory_sha256",
    }
    missing = sorted(expected - set(data))
    if missing:
        raise ValueError(f"{path}: missing fields: {', '.join(missing)}")
    accepted_merkle_root = str(data["accepted_merkle_root"])
    tempo_directory_sha256 = str(data["tempo_directory_sha256"])
    if not _HEX64.fullmatch(accepted_merkle_root):
        raise ValueError(f"{path}: accepted_merkle_root must be a 64-char lowercase hex digest")
    if not _HEX64.fullmatch(tempo_directory_sha256):
        raise ValueError(f"{path}: tempo_directory_sha256 must be a 64-char lowercase hex digest")
    payload = str(data["commitment_payload"])
    legacy_payload = storage_commitment_preimage(
        netuid=data["netuid"],
        tempo=data["tempo"],
        tempo_directory_sha256=tempo_directory_sha256,
        accepted_merkle_root=accepted_merkle_root,
    )
    compact_payload = compact_storage_commitment_payload(
        netuid=data["netuid"],
        tempo=data["tempo"],
        tempo_directory_sha256=tempo_directory_sha256,
        accepted_merkle_root=accepted_merkle_root,
    )
    accepted_payloads = {legacy_payload, compact_payload}
    active_pool_directory_sha256 = str(data.get("active_pool_directory_sha256") or "")
    active_pool_directory_cid = str(data.get("active_pool_directory_cid") or "")
    accepted_directory_cid = str(data.get("tempo_directory_cid") or "")
    tempo_payload = str(data.get("tempo_commitment_payload") or "")
    if active_pool_directory_sha256:
        if not _HEX64.fullmatch(active_pool_directory_sha256):
            raise ValueError(f"{path}: active_pool_directory_sha256 must be a 64-char lowercase hex digest")
        expected_tempo_payload = (
            compact_tempo_cid_commitment_payload(
                netuid=data["netuid"],
                tempo=data["tempo"],
                active_pool_directory_cid=active_pool_directory_cid,
                active_pool_directory_sha256=active_pool_directory_sha256,
                accepted_directory_cid=accepted_directory_cid,
                accepted_directory_sha256=tempo_directory_sha256,
                accepted_merkle_root=accepted_merkle_root,
            )
            if active_pool_directory_cid and accepted_directory_cid
            else compact_tempo_commitment_payload(
                netuid=data["netuid"],
                tempo=data["tempo"],
                active_pool_directory_sha256=active_pool_directory_sha256,
                accepted_directory_sha256=tempo_directory_sha256,
                accepted_merkle_root=accepted_merkle_root,
            )
        )
        if tempo_payload and tempo_payload != expected_tempo_payload:
            raise ValueError(f"{path}: tempo_commitment_payload mismatch")
        accepted_payloads.add(expected_tempo_payload)
    if payload not in accepted_payloads:
        raise ValueError(f"{path}: commitment_payload mismatch")
    return data


def storage_commitment_payload(path: Path) -> str:
    data = load_storage_commitment(path)
    active_pool_directory_sha256 = str(data.get("active_pool_directory_sha256") or "")
    if active_pool_directory_sha256:
        active_pool_directory_cid = str(data.get("active_pool_directory_cid") or "")
        accepted_directory_cid = str(data.get("tempo_directory_cid") or "")
        if active_pool_directory_cid and accepted_directory_cid:
            return compact_tempo_cid_commitment_payload(
                netuid=data["netuid"],
                tempo=data["tempo"],
                active_pool_directory_cid=active_pool_directory_cid,
                active_pool_directory_sha256=active_pool_directory_sha256,
                accepted_directory_cid=accepted_directory_cid,
                accepted_directory_sha256=str(data["tempo_directory_sha256"]),
                accepted_merkle_root=str(data["accepted_merkle_root"]),
            )
        return compact_tempo_commitment_payload(
            netuid=data["netuid"],
            tempo=data["tempo"],
            active_pool_directory_sha256=active_pool_directory_sha256,
            accepted_directory_sha256=str(data["tempo_directory_sha256"]),
            accepted_merkle_root=str(data["accepted_merkle_root"]),
        )
    return compact_storage_commitment_payload(
        netuid=data["netuid"],
        tempo=data["tempo"],
        tempo_directory_sha256=str(data["tempo_directory_sha256"]),
        accepted_merkle_root=str(data["accepted_merkle_root"]),
    )


def _response_message(response: object) -> str:
    message = getattr(response, "message", "") or getattr(response, "error", "")
    if not message:
        return ""
    text = str(message)
    text = _LOCAL_PATH.sub("[local-path]", text)
    text = _ROOT_LOGIN.sub("[ssh-login]", text)
    text = _IP_ADDRESS.sub("[ip-address]", text)
    text = _SS58_ADDRESS.sub("[ss58-address]", text)
    return text[:300]


def _receipt_value(response: object, field: str) -> object:
    receipt = getattr(response, "extrinsic_receipt", None)
    return getattr(receipt, field, None) if receipt else None


def _receipt_int_value(response: object, field: str) -> int | None:
    value = _receipt_value(response, field)
    return value if isinstance(value, int) else None


def _commitment_block_number(response: object, subtensor: object) -> int | None:
    direct = _receipt_int_value(response, "block_number")
    if direct is not None:
        return direct
    block_hash = _receipt_value(response, "block_hash")
    if not block_hash:
        return None
    substrate = getattr(subtensor, "substrate", None)
    getter = getattr(substrate, "get_block_number", None)
    if not callable(getter):
        return None
    try:
        block_number = getter(block_hash)
    except Exception:
        return None
    return block_number if isinstance(block_number, int) else None


def wallet_hotkey_address(settings: LemmaSettings) -> str:
    import bittensor as bt

    wallet = bt.Wallet(name=settings.wallet_cold, hotkey=settings.wallet_hot)
    return str(wallet.hotkey.ss58_address)


def submit_storage_commitment(settings: LemmaSettings, payload: str) -> ChainCommitmentSubmission:
    import bittensor as bt

    wallet = bt.Wallet(name=settings.wallet_cold, hotkey=settings.wallet_hot)
    subtensor = bt.Subtensor(network=settings.bt_network or None)
    response = subtensor.set_commitment(
        wallet=wallet,
        netuid=settings.netuid,
        data=payload,
        raise_error=False,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    return ChainCommitmentSubmission(
        success=bool(response.success),
        payload=payload,
        hotkey=str(wallet.hotkey.ss58_address),
        message=_response_message(response),
        extrinsic_function=str(response.extrinsic_function or ""),
        extrinsic_hash=str(_receipt_value(response, "extrinsic_hash") or ""),
        block_hash=str(_receipt_value(response, "block_hash") or ""),
        block_number=_commitment_block_number(response, subtensor),
        extrinsic_fee_rao=getattr(response.extrinsic_fee, "rao", None) if response.extrinsic_fee else None,
    )


def read_storage_commitment(settings: LemmaSettings, hotkey: str | None = None) -> str:
    target_hotkey = hotkey or wallet_hotkey_address(settings)
    return str(read_all_commitments(settings).get(target_hotkey, ""))


def read_all_commitments(settings: LemmaSettings, *, block: int | None = None) -> dict[str, str]:
    import bittensor as bt

    subtensor = bt.Subtensor(network=settings.bt_network or None)
    commitments: Mapping[str, str] = subtensor.get_all_commitments(settings.netuid, block=block)
    return {str(hotkey): str(payload) for hotkey, payload in commitments.items()}

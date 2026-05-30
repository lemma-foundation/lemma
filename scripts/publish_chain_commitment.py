#!/usr/bin/env python3
"""Publish a corpus storage root through Bittensor set_commitment."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lemma.chain.commitments import (  # noqa: E402
    read_storage_commitment,
    storage_commitment_file,
    storage_commitment_payload,
    submit_storage_commitment,
    wallet_hotkey_address,
)
from lemma.common.config import LemmaSettings  # noqa: E402


def _settings(args: argparse.Namespace) -> LemmaSettings:
    overrides: dict[str, object] = {}
    if args.bt_netuid is not None:
        overrides["netuid"] = args.bt_netuid
    if args.bt_network:
        overrides["bt_network"] = args.bt_network
    if args.wallet_cold:
        overrides["wallet_cold"] = args.wallet_cold
    if args.wallet_hot:
        overrides["wallet_hot"] = args.wallet_hot
    return LemmaSettings(**overrides)


def _readback_or_fail(
    settings: LemmaSettings,
    payload: str,
    hotkey: str | None,
    *,
    require_match: bool = True,
    retries: int = 1,
    delay_seconds: float = 0.5,
) -> tuple[str, bool]:
    readback = ""
    matches = False
    for attempt in range(1, max(1, retries) + 1):
        readback = read_storage_commitment(settings, hotkey=hotkey)
        matches = readback == payload
        if matches:
            break
        if attempt < retries:
            time.sleep(delay_seconds)
    return readback, matches if require_match else True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="lemma-corpus checkout")
    parser.add_argument("--netuid", default="sn467", help="corpus namespace, for example sn467")
    parser.add_argument("--tempo", type=int, help="tempo/epoch number to publish; default is latest")
    parser.add_argument("--bt-netuid", type=int, help="Bittensor subnet id; defaults to BT_NETUID")
    parser.add_argument("--bt-network", help="Bittensor network; defaults to BT_NETWORK")
    parser.add_argument("--wallet-cold", help="Bittensor cold wallet name; defaults to BT_WALLET_COLD")
    parser.add_argument("--wallet-hot", help="Bittensor hotkey name; defaults to BT_WALLET_HOT")
    parser.add_argument("--hotkey", help="hotkey SS58 address to read back without loading a wallet")
    parser.add_argument("--submit", action="store_true", help="write the commitment on chain")
    parser.add_argument("--readback", action="store_true", help="read back the current commitment for this hotkey")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    path = storage_commitment_file(repo, args.netuid, args.tempo)
    payload = storage_commitment_payload(path)
    settings = _settings(args)

    result: dict[str, object] = {
        "bt_netuid": settings.netuid,
        "commitment_file": str(path),
        "dry_run": not args.submit,
        "netuid": args.netuid,
        "payload": payload,
        "payload_bytes": len(payload.encode("utf-8")),
    }

    readback_hotkey = args.hotkey
    if args.readback and readback_hotkey:
        result["readback_hotkey_address"] = readback_hotkey
    elif args.readback and not args.submit:
        readback_hotkey = wallet_hotkey_address(settings)
        result["wallet_hotkey_address"] = readback_hotkey

    if args.submit:
        if settings.netuid == 0:
            raise SystemExit("refusing to submit with BT_NETUID=0; pass --bt-netuid or set BT_NETUID")
        submission = submit_storage_commitment(settings, payload)
        readback_hotkey = readback_hotkey or submission.hotkey
        result["wallet_hotkey_address"] = submission.hotkey
        result["submission"] = {
            "block_hash": submission.block_hash,
            "block_number": submission.block_number,
            "extrinsic_fee_rao": submission.extrinsic_fee_rao,
            "extrinsic_function": submission.extrinsic_function,
            "extrinsic_hash": submission.extrinsic_hash,
            "message": submission.message,
            "success": submission.success,
        }
        if not submission.success:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1
        readback, matches = _readback_or_fail(
            settings,
            payload,
            hotkey=readback_hotkey,
            require_match=True,
            retries=2,
            delay_seconds=1.0,
        )
        result["readback_matches"] = matches
        result["readback_payload"] = readback
        if not matches:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.readback or args.submit:
        readback = read_storage_commitment(settings, hotkey=readback_hotkey)
        result["readback_matches"] = readback == payload
        result["readback_payload"] = readback

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

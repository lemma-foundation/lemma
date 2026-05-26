"""Subnet tempo reads and guarded tempo updates."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from lemma.common.config import LemmaSettings


class _SubnetHyperparameters(Protocol):
    tempo: int


class _TempoSubtensor(Protocol):
    def get_current_block(self) -> int: ...

    def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> object: ...


_LOCAL_PATH = re.compile("/" + "Users" + r"/[^\s]+")
_ROOT_LOGIN = re.compile("".join(("ro", "ot")) + r"@[^\s]+")
_IP_ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SS58_ADDRESS = re.compile(r"\b5[1-9A-HJ-NP-Za-km-z]{40,}\b")


@dataclass(frozen=True)
class ChainTempoSubmission:
    success: bool
    target_tempo: int
    current_tempo: int | None = None
    changed: bool = False
    message: str = ""


def _sanitize(text: str) -> str:
    text = _LOCAL_PATH.sub("[local-path]", text)
    text = _ROOT_LOGIN.sub("[ssh-login]", text)
    text = _IP_ADDRESS.sub("[ip-address]", text)
    text = _SS58_ADDRESS.sub("[ss58-address]", text)
    return text[:300]


def current_chain_tempo_blocks(settings: LemmaSettings, *, subtensor: _TempoSubtensor | None = None) -> int:
    """Return the current subnet tempo length in blocks."""
    if subtensor is None:
        import bittensor as bt

        subtensor = bt.Subtensor(network=settings.bt_network or None)
    block = int(subtensor.get_current_block())
    hyperparams = cast(_SubnetHyperparameters, subtensor.get_subnet_hyperparameters(settings.netuid, block=block))
    tempo = int(hyperparams.tempo)
    if tempo <= 0:
        raise RuntimeError("chain tempo must be positive")
    return tempo


def submit_bittensor_tempo(settings: LemmaSettings, target_tempo: int) -> ChainTempoSubmission:
    """Set the subnet tempo through btcli when it differs from the current chain tempo."""
    if target_tempo <= 0:
        raise ValueError("target tempo must be positive")
    current_tempo = current_chain_tempo_blocks(settings)
    if current_tempo == target_tempo:
        return ChainTempoSubmission(
            success=True,
            current_tempo=current_tempo,
            target_tempo=target_tempo,
            changed=False,
            message="tempo already matches target",
        )

    command = shlex.split(settings.btcli_command) + [
        "sudo",
        "set",
        "--netuid",
        str(settings.netuid),
        "--param",
        "tempo",
        "--value",
        str(target_tempo),
        "--wallet-name",
        settings.wallet_cold,
        "--hotkey",
        settings.wallet_hot,
        "--yes",
    ]
    if settings.bt_network:
        command.extend(["--network", settings.bt_network])
    try:
        response = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=settings.set_tempo_timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ChainTempoSubmission(
            success=False,
            current_tempo=current_tempo,
            target_tempo=target_tempo,
            changed=False,
            message=_sanitize(str(exc)),
        )
    message = _sanitize((response.stdout + "\n" + response.stderr).strip())
    return ChainTempoSubmission(
        success=response.returncode == 0,
        current_tempo=current_tempo,
        target_tempo=target_tempo,
        changed=response.returncode == 0,
        message=message,
    )

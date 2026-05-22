"""Drand timelock helpers."""

from __future__ import annotations

import base64
import binascii

from pydantic import BaseModel, ConfigDict, Field


class DrandRevealPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_round: int = Field(ge=0)
    reveal_round: int = Field(ge=0)
    chain_hash: str = ""

    @property
    def ready(self) -> bool:
        return self.current_round >= self.reveal_round


def ciphertext_bytes(ciphertext: str) -> bytes:
    text = ciphertext.strip()
    if text.startswith("0x"):
        try:
            return bytes.fromhex(text[2:])
        except ValueError as e:
            raise ValueError("ciphertext hex is invalid") from e
    if text.startswith("base64:"):
        encoded = text.removeprefix("base64:")
        try:
            return base64.b64decode(encoded, validate=True)
        except binascii.Error as e:
            raise ValueError("ciphertext base64 is invalid") from e
    return text.encode("utf-8")


def encode_ciphertext(ciphertext: bytes) -> str:
    return "base64:" + base64.b64encode(ciphertext).decode("ascii")


def encrypt_timelocked_payload(payload: bytes, reveal_round: int) -> bytes:
    """Encrypt bytes so they can be opened at one Drand round."""
    import bittensor_drand

    encrypted, actual_round = bittensor_drand.encrypt_at_round(payload, reveal_round)
    if int(actual_round) != reveal_round:
        raise ValueError(f"drand encrypt returned round {actual_round}, expected {reveal_round}")
    return bytes(encrypted)


def decrypt_timelocked_payload(ciphertext: str, signature_hex: str | None = None) -> bytes:
    """Decrypt a bittensor-drand ciphertext after its reveal round."""
    import bittensor_drand

    raw = ciphertext_bytes(ciphertext)
    if signature_hex:
        return bytes(bittensor_drand.decrypt_with_signature(raw, signature_hex))
    decrypted = bittensor_drand.decrypt(raw, no_errors=False)
    if decrypted is None:
        raise ValueError("drand reveal is not available")
    return bytes(decrypted)

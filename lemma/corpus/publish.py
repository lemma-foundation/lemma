"""Publish canonical Proof Atlas artifacts to S3-compatible storage."""

from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class PublishedPath:
    local_path: str
    s3_uri: str
    sha256: str


@dataclass(frozen=True)
class IpfsDirectory:
    path: str
    cid: str
    file_count: int


def aws_command(value: str = "") -> list[str]:
    if value.strip():
        return shlex.split(value)
    if aws := shutil.which("aws"):
        return [aws]
    if uvx := shutil.which("uvx"):
        return [uvx, "--from", "awscli", "aws"]
    raise RuntimeError("missing aws CLI; install awscli or uv, or set LEMMA_CANONICAL_PUBLISH_AWS_COMMAND")


def publish_paths_to_s3(
    paths: Sequence[Path],
    *,
    root: Path,
    s3_uri: str,
    endpoint_url: str,
    aws: list[str],
    verify: bool,
) -> tuple[PublishedPath, ...]:
    published: list[PublishedPath] = []
    for file_path in _files(paths):
        relative = file_path.relative_to(root).as_posix()
        destination = _s3_join(s3_uri, relative)
        _run([*aws, "s3", "cp", str(file_path), destination, "--endpoint-url", endpoint_url, "--only-show-errors"])
        local_hash = _sha256(file_path.read_bytes())
        if verify:
            body = _run(
                [*aws, "s3", "cp", destination, "-", "--endpoint-url", endpoint_url, "--only-show-errors"],
                capture_output=True,
            )
            remote_hash = _sha256(body)
            if remote_hash != local_hash:
                raise RuntimeError(f"published artifact hash mismatch: {destination}")
        published.append(PublishedPath(local_path=relative, s3_uri=destination, sha256=local_hash))
    return tuple(published)


def add_directory_to_ipfs(
    directory: Path,
    *,
    api_url: str,
    verify: bool,
    timeout_s: float,
) -> IpfsDirectory:
    entries = _relative_files(directory)
    if not entries:
        raise RuntimeError(f"cannot publish empty directory to IPFS: {directory}")
    files = [
        ("file", (relative, path.read_bytes(), "application/octet-stream"))
        for relative, path in entries
    ]
    response = httpx.post(
        _ipfs_api(api_url, "add"),
        params={"recursive": "true", "cid-version": "1", "wrap-with-directory": "true"},
        files=files,
        timeout=timeout_s,
    )
    response.raise_for_status()
    cid = _last_ipfs_hash(response.text)
    if verify:
        for relative, path in entries:
            readback = httpx.post(
                _ipfs_api(api_url, "cat"),
                params={"arg": f"{cid}/{relative}"},
                timeout=timeout_s,
            )
            readback.raise_for_status()
            if readback.content != path.read_bytes():
                raise RuntimeError(f"IPFS readback mismatch: {directory / relative}")
    return IpfsDirectory(path=str(directory), cid=cid, file_count=len(entries))


def _files(paths: Sequence[Path]) -> tuple[Path, ...]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(item for item in sorted(path.rglob("*")) if item.is_file())
        elif path.is_file():
            out.append(path)
        else:
            raise RuntimeError(f"missing publish path: {path}")
    return tuple(out)


def _relative_files(directory: Path) -> tuple[tuple[str, Path], ...]:
    return tuple(
        (path.relative_to(directory).as_posix(), path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    )


def _s3_join(base: str, relative: str) -> str:
    return base.rstrip("/") + "/" + relative.lstrip("/")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ipfs_api(api_url: str, command: str) -> str:
    return api_url.rstrip("/") + f"/api/v0/{command}"


def _last_ipfs_hash(text: str) -> str:
    cid = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        value = payload.get("Hash")
        if isinstance(value, str) and value.strip():
            cid = value.strip()
    if not cid:
        raise RuntimeError("IPFS add response did not include a CID")
    return cid


def _run(command: list[str], *, capture_output: bool = False) -> bytes:
    completed = subprocess.run(command, check=True, capture_output=capture_output)  # noqa: S603
    return completed.stdout if capture_output else b""

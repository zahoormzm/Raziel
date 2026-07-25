"""Streaming source hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO


DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024


def sha256_stream(stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    source = Path(path)
    with source.open("rb") as handle:
        return sha256_stream(handle, chunk_size)

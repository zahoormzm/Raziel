"""Content-addressed, append-durable embedding and checkpoint stores."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Path-segment length for content-addressed artifact directories. See
# ArtifactGeneration.directory for why this is shorter than the digest.
_PATH_SEGMENT_CHARS = 32


def content_key(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = (canonical_json(payload) + "\n").encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass(frozen=True)
class ArtifactGeneration:
    schema_version: str
    kind: str
    inputs: Mapping[str, Any]

    @property
    def key(self) -> str:
        return content_key(
            {
                "schema_version": self.schema_version,
                "kind": self.kind,
                "inputs": self.inputs,
            }
        )

    def directory(self, root: str | Path) -> Path:
        """Content-addressed directory for this generation.

        The path segment is a prefix of the key, not the whole digest. Windows
        caps a path at 260 characters unless long paths are explicitly enabled,
        and a 64-character segment spends a quarter of that budget on one
        directory name -- with the store's own files nested below it and the
        project root above it. `stable_identifier` already truncates for the same
        reason. 32 hex characters is 128 bits, so a collision between two
        generations is not a practical concern.

        `key` itself is deliberately left at full length: it is written into
        store metadata and compared on reopen (see VersionedEmbeddingStore), so
        shortening it would invalidate every existing store's identity rather
        than merely relocating it.
        """
        root_path = Path(root)
        short = root_path / self.kind / self.key[:_PATH_SEGMENT_CHARS]
        if not short.exists():
            # Adopt a generation written before the segment was shortened rather
            # than silently rebuilding it under the new name.
            legacy = root_path / self.kind / self.key
            if legacy.exists():
                return legacy
        return short


class VersionedEmbeddingStore:
    """A float32 row matrix whose metadata advances only after fsync.

    The metadata row count is authoritative. Extra bytes from a process death
    between data fsync and metadata replacement are truncated when reopened.
    """

    METADATA_NAME = "metadata.json"
    MATRIX_NAME = "embeddings.f32"

    def __init__(
        self,
        directory: str | Path,
        *,
        dimension: int,
        generation: ArtifactGeneration,
        create: bool = True,
    ):
        if dimension <= 0:
            raise ValueError("embedding dimension must be positive")
        self.directory = Path(directory)
        self.dimension = int(dimension)
        self.generation = generation
        self.metadata_path = self.directory / self.METADATA_NAME
        self.matrix_path = self.directory / self.MATRIX_NAME
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        if self.metadata_path.exists():
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if metadata["dimension"] != self.dimension:
                raise ValueError("embedding dimension does not match existing store")
            if metadata["generation_key"] != generation.key:
                raise ValueError("artifact generation does not match existing store")
            self._row_count = int(metadata["row_count"])
            self._blocks = list(metadata.get("blocks", []))
            self._matrix_sha256 = metadata.get("matrix_sha256")
            self._repair_uncommitted_tail()
        else:
            if not create:
                raise FileNotFoundError(self.metadata_path)
            self._row_count = 0
            self._blocks: list[dict[str, Any]] = []
            self._matrix_sha256: str | None = hashlib.sha256(b"").hexdigest()
            self.matrix_path.touch()
            self._write_metadata()

    @property
    def row_count(self) -> int:
        return self._row_count

    @property
    def row_size_bytes(self) -> int:
        return self.dimension * 4

    def _metadata(self) -> dict[str, Any]:
        return {
            "schema_version": self.generation.schema_version,
            "kind": self.generation.kind,
            "generation_key": self.generation.key,
            "generation_inputs": self.generation.inputs,
            "dimension": self.dimension,
            "dtype": "float32",
            "row_count": self._row_count,
            "blocks": self._blocks,
            "content_root_sha256": content_key(self._blocks),
            "matrix_sha256": self._matrix_sha256,
        }

    def _write_metadata(self) -> None:
        _atomic_json(self.metadata_path, self._metadata())

    def _repair_uncommitted_tail(self) -> None:
        expected_size = self._row_count * self.row_size_bytes
        actual_size = self.matrix_path.stat().st_size if self.matrix_path.exists() else 0
        if actual_size < expected_size:
            raise IOError(
                f"embedding matrix is truncated: expected {expected_size}, got {actual_size}"
            )
        if actual_size > expected_size:
            with self.matrix_path.open("r+b") as handle:
                handle.truncate(expected_size)
                handle.flush()
                os.fsync(handle.fileno())

    def matrix_sha256(self) -> str:
        digest = hashlib.sha256()
        if self.matrix_path.exists():
            with self.matrix_path.open("rb") as handle:
                while chunk := handle.read(8 * 1024 * 1024):
                    digest.update(chunk)
        return digest.hexdigest()

    def finalize(self) -> str:
        """Compute and record the full byte hash once at an artifact boundary."""
        self._matrix_sha256 = self.matrix_sha256()
        self._write_metadata()
        return self._matrix_sha256

    def append(self, rows: Sequence[Sequence[float]] | Any) -> range:
        try:
            import numpy as np  # type: ignore
        except ImportError:
            np = None
        if np is not None:
            array = np.asarray(rows, dtype="<f4")
            if array.ndim == 1:
                array = array.reshape(1, -1)
            if array.ndim != 2 or array.shape[1] != self.dimension:
                raise ValueError(f"rows must have shape (n, {self.dimension})")
            payload = array.tobytes(order="C")
            count = int(array.shape[0])
        else:
            materialized = [list(row) for row in rows]
            if any(len(row) != self.dimension for row in materialized):
                raise ValueError(f"rows must have dimension {self.dimension}")
            payload = b"".join(
                struct.pack(f"<{self.dimension}f", *map(float, row))
                for row in materialized
            )
            count = len(materialized)
        start = self._row_count
        if count == 0:
            return range(start, start)
        with self.matrix_path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._row_count += count
        self._blocks.append(
            {"rows": count, "sha256": hashlib.sha256(payload).hexdigest()}
        )
        self._matrix_sha256 = None
        self._write_metadata()
        return range(start, self._row_count)

    def truncate(self, row_count: int) -> None:
        if not 0 <= row_count <= self._row_count:
            raise ValueError("truncate row must be within committed rows")
        with self.matrix_path.open("r+b") as handle:
            handle.truncate(row_count * self.row_size_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        self._row_count = row_count
        retained: list[dict[str, Any]] = []
        remaining = row_count
        for block in self._blocks:
            block_rows = int(block["rows"])
            if remaining >= block_rows:
                retained.append(block)
                remaining -= block_rows
            elif remaining > 0:
                with self.matrix_path.open("rb") as handle:
                    offset = sum(int(item["rows"]) for item in retained) * self.row_size_bytes
                    handle.seek(offset)
                    payload = handle.read(remaining * self.row_size_bytes)
                retained.append(
                    {"rows": remaining, "sha256": hashlib.sha256(payload).hexdigest()}
                )
                remaining = 0
                break
            else:
                break
        self._blocks = retained
        self._matrix_sha256 = self.matrix_sha256()
        self._write_metadata()

    def read_rows(self, rows: Iterable[int] | None = None) -> list[list[float]]:
        selected = list(range(self._row_count)) if rows is None else list(rows)
        output: list[list[float]] = []
        with self.matrix_path.open("rb") as handle:
            for row in selected:
                if not 0 <= row < self._row_count:
                    raise IndexError(row)
                handle.seek(row * self.row_size_bytes)
                values = struct.unpack(
                    f"<{self.dimension}f", handle.read(self.row_size_bytes)
                )
                output.append(list(values))
        return output


class ResumableBlockState:
    """Atomic state for bounded archive jobs."""

    def __init__(
        self,
        path: str | Path,
        *,
        generation_key: str,
    ):
        self.path = Path(path)
        self.generation_key = generation_key
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload["generation_key"] != generation_key:
                raise ValueError("checkpoint generation mismatch")
            self.completed_blocks = set(payload.get("completed_blocks", []))
        else:
            self.completed_blocks: set[str] = set()

    def is_complete(self, block_id: str) -> bool:
        return block_id in self.completed_blocks

    def complete(self, block_id: str) -> None:
        self.completed_blocks.add(block_id)
        _atomic_json(
            self.path,
            {
                "schema_version": "1.0",
                "generation_key": self.generation_key,
                "completed_blocks": sorted(self.completed_blocks),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

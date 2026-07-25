"""PTS-safe video ingestion primitives for RAZIEL."""

from .hash_source import sha256_file
from .probe import FFProbeError, probe_video
from .sampler import (
    CoverageReport,
    ExpectedTickLedger,
    SamplingTick,
    build_ingest_cache_key,
)

__all__ = [
    "CoverageReport",
    "ExpectedTickLedger",
    "FFProbeError",
    "SamplingTick",
    "build_ingest_cache_key",
    "probe_video",
    "sha256_file",
]

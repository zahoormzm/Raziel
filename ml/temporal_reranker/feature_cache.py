"""Content-addressed, immutable cache for frozen reranker feature records."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from ml.temporal_reranker.dataset import (
    CandidateFeatureRecord,
    FeatureCacheIdentity,
)


class FeatureCacheStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, identity: FeatureCacheIdentity) -> Path:
        key = identity.key
        return self.root / key[:2] / f"{key}.json"

    def put(
        self,
        identity: FeatureCacheIdentity,
        record: CandidateFeatureRecord,
    ) -> Path:
        errors = record.validate()
        if errors:
            raise ValueError("invalid feature record: " + "; ".join(errors))
        if record.source_hash != identity.source_hash:
            raise ValueError("feature record source hash does not match cache identity")
        if record.candidate_bounds != identity.candidate_bounds:
            raise ValueError("feature record bounds do not match cache identity")
        destination = self.path_for(identity)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_key": identity.key,
            "identity": _identity_mapping(identity),
            "record": _record_mapping(record),
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if destination.exists():
            if destination.read_bytes() != encoded:
                raise RuntimeError("content-addressed feature cache collision")
            return destination
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{identity.key}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return destination

    def get(self, identity: FeatureCacheIdentity) -> CandidateFeatureRecord | None:
        path = self.path_for(identity)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if raw.get("cache_key") != identity.key:
            raise ValueError("feature cache key does not match its path")
        if raw.get("identity") != _identity_mapping(identity):
            raise ValueError("feature cache identity metadata does not match the request")
        return CandidateFeatureRecord.from_mapping(raw["record"])


def _identity_mapping(identity: FeatureCacheIdentity) -> Mapping[str, Any]:
    return {
        "source_hash": identity.source_hash,
        "candidate_bounds": list(identity.candidate_bounds),
        "query_plan_hash": identity.query_plan_hash,
        "frame_ids_and_pts": [list(item) for item in identity.frame_ids_and_pts],
        "preprocessing_version": identity.preprocessing_version,
        "feature_schema_version": identity.feature_schema_version,
        "encoder_revision_hashes": dict(identity.encoder_revision_hashes),
        "graph_schema_version": identity.graph_schema_version,
    }


def _record_mapping(record: CandidateFeatureRecord) -> Mapping[str, Any]:
    return {
        "candidate_id": record.candidate_id,
        "episode_id": record.episode_id,
        "query_id": record.query_id,
        "scenario_id": record.scenario_id,
        "session_id": record.session_id,
        "split": record.split,
        "source_hash": record.source_hash,
        "candidate_bounds": list(record.candidate_bounds),
        "frame_ids": list(record.frame_ids),
        "pts": list(record.pts),
        "sequence_features": {
            name: list(values) for name, values in record.sequence_features.items()
        },
        "atom_features": {
            name: list(values) for name, values in record.atom_features.items()
        },
        "relation_features": {
            name: list(values) for name, values in record.relation_features.items()
        },
        "challenger_types": sorted(record.challenger_types),
        "is_absent_example": record.is_absent_example,
        "labels": {
            "relevance": record.labels.relevance,
            "atom_support": dict(record.labels.atom_support),
            "relation_support": dict(record.labels.relation_support),
            "start_index": record.labels.start_index,
            "end_index": record.labels.end_index,
            "frame_relevance": list(record.labels.frame_relevance),
            "evidence_complete": record.labels.evidence_complete,
        },
    }

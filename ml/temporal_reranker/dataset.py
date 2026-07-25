"""Feature records and the non-negotiable Temporal Evidence Reranker data gate.

The large visual encoders are deliberately outside this module.  Records contain
only frozen, precomputed sequences plus labels and provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


CORE_SEQUENCE_FEATURES = frozenset(
    {
        "query_similarity",
        "relative_time",
        "window_scale",
        "motion",
        "luminance",
        "sharpness",
        "missing_frame",
    }
)
CORE_CHALLENGERS = frozenset(
    {
        "wrong_attribute",
        "wrong_binding",
        "wrong_order",
        "partial_event",
        "unobservable",
    }
)
RELATION_LOGIC_CHALLENGERS = frozenset(
    {
        "track_fragmentation",
        "bounded_or",
        "visible_absence",
        "bounded_count",
    }
)
VALID_SPLITS = frozenset({"train", "dev", "test"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RerankerLabels:
    relevance: int
    atom_support: Mapping[str, int | None]
    relation_support: Mapping[str, int | None] = field(default_factory=dict)
    start_index: int | None = None
    end_index: int | None = None
    frame_relevance: tuple[int | None, ...] = ()
    evidence_complete: int | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RerankerLabels":
        return cls(
            relevance=int(raw["relevance"]),
            atom_support={str(k): _optional_binary(v) for k, v in raw.get("atom_support", {}).items()},
            relation_support={
                str(k): _optional_binary(v) for k, v in raw.get("relation_support", {}).items()
            },
            start_index=_optional_int(raw.get("start_index")),
            end_index=_optional_int(raw.get("end_index")),
            frame_relevance=tuple(_optional_binary(v) for v in raw.get("frame_relevance", [])),
            evidence_complete=_optional_binary(raw.get("evidence_complete")),
        )

    def validate(self, sequence_length: int) -> list[str]:
        errors: list[str] = []
        if self.relevance not in (0, 1):
            errors.append("labels.relevance must be 0 or 1")
        if self.start_index is not None and not 0 <= self.start_index < sequence_length:
            errors.append("labels.start_index is outside the feature sequence")
        if self.end_index is not None and not 0 <= self.end_index < sequence_length:
            errors.append("labels.end_index is outside the feature sequence")
        if (
            self.start_index is not None
            and self.end_index is not None
            and self.start_index > self.end_index
        ):
            errors.append("labels.start_index must not exceed labels.end_index")
        if self.frame_relevance and len(self.frame_relevance) != sequence_length:
            errors.append("labels.frame_relevance length must equal the feature sequence length")
        return errors


@dataclass(frozen=True)
class CandidateFeatureRecord:
    candidate_id: str
    episode_id: str
    query_id: str
    scenario_id: str
    session_id: str
    split: str
    source_hash: str
    candidate_bounds: tuple[float, float]
    frame_ids: tuple[int, ...]
    pts: tuple[float, ...]
    sequence_features: Mapping[str, tuple[float, ...]]
    atom_features: Mapping[str, tuple[float, ...]]
    relation_features: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    challenger_types: frozenset[str] = frozenset()
    is_absent_example: bool = False
    labels: RerankerLabels = field(
        default_factory=lambda: RerankerLabels(relevance=0, atom_support={})
    )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CandidateFeatureRecord":
        bounds = raw["candidate_bounds"]
        record = cls(
            candidate_id=str(raw["candidate_id"]),
            episode_id=str(raw["episode_id"]),
            query_id=str(raw["query_id"]),
            scenario_id=str(raw["scenario_id"]),
            session_id=str(raw["session_id"]),
            split=str(raw["split"]),
            source_hash=str(raw["source_hash"]),
            candidate_bounds=(float(bounds[0]), float(bounds[1])),
            frame_ids=tuple(int(v) for v in raw["frame_ids"]),
            pts=tuple(float(v) for v in raw["pts"]),
            sequence_features={
                str(name): tuple(float(v) for v in values)
                for name, values in raw["sequence_features"].items()
            },
            atom_features={
                str(name): tuple(float(v) for v in values)
                for name, values in raw.get("atom_features", {}).items()
            },
            relation_features={
                str(name): tuple(float(v) for v in values)
                for name, values in raw.get("relation_features", {}).items()
            },
            challenger_types=frozenset(str(v) for v in raw.get("challenger_types", [])),
            is_absent_example=bool(raw.get("is_absent_example", False)),
            labels=RerankerLabels.from_mapping(raw["labels"]),
        )
        errors = record.validate()
        if errors:
            raise ValueError(f"invalid candidate feature record {record.candidate_id}: {'; '.join(errors)}")
        return record

    @property
    def length(self) -> int:
        return len(self.pts)

    @property
    def group_key(self) -> tuple[str, str]:
        return (self.scenario_id, self.session_id)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.candidate_id or not self.episode_id or not self.query_id:
            errors.append("candidate_id, episode_id, and query_id are required")
        if self.split not in VALID_SPLITS:
            errors.append(f"split must be one of {sorted(VALID_SPLITS)}")
        if not self.source_hash:
            errors.append("source_hash is required")
        if self.candidate_bounds[0] > self.candidate_bounds[1]:
            errors.append("candidate bounds are reversed")
        if not self.pts:
            errors.append("at least one PTS sample is required")
        if len(self.frame_ids) != len(self.pts):
            errors.append("frame_ids and pts lengths differ")
        if len(set(self.frame_ids)) != len(self.frame_ids):
            errors.append("frame_ids must be unique")
        if tuple(sorted(self.pts)) != self.pts:
            errors.append("pts must be nondecreasing")
        if any(not math.isfinite(value) for value in self.pts):
            errors.append("pts values must be finite")
        missing_core = CORE_SEQUENCE_FEATURES.difference(self.sequence_features)
        if missing_core:
            errors.append(f"missing core sequence features: {sorted(missing_core)}")
        for name, values in (
            list(self.sequence_features.items())
            + list(self.atom_features.items())
            + list(self.relation_features.items())
        ):
            if len(values) != len(self.pts):
                errors.append(f"feature {name!r} length does not match pts")
            if any(not math.isfinite(value) for value in values):
                errors.append(f"feature {name!r} contains a non-finite value")
        if set(self.labels.atom_support).difference(self.atom_features):
            errors.append("atom-support labels reference absent atom feature sequences")
        if set(self.labels.relation_support).difference(self.relation_features):
            errors.append("relation-support labels reference absent relation feature sequences")
        errors.extend(self.labels.validate(len(self.pts)))
        return errors

    def ordered_feature_matrix(self, feature_order: Sequence[str]) -> list[list[float]]:
        missing = set(feature_order).difference(self.sequence_features)
        if missing:
            raise ValueError(f"feature order references missing features: {sorted(missing)}")
        return [
            [self.sequence_features[name][tick] for name in feature_order]
            for tick in range(self.length)
        ]


@dataclass(frozen=True)
class DatasetManifest:
    schema_version: str
    dataset_manifest_hash: str
    split_hash: str
    feature_cache_version: str
    model_revision_hashes: Mapping[str, str]
    independent_episode_count: int
    confusion_table_ready: bool

    @classmethod
    def load(cls, path: str | Path) -> "DatasetManifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            schema_version=str(raw["schema_version"]),
            dataset_manifest_hash=str(raw["dataset_manifest_hash"]),
            split_hash=str(raw["split_hash"]),
            feature_cache_version=str(raw["feature_cache_version"]),
            model_revision_hashes={
                str(k): str(v) for k, v in raw.get("model_revision_hashes", {}).items()
            },
            independent_episode_count=int(raw["independent_episode_count"]),
            confusion_table_ready=bool(raw.get("confusion_table_ready", False)),
        )


@dataclass(frozen=True)
class TrainingDataGateReport:
    passed: bool
    blockers: tuple[str, ...]
    episode_count: int
    split_group_counts: Mapping[str, int]
    challenger_counts: Mapping[str, int]
    relation_logic_head_allowed: bool

    def require_passed(self) -> None:
        if not self.passed:
            raise RuntimeError("training data gate failed: " + "; ".join(self.blockers))


def load_jsonl(path: str | Path) -> list[CandidateFeatureRecord]:
    records: list[CandidateFeatureRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(CandidateFeatureRecord.from_mapping(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return records


def evaluate_training_data_gate(
    records: Sequence[CandidateFeatureRecord],
    manifest: DatasetManifest,
    *,
    request_relation_logic_head: bool,
) -> TrainingDataGateReport:
    blockers: list[str] = []
    unique_episodes = {
        (record.scenario_id, record.session_id, record.episode_id) for record in records
    }
    if len(unique_episodes) < 150:
        blockers.append("fewer than 150 independent labeled candidate episodes")
    if manifest.independent_episode_count != len(unique_episodes):
        blockers.append("manifest independent_episode_count does not match loaded records")
    if (
        not manifest.schema_version
        or not manifest.dataset_manifest_hash
        or not manifest.split_hash
        or not manifest.feature_cache_version
        or not manifest.model_revision_hashes
    ):
        blockers.append("dataset manifest provenance/version fields are incomplete")
    if not any(record.labels.relevance == 1 for record in records):
        blockers.append("no positive examples")
    if not any(record.is_absent_example for record in records):
        blockers.append("no absent examples")

    challenger_counts = {
        challenger: sum(challenger in record.challenger_types for record in records)
        for challenger in sorted(CORE_CHALLENGERS | RELATION_LOGIC_CHALLENGERS)
    }
    for challenger in sorted(CORE_CHALLENGERS):
        if challenger_counts[challenger] == 0:
            blockers.append(f"missing required challenger family: {challenger}")

    group_to_splits: dict[tuple[str, str], set[str]] = {}
    for record in records:
        group_to_splits.setdefault(record.group_key, set()).add(record.split)
    leaked = [group for group, splits in group_to_splits.items() if len(splits) > 1]
    if leaked:
        blockers.append("scenario/session groups leak across train/dev/test splits")
    split_group_counts = {
        split: len({record.group_key for record in records if record.split == split})
        for split in sorted(VALID_SPLITS)
    }
    for split, count in split_group_counts.items():
        if count == 0:
            blockers.append(f"split {split!r} has no scenario/session group")

    if not manifest.confusion_table_ready:
        blockers.append(
            "manifest does not certify enough examples per major challenger family for a confusion table"
        )

    relation_logic_ready = all(
        challenger_counts[name] > 0 for name in RELATION_LOGIC_CHALLENGERS
    )
    if request_relation_logic_head and not relation_logic_ready:
        missing = [
            name for name in sorted(RELATION_LOGIC_CHALLENGERS) if challenger_counts[name] == 0
        ]
        blockers.append(
            "relation/logic head requested without labeled challengers: " + ", ".join(missing)
        )

    return TrainingDataGateReport(
        passed=not blockers,
        blockers=tuple(blockers),
        episode_count=len(unique_episodes),
        split_group_counts=split_group_counts,
        challenger_counts=challenger_counts,
        relation_logic_head_allowed=relation_logic_ready,
    )


@dataclass(frozen=True)
class FeatureCacheIdentity:
    source_hash: str
    candidate_bounds: tuple[float, float]
    query_plan_hash: str
    frame_ids_and_pts: tuple[tuple[int, float], ...]
    preprocessing_version: str
    feature_schema_version: str
    encoder_revision_hashes: Mapping[str, str]
    graph_schema_version: str | None

    @property
    def key(self) -> str:
        return content_hash(
            {
                "source_hash": self.source_hash,
                "candidate_bounds": self.candidate_bounds,
                "query_plan_hash": self.query_plan_hash,
                "frame_ids_and_pts": self.frame_ids_and_pts,
                "preprocessing_version": self.preprocessing_version,
                "feature_schema_version": self.feature_schema_version,
                "encoder_revision_hashes": dict(self.encoder_revision_hashes),
                "graph_schema_version": self.graph_schema_version,
            }
        )


def _optional_binary(value: Any) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed not in (0, 1):
        raise ValueError("binary label must be 0, 1, or null")
    return parsed


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def records_hash(records: Iterable[CandidateFeatureRecord]) -> str:
    serializable = [
        {
            "candidate_id": record.candidate_id,
            "episode_id": record.episode_id,
            "query_id": record.query_id,
            "group": record.group_key,
            "split": record.split,
            "source_hash": record.source_hash,
            "frame_ids": record.frame_ids,
            "pts": record.pts,
        }
        for record in records
    ]
    return content_hash(serializable)
